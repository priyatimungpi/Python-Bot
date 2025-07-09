import asyncio
import os
import re
import logging
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from dotenv import load_dotenv
from collections import defaultdict

# --------- ENV SETUP ----------
load_dotenv()
api_id = int(os.getenv("API_ID"))
api_hash = os.getenv("API_HASH")
source_channels = [ch.strip() for ch in os.getenv("SOURCE_CHANNELS", "").split(",") if ch.strip()]
destination_channel = os.getenv("DEST_CHANNEL")

ADMIN_ID = 1121727322  # <-- SET YOUR TELEGRAM USER ID HERE!

forwarding_enabled = True  # Controls if forwarding is paused/running

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')

if not all([api_id, api_hash, source_channels, destination_channel]):
    logging.error("Missing one or more required environment variables.")
    exit(1)

if not os.path.exists('sessions'):
    os.makedirs('sessions')

client = TelegramClient('sessions/forwarder_session', api_id, api_hash)

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

# --- For album/grouped media support
album_buffer = defaultdict(list)
album_tasks = {}

@client.on(events.NewMessage(chats=source_channels))
async def forward_message(event):
    if not forwarding_enabled:
        return  # Forwarding is paused by admin
    try:
        message = event.message
        chat = await event.get_chat()
        source_name = getattr(chat, 'title', None) or getattr(chat, 'username', None) or str(chat.id)
        tag = f"Source: {source_name}"

        # Albums/grouped media
        if message.grouped_id:
            group_id = (event.chat_id, message.grouped_id)
            album_buffer[group_id].append((event, tag))
            if group_id not in album_tasks:
                album_tasks[group_id] = asyncio.create_task(process_album(group_id))
        elif message.media:
            clean_caption = remove_mentions(message.text) if message.text else ""
            caption_with_source = f"{clean_caption}\n\n{tag}".strip()
            logging.info(f"Forwarding single media from {source_name} to @{destination_channel}")
            await client.send_file(destination_channel, file=event.message, caption=caption_with_source)
        elif message.text:
            clean_text = remove_mentions(message.text)
            text_with_source = f"{clean_text}\n\n{tag}".strip()
            logging.info(f"Forwarding text from {source_name} to @{destination_channel}")
            await client.send_message(destination_channel, text_with_source)
        else:
            logging.info(f"Unknown message type from {source_name} - skipping.")
    except FloodWaitError as e:
        logging.warning(f"Hit rate limit. Sleeping for {e.seconds} seconds.")
        await asyncio.sleep(e.seconds)
    except Exception as e:
        logging.error(f"Error forwarding message: {e}")

async def process_album(group_id):
    await asyncio.sleep(1.0)
    events_group = album_buffer.pop(group_id, [])
    album_tasks.pop(group_id, None)
    if not events_group:
        return
    events_group.sort(key=lambda x: x[0].message.id)
    files = [e.message for e, _ in events_group]
    tag = events_group[0][1]
    clean_caption = remove_mentions(events_group[0][0].message.text) if events_group[0][0].message.text else ""
    caption_with_source = f"{clean_caption}\n\n{tag}".strip()
    logging.info(f"Forwarding album (grouped_id={group_id[1]}) with {len(files)} media from chat {group_id[0]} to @{destination_channel}")
    try:
        await client.send_file(destination_channel, file=files, caption=caption_with_source)
    except Exception as e:
        logging.error(f"Error forwarding album: {e}")

# ------- ADMIN CONTROLS (from your own user ID only)
@client.on(events.NewMessage(from_users=ADMIN_ID))
async def admin_commands(event):
    global forwarding_enabled, source_channels, destination_channel
    cmd = event.raw_text.strip()
    if cmd == "/help":
        await event.reply("Admin commands:\n"
                          "/start - Enable forwarding\n"
                          "/stop - Pause forwarding\n"
                          "/status - Show if bot is forwarding\n"
                          "/showconfig - Show current channels\n"
                          "/addsource <channel>\n"
                          "/removesource <channel>\n"
                          "/setdest <channel>")
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
        await event.reply(f"Sources: {source_channels}\nDestination: {destination_channel}")
    elif cmd.startswith("/addsource "):
        ch = cmd.split(maxsplit=1)[1].strip()
        if ch not in source_channels:
            source_channels.append(ch)
            await event.reply(f"✅ Added source channel: {ch}\n\n*Note: For permanent changes, update Railway Variables and restart!*")
        else:
            await event.reply(f"Channel {ch} already in source list.")
    elif cmd.startswith("/removesource "):
        ch = cmd.split(maxsplit=1)[1].strip()
        if ch in source_channels:
            source_channels.remove(ch)
            await event.reply(f"✅ Removed source channel: {ch}\n\n*Note: For permanent changes, update Railway Variables and restart!*")
        else:
            await event.reply(f"Channel {ch} not found in source list.")
    elif cmd.startswith("/setdest "):
        ch = cmd.split(maxsplit=1)[1].strip()
        destination_channel = ch
        await event.reply(f"✅ Destination set to: {ch}\n\n*Note: For permanent changes, update Railway Variables and restart!*")
    else:
        await event.reply("❓ Unknown command. Type /help.")

async def main():
    logging.info("🔄 Starting Telegram client...")
    await client.start()
    logging.info("✅ Logged in successfully!")
    logging.info(f"👂 Listening to: {', '.join(source_channels)}")
    await client.run_until_disconnected()

if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("🔌 Bot stopped by user.")
