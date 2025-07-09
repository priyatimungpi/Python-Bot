import asyncio
import os
import re
import logging
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from dotenv import load_dotenv

# Load environment variables from .env file if present
load_dotenv()

# Logging setup
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')

# Environment configs
try:
    api_id = int(os.getenv("API_ID"))
    api_hash = os.getenv("API_HASH")
    source_channels = [ch.strip() for ch in os.getenv("SOURCE_CHANNELS", "").split(",") if ch.strip()]
    destination_channel = os.getenv("DEST_CHANNEL")

    if not all([api_id, api_hash, source_channels, destination_channel]):
        raise ValueError("Missing one or more required environment variables.")

except Exception as e:
    logging.error(f"Error loading environment variables: {e}")
    exit(1)

# Create Telegram session folder if needed
if not os.path.exists('sessions'):
    os.makedirs('sessions')

# Create Telegram client
client = TelegramClient('sessions/forwarder_session', api_id, api_hash)

def remove_mentions(text):
    if not text:
        return text
    text = re.sub(r'@\w+', '', text)
    # Do NOT remove hashtags, as requested!
    text = re.sub(r'(?i)^.*(credit|via):.*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'https?://\S+|t\.me/\S+|telegram\.me/\S+', '', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

async def forward_message(event):
    try:
        message = event.message

        if message.text and not message.media:
            clean_text = remove_mentions(message.text)
            logging.info(f"Forwarding text from {event.chat.username} to @{destination_channel}")
            await client.send_message(destination_channel, clean_text)

        elif message.media:
            clean_caption = remove_mentions(message.caption) if message.caption else None
            logging.info(f"Forwarding media from {event.chat.username} to @{destination_channel}")
            # This is the correct way! file=event.message handles all Telegram media
            await client.send_file(destination_channel, file=event.message, caption=clean_caption)

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

    client.add_event_handler(forward_message, events.NewMessage(chats=source_channels))

    logging.info(f"👂 Listening to: {', '.join(source_channels)}")
    await client.run_until_disconnected()

if __name__ == "__main__":
    if os.name == 'nt':  # Windows
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("🔌 Bot stopped by user.")
