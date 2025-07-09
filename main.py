import asyncio
import os
import re
import logging
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from dotenv import load_dotenv

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

@client.on(events.NewMessage(chats=source_channels))
async def forward_message(event):
    try:
        message = event.message
        # Media messages (photo, video, document, etc.)
        if message.media:
            clean_caption = remove_mentions(message.text) if message.text else None
            logging.info(f"Forwarding media from {event.chat.username} to @{destination_channel}")
            await client.send_file(destination_channel, file=event.message, caption=clean_caption)
        # Plain text messages
        elif message.text:
            clean_text = remove_mentions(message.text)
            logging.info(f"Forwarding text from {event.chat.username} to @{destination_channel}")
            await client.send_message(destination_channel, clean_text)
        else:
            logging.info(f"Unknown message type from {event.chat.username} - skipping.")
    except FloodWaitError as e:
        logging.warning(f"Hit rate limit. Sleeping for {e.seconds} seconds.")
        await asyncio.sleep(e.seconds)
    except Exception as e:
        logging.error(f"Error forwarding message: {e}")

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
