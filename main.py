import asyncio
import os
import re
import logging
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from dotenv import load_dotenv

# Load .env config
load_dotenv()

# Logging setup
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')
logging.getLogger("telethon").setLevel(logging.WARNING)  # Clean up Telethon internal logs

# Env variables
try:
    api_id = int(os.getenv("API_ID"))
    api_hash = os.getenv("API_HASH")
    # Accept both usernames and numeric IDs
    source_channels = [ch.strip() for ch in os.getenv("SOURCE_CHANNELS", "").split(",") if ch.strip()]
    source_channels = [int(ch) if ch.isdigit() else ch for ch in source_channels]
    destination_channel = os.getenv("DEST_CHANNEL")

    if not all([api_id, api_hash, source_channels, destination_channel]):
        raise ValueError("Missing one or more required environment variables.")
except Exception as e:
    logging.error(f"Error loading environment variables: {e}")
    exit(1)

# Make sure session folder exists
if not os.path.exists('sessions'):
    os.makedirs('sessions')

client = TelegramClient('sessions/forwarder_session', api_id, api_hash)

def remove_mentions(text):
    if not text:
        return text
    text = re.sub(r'@\w+', '', text)
    text = re.sub(r'#\w+', '', text)
    text = re.sub(r'(?i)^.*(credit|via):.*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'https?://\S+|t\.me/\S+|telegram\.me/\S+', '', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

@client.on(events.NewMessage(chats=source_channels))
async def handle_all(event):
    chat = event.chat if event.chat else event.message.to_id
    # Log every message received for debug!
    logging.info(
        f"Received message from: {getattr(chat, 'username', None) or getattr(chat, 'title', None) or chat} "
        f"({event.chat_id}) | Has Media: {event.message.media is not None} | "
        f"Text: {event.message.text[:40] if event.message.text else ''}"
    )
    await forward_message(event)

async def forward_message(event):
    try:
        message = event.message

        # 1. First, try native forward (best for media, fastest)
        try:
            logging.info(f"Trying native forward from {event.chat.username if event.chat else event.chat_id} to @{destination_channel}")
            await event.message.forward_to(destination_channel)
            return  # Success! Done.
        except Exception as e:
            logging.warning(f"Native forward failed, trying upload: {e}")

        # 2. If native forward fails (maybe content protection), try sending as file
        if message.media:
            caption = remove_mentions(message.caption) if message.caption else None
            logging.info(f"Uploading media from {event.chat.username if event.chat else event.chat_id} to @{destination_channel}")
            await client.send_file(destination_channel, file=message.media, caption=caption)
        elif message.text:
            clean_text = remove_mentions(message.text)
            logging.info(f"Forwarding text from {event.chat.username if event.chat else event.chat_id} to @{destination_channel}")
            await client.send_message(destination_channel, clean_text)
        else:
            logging.info(f"Unhandled message type: {message.to_dict()}")

    except FloodWaitError as e:
        logging.warning(f"Hit rate limit. Sleeping for {e.seconds} seconds.")
        await asyncio.sleep(e.seconds)
    except Exception as e:
        logging.error(f"Error forwarding message: {e}")

async def main():
    logging.info("🔄 Starting Telegram client...")
    await client.start()
    logging.info(f"✅ Logged in successfully! Session is user: {not client.is_bot}")

    logging.info(f"👂 Listening to: {', '.join([str(ch) for ch in source_channels])}")
    await client.run_until_disconnected()

if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("🔌 Bot stopped by user.")
