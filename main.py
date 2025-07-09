import asyncio
import os
import re
import logging
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from dotenv import load_dotenv
from collections import defaultdict

# --- Load ENV variables ---
load_dotenv()
api_id = int(os.getenv("API_ID"))
api_hash = os.getenv("API_HASH")
source_channels = [ch.strip() for ch in os.getenv("SOURCE_CHANNELS", "").split(",") if ch.strip()]
destination_channel = os.getenv("DEST_CHANNEL")

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
    # Hashtags are preserved!
    text = re.sub(r'(?i)^.*(credit|via):.*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'https?://\S+|t\.me/\S+|telegram\.me/\S+', '', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

# Buffer to collect grouped media (albums)
album_buffer = defaultdict(list)
album_tasks = {}

@client.on(events.NewMessage(chats=source_channels))
async def forward_message(event):
    try:
        message = event.message
        chat = await event.get_chat()
        source_name = getattr(chat, 'title', None) or getattr(chat, 'username', None) or str(chat.id)
        tag = f"Source: {source_name}"

        # Handle grouped (album) media
        if message.grouped_id:
            group_id = (event.chat_id, message.grouped_id)
            album_buffer[group_id].append((event, tag))  # Save tag for the album
            # Cancel any previous task for this album group (debounce)
            if group_id in album_tasks:
                album_tasks[group_id].cancel()
            # Set a delayed send (waits 1s for all parts to arrive)
            album_tasks[group_id] = asyncio.create_task(process_album(group_id))
        # Single media
        elif message.media:
            clean_caption = remove_mentions(message.text) if message.text else ""
            caption_with_source = f"{clean_caption}\n\n{tag}".strip()
            logging.info(f"Forwarding single media from {source_name} to @{destination_channel}")
            await client.send_file(destination_channel, file=event.message, caption=caption_with_source)
        # Plain text
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
    await asyncio.sleep(1.0)  # Wait for the rest of the album to arrive
    events_group = album_buffer.pop(group_id, [])
    if not events_group:
        return
    events_group.sort(key=lambda x: x[0].message.id)  # maintain order
    files = [e.message for e, _ in events_group]
    tag = events_group[0][1]  # Get tag from first event
    clean_caption = remove_mentions(events_group[0][0].message.text) if events_group[0][0].message.text else ""
    caption_with_source = f"{clean_caption}\n\n{tag}".strip()
    logging.info(f"Forwarding album (grouped_id={group_id[1]}) with {len(files)} media from chat {group_id[0]} to @{destination_channel}")
    try:
        await client.send_file(destination_channel, file=files, caption=caption_with_source)
    except Exception as e:
        logging.error(f"Error forwarding album: {e}")

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
