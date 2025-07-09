import asyncio, os, re, json, time, logging
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from dotenv import load_dotenv
from collections import defaultdict

CONFIG_FILE = "config.json"
load_dotenv()
api_id = int(os.getenv("API_ID"))
api_hash = os.getenv("API_HASH")
initial_sources = [ch.strip().lower() for ch in os.getenv("SOURCE_CHANNELS", "").split(",") if ch.strip()]
initial_dest = os.getenv("DEST_CHANNEL")
default_admin = int(os.getenv("ADMIN_ID", "1121727322"))

logging.basicConfig(level=logging.WARNING, format='[%(asctime)s] %(levelname)s: %(message)s')
if not all([api_id, api_hash, initial_sources, initial_dest]):
    exit("Missing .env values")
if not os.path.exists('sessions'): os.makedirs('sessions')
client = TelegramClient('sessions/forwarder_session', api_id, api_hash)
forwarding_enabled = True

def load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                d = json.load(f)
                return [str(x).lower() for x in d.get("source_channels", [])], d.get("destination_channel"), set(int(x) for x in d.get("admin_ids", [default_admin]))
        except: pass
    return initial_sources, initial_dest, set([default_admin])

def save_config(src, dst, admins):
    with open(CONFIG_FILE, "w") as f:
        json.dump({"source_channels": [str(x).lower() for x in src], "destination_channel": dst, "admin_ids": list(admins)}, f)

source_channels, destination_channel, admin_ids = load_config()

def remove_mentions(text):
    if not text: return text
    text = re.sub(r'@\w+', '', text)
    text = re.sub(r'(?i)^.*(credit|via):.*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'https?://\S+|t\.me/\S+|telegram\.me/\S+', '', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

album_buffer, album_last_seen = defaultdict(list), {}

@client.on(events.NewMessage)
async def forward_message(event):
    global forwarding_enabled
    chat = await event.get_chat()
    uname, cid = getattr(chat, "username", None), str(getattr(chat, "id", None))
    print(f"[DEBUG] New message: uname={uname}, cid={cid}, chat_type={type(chat)}")
    if not ((uname and uname.lower() in source_channels) or (cid in source_channels)): return
    if not forwarding_enabled: return
    try:
        message = event.message
        source_name = getattr(chat, 'title', None) or uname or cid
        tag = f"Source: {source_name}"
        if message.grouped_id:
            group_id = (event.chat_id, message.grouped_id)
            album_buffer[group_id].append((event, tag))
            album_last_seen[group_id] = time.time()
            asyncio.create_task(debounce_album_send(group_id))
        elif message.media:
            clean_caption = remove_mentions(message.text) if message.text else ""
            await client.send_file(destination_channel, file=event.message, caption=f"{clean_caption}\n\n{tag}".strip())
        elif message.text:
            clean_text = remove_mentions(message.text)
            await client.send_message(destination_channel, f"{clean_text}\n\n{tag}".strip())
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
    if not events_group: return
    events_group.sort(key=lambda x: x[0].message.id)
    files = [e.message for e, _ in events_group]
    tag = events_group[0][1]
    clean_caption = remove_mentions(events_group[0][0].message.text) if events_group[0][0].message.text else ""
    await client.send_file(destination_channel, file=files, caption=f"{clean_caption}\n\n{tag}".strip())

@client.on(events.NewMessage(pattern=r'^/'))
async def admin_commands(event):
    global forwarding_enabled, source_channels, destination_channel, admin_ids
    if event.sender_id not in admin_ids: return
    cmd = event.raw_text.strip()
    # --- Add admin by id or reply
    if cmd.startswith("/addadmin"):
        if event.reply_to_msg_id:
            reply_msg = await event.get_reply_message()
            if reply_msg:
                admin_ids.add(reply_msg.sender_id)
                save_config(source_channels, destination_channel, admin_ids)
                await event.reply(f"✅ Added admin by reply: `{reply_msg.sender_id}`")
        elif len(cmd.split()) == 2:
            try:
                new_admin = int(cmd.split()[1])
                admin_ids.add(new_admin)
                save_config(source_channels, destination_channel, admin_ids)
                await event.reply(f"✅ Added admin: `{new_admin}`")
            except: await event.reply("❌ Usage: /addadmin <user_id>")
        else: await event.reply("❌ Usage: /addadmin <user_id> or reply")
        return
    # --- Remove admin by id or reply
    if cmd.startswith("/removeadmin"):
        if event.reply_to_msg_id:
            reply_msg = await event.get_reply_message()
            remove_admin = reply_msg.sender_id
            if remove_admin in admin_ids and len(admin_ids) > 1:
                admin_ids.remove(remove_admin)
                save_config(source_channels, destination_channel, admin_ids)
                await event.reply(f"✅ Removed admin by reply: `{remove_admin}`")
            else: await event.reply("❌ At least one admin must remain.")
        elif len(cmd.split()) == 2:
            try:
                remove_admin = int(cmd.split()[1])
                if remove_admin in admin_ids and len(admin_ids) > 1:
                    admin_ids.remove(remove_admin)
                    save_config(source_channels, destination_channel, admin_ids)
                    await event.reply(f"✅ Removed admin: `{remove_admin}`")
                else: await event.reply("❌ At least one admin must remain.")
            except: await event.reply("❌ Usage: /removeadmin <user_id>")
        else: await event.reply("❌ Usage: /removeadmin <user_id> or reply")
        return
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
    if cmd == "/help":
        await event.reply("Admin:\n/start\n/stop\n/status\n/showconfig\n/addsource <ch>\n/removesource <ch>\n/setdest <ch>\n/addadmin <id> or reply\n/removeadmin <id> or reply\n/backup\n/restore")
    elif cmd == "/start":
        forwarding_enabled = True
        await event.reply("✅ Forwarding enabled!")
    elif cmd == "/stop":
        forwarding_enabled = False
        await event.reply("⛔ Forwarding paused.")
    elif cmd == "/status":
        await event.reply(f"Bot forwarding is currently {'enabled ✅' if forwarding_enabled else 'paused ⛔'}.")
    elif cmd == "/showconfig":
        await event.reply(f"Sources: {source_channels}\nDestination: {destination_channel}\nAdmins: {list(admin_ids)}")
    elif cmd.startswith("/addsource "):
        ch = cmd.split(maxsplit=1)[1].strip().lower()
        if ch not in source_channels:
            source_channels.append(ch)
            save_config(source_channels, destination_channel, admin_ids)
            await event.reply(f"✅ Added source channel: {ch}")
        else: await event.reply(f"Channel {ch} already in list.")
    elif cmd.startswith("/removesource "):
        ch = cmd.split(maxsplit=1)[1].strip().lower()
        if ch in source_channels:
            source_channels.remove(ch)
            save_config(source_channels, destination_channel, admin_ids)
            await event.reply(f"✅ Removed source channel: {ch}")
        else: await event.reply(f"Channel {ch} not found.")
    elif cmd.startswith("/setdest "):
        ch = cmd.split(maxsplit=1)[1].strip()
        destination_channel = ch
        save_config(source_channels, destination_channel, admin_ids)
        await event.reply(f"✅ Destination set to: {ch}")
    else: await event.reply("❓ Unknown command. Type /help.")

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
