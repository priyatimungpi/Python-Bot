import asyncio
import os
import re
import json
import time
import logging
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from dotenv import load_dotenv
from collections import defaultdict

CONFIG_FILE = "config.json"

# Load .env
load_dotenv()
api_id = int(os.getenv("API_ID"))
api_hash = os.getenv("API_HASH")
initial_sources = [ch.strip() for ch in os.getenv("SOURCE_CHANNELS", "").split(",") if ch.strip()]
initial_dest = os.getenv("DEST_CHANNEL")
default_admin = int(os.getenv("ADMIN_ID", "1121727322"))

logging.basicConfig(level=logging.WARNING, format='[%(asctime)s] %(levelname)s: %(message)s')

if not all([api_id, api_hash, initial_dest]):
    logging.error("Missing one or more required environment variables.")
    exit(1)

if not os.path.exists('sessions'):
    os.makedirs('sessions')

client = TelegramClient('sessions/forwarder_session', api_id, api_hash)
forwarding_enabled = True

# --- Persistent config with multi-admin and (username, id, title) sources ---
def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
                sources = [dict(x) for x in data.get("source_channels", [])]
                return (
                    sources,
                    data.get("destination_channel"),
                    set(int(x) for x in data.get("admin_ids", [default_admin]))
                )
        except Exception as e:
            logging.error(f"Failed to load config: {e}")
    # fallback if first run
    return [], initial_dest, set([default_admin])

def save_config(source_channels, destination_channel, admin_ids):
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump({
                "source_channels": [dict(x) for x in source_channels],
                "destination_channel": destination_channel,
                "admin_ids": list(admin_ids)
            }, f, indent=2)
    except Exception as e:
        logging.error(f"Failed to save config: {e}")

source_channels, destination_channel, admin_ids = load_config()

def remove_mentions(text):
    if not text:
        return text
    text = re.sub(r'@\w+', '', text)
    text = re.sub(r'(?i)^.*(credit|via):.*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'https?://\S+|t\.me/\S+|telegram\.me/\S+', '', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

def is_channel_allowed(cid):
    return any(str(cid) == str(sc['id']) for sc in source_channels)

def get_username_for_id(cid):
    for sc in source_channels:
        if str(cid) == str(sc['id']):
            return sc.get('username')
    return None

# --- Robust album (grouped media) debouncing ---
album_buffer = defaultdict(list)
album_last_seen = {}

@client.on(events.NewMessage)
async def forward_message(event):
    global forwarding_enabled
    chat = await event.get_chat()
    uname = getattr(chat, "username", None)
    cid = str(getattr(chat, "id", None))
    print(f"[ALL_MSGS] username={uname}, id={cid}, text={event.message.text[:40] if event.message.text else None}")
    if not is_channel_allowed(cid):
        print(f"[SKIP] Message from {uname or cid} not in source_channels, skipping.")
        return
    if not forwarding_enabled:
        print("[SKIP] Forwarding paused.")
        return
    try:
        message = event.message
        source_name = getattr(chat, 'title', None) or uname or cid
        tag = f"Source: {source_name}"
        # Robust grouped media/album debouncing
        if message.grouped_id:
            group_id = (event.chat_id, message.grouped_id)
            album_buffer[group_id].append((event, tag))
            album_last_seen[group_id] = time.time()
            asyncio.create_task(debounce_album_send(group_id))
        elif message.media:
            clean_caption = remove_mentions(message.text) if message.text else ""
            caption_with_source = f"{clean_caption}\n\n{tag}".strip()
            await client.send_file(destination_channel, file=event.message, caption=caption_with_source)
        elif message.text:
            clean_text = remove_mentions(message.text)
            text_with_source = f"{clean_text}\n\n{tag}".strip()
            await client.send_message(destination_channel, text_with_source)
    except FloodWaitError as e:
        await asyncio.sleep(e.seconds)
        await forward_message(event)
    except Exception as e:
        logging.error(f"Error forwarding message: {e}")

async def debounce_album_send(group_id, debounce_sec=1.5):
    await asyncio.sleep(debounce_sec)
    last = album_last_seen.get(group_id)
    if last and time.time() - last >= debounce_sec:
        await process_album(group_id)
        album_last_seen.pop(group_id, None)

async def process_album(group_id):
    events_group = album_buffer.pop(group_id, [])
    if not events_group:
        return
    events_group.sort(key=lambda x: x[0].message.id)
    files = [e.message for e, _ in events_group]
    tag = events_group[0][1]
    clean_caption = remove_mentions(events_group[0][0].message.text) if events_group[0][0].message.text else ""
    caption_with_source = f"{clean_caption}\n\n{tag}".strip()
    try:
        await client.send_file(destination_channel, file=files, caption=caption_with_source)
    except Exception as e:
        logging.error(f"Error forwarding album: {e}")

# --- Admin commands, only handle '/' and from admin! ---
@client.on(events.NewMessage(pattern=r'^/'))
async def admin_commands(event):
    global forwarding_enabled, source_channels, destination_channel, admin_ids
    sender = event.sender_id
    cmd = event.raw_text.strip()

    if sender not in admin_ids:
        return

    # --- Add admin by user ID or reply ---
    if cmd.startswith("/addadmin"):
        if event.reply_to_msg_id:
            reply_msg = await event.get_reply_message()
            if reply_msg:
                new_admin = reply_msg.sender_id
                if new_admin in admin_ids:
                    await event.reply(f"User ID {new_admin} is already an admin.")
                else:
                    admin_ids.add(new_admin)
                    save_config(source_channels, destination_channel, admin_ids)
                    await event.reply(f"✅ Added admin by reply: `{new_admin}` (Saved to config.json)")
        else:
            parts = cmd.split()
            if len(parts) == 2:
                try:
                    new_admin = int(parts[1])
                    if new_admin in admin_ids:
                        await event.reply(f"User ID {new_admin} is already an admin.")
                    else:
                        admin_ids.add(new_admin)
                        save_config(source_channels, destination_channel, admin_ids)
                        await event.reply(f"✅ Added admin: `{new_admin}` (Saved to config.json)")
                except Exception:
                    await event.reply("❌ Usage: /addadmin <user_id>")
            else:
                await event.reply("❌ Usage: /addadmin <user_id> or reply to user")
        return

    # --- Remove admin by user ID or reply ---
    if cmd.startswith("/removeadmin"):
        if event.reply_to_msg_id:
            reply_msg = await event.get_reply_message()
            if reply_msg:
                remove_admin = reply_msg.sender_id
                if remove_admin not in admin_ids:
                    await event.reply(f"User ID {remove_admin} is not an admin.")
                elif len(admin_ids) == 1:
                    await event.reply("❌ At least one admin must remain.")
                else:
                    admin_ids.remove(remove_admin)
                    save_config(source_channels, destination_channel, admin_ids)
                    await event.reply(f"✅ Removed admin by reply: `{remove_admin}` (Saved to config.json)")
        else:
            parts = cmd.split()
            if len(parts) == 2:
                try:
                    remove_admin = int(parts[1])
                    if remove_admin not in admin_ids:
                        await event.reply(f"User ID {remove_admin} is not an admin.")
                    elif len(admin_ids) == 1:
                        await event.reply("❌ At least one admin must remain.")
                    else:
                        admin_ids.remove(remove_admin)
                        save_config(source_channels, destination_channel, admin_ids)
                        await event.reply(f"✅ Removed admin: `{remove_admin}` (Saved to config.json)")
                except Exception:
                    await event.reply("❌ Usage: /removeadmin <user_id>")
            else:
                await event.reply("❌ Usage: /removeadmin <user_id> or reply to user")
        return

    # --- Backup/Restore and all the rest ---
    if cmd == "/backup":
        if os.path.exists(CONFIG_FILE):
            await event.reply("Here is your config.json backup ⬇️")
            await client.send_file(event.chat_id, CONFIG_FILE)
        else:
            await event.reply("No config.json found to backup.")
        return

    if cmd == "/restore":
        if event.reply_to_msg_id:
            reply_msg = await event.get_reply_message()
            if reply_msg and reply_msg.file:
                await reply_msg.download_media(CONFIG_FILE)
                source_channels, destination_channel, admin_ids = load_config()
                await event.reply("✅ Config restored from uploaded file!")
            else:
                await event.reply("Please reply to a config.json file with /restore.")
        else:
            await event.reply("Reply to a config.json file with /restore.")
        return

    # ---- /addsource stores username + id + title ----
    if cmd.startswith("/addsource "):
        ch = cmd.split(maxsplit=1)[1].strip()
        try:
            if ch.lstrip("-").isdigit():
                resolved_id = ch
                resolved_username = None
                resolved_title = None
                try:
                    entity = await client.get_entity(int(resolved_id))
                    resolved_username = getattr(entity, "username", None)
                    resolved_title = getattr(entity, "title", None)
                except Exception:
                    pass
            else:
                entity = await client.get_entity(ch)
                resolved_id = str(entity.id)
                resolved_username = getattr(entity, "username", None)
                resolved_title = getattr(entity, "title", None)
            if any(sc['id'] == resolved_id for sc in source_channels):
                await event.reply(f"Channel {resolved_title or resolved_username or resolved_id} already in the source list.")
            else:
                source_channels.append({'id': resolved_id, 'username': resolved_username, 'title': resolved_title})
                save_config(source_channels, destination_channel, admin_ids)
                await event.reply(
                    f"✅ Added source: {resolved_title or resolved_username or resolved_id} "
                    f"(ID: {resolved_id}) (Saved to config.json!)"
                )
        except Exception as e:
            await event.reply(f"❌ Could not resolve {ch}: {e}")
        return

    if cmd == "/help":
        await event.reply(
            "Admin commands:\n"
            "/start - Enable forwarding\n"
            "/stop - Pause forwarding\n"
            "/status - Show if bot is forwarding\n"
            "/showconfig - Show current channels\n"
            "/addsource <channel username or id>\n"
            "/removesource <channel id>\n"
            "/setdest <channel>\n"
            "/addadmin <user_id> or reply to user\n"
            "/removeadmin <user_id> or reply to user\n"
            "/backup - Download config.json\n"
            "/restore (reply to file) - Restore config.json"
        )
    elif cmd == "/start":
        forwarding_enabled = True
        await event.reply("✅ Forwarding enabled!")
    elif cmd == "/stop":
        forwarding_enabled = False
        await event.reply("⛔ Forwarding paused. No messages will be forwarded.")
    elif cmd == "/status":
        status = "enabled ✅" if forwarding_enabled else "paused ⛔"
        await event.reply(f"Bot forwarding is currently *{status}*.")
    elif cmd == "/showconfig":
        pretty_sources = []
        for i, sc in enumerate(source_channels, 1):
            pretty_sources.append(
                f"{i}. {sc.get('title') or '[NO_TITLE]'} (id: {sc['id']}, username: {sc.get('username') or '[NO_USERNAME]'})"
            )
        await event.reply(
            "Sources:\n" + "\n".join(pretty_sources) +
            f"\nDestination: {destination_channel}\nAdmins: {list(admin_ids)}"
        )
    elif cmd.startswith("/removesource "):
        ch = cmd.split(maxsplit=1)[1].strip()
        # Remove by ID only (not username)
        before = len(source_channels)
        source_channels = [sc for sc in source_channels if sc['id'] != ch]
        if len(source_channels) < before:
            save_config(source_channels, destination_channel, admin_ids)
            await event.reply(f"✅ Removed source channel: {ch} (Saved to config.json!)")
        else:
            await event.reply(f"Channel ID {ch} not found in source list.")
    elif cmd.startswith("/setdest "):
        ch = cmd.split(maxsplit=1)[1].strip()
        destination_channel = ch
        save_config(source_channels, destination_channel, admin_ids)
        await event.reply(f"✅ Destination set to: {ch} (Saved to config.json!)")
    else:
        await event.reply("❓ Unknown command. Type /help.")

async def main():
    await client.start()
    await client.run_until_disconnected()

if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
