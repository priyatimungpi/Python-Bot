import asyncio
import os
import re
import logging
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Logging setup
logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s: %(message)s')
logging.getLogger("telethon").setLevel(logging.WARNING)  # Mute most Telethon internal logs

# Environment configs
try:
    api_id = int(os.getenv("API_ID"))
    api_hash = os.getenv("API_HASH")
    # Allow both channel usernames and IDs (as strings)
    source_channels = [ch.strip() for ch in os.getenv("SOURCE_CHANNELS", "").split(",") if ch.strip()]
    # Convert numeric strings to ints (Telethon can accept both)
    source_channels = [int(ch) if ch.isdigit() else ch for ch in source_channels]
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
    text = re.sub(r'#\w+', '', text)
    text = re.sub(r'(?i)^.*(credit|via):.*$', '', text, flags=re.MULTILINE)
    text = re.sub(r'https?://\S+|t\.me/\S+|telegram\.me/\S+', '', text)
    text = re.sub(r'[ \t]+', ' ', text)
    text = re.sub(r' *\n *', '\n', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()

# DEBUG: Print every new message from any channel in SOURCE_CHANNELS
@client.on(events.NewMessage(chats=source_channels))
async def debug_all(event):
    chat = event.chat if event.chat else event.message.to_id
    logging.info(
        f"Received message from: {getattr(chat, 'username', None) or getattr(chat, 'title', None) or chat} "
        f"({event.chat_id}) | Text: {event.message.text[:60] if event.message.text else ''}"
    )
    await forward_message(event)  # Forward the message

async def forward_message(event):
    try:
        message = event.message

        # Forward text messages
        if message.text:
            clean_text = remove_mentions(message.text)
            logging.info(f"Forwarding text from {event.chat.username if event.chat else event.chat_id} to @{destination_channel}")
            await client.send_message(destination_channel, clean_text)

        # Forward media with caption
        elif message.media and message.caption:
            clean_caption = remove_mentions(message.caption)
            logging.info(f"Forwarding media+caption from {event.chat.username if event.chat else event.chat_id} to @{destination_channel}")
            await client.send_file(destination_channel, file=message.media, caption=clean_caption)

        # Forward media without caption
        elif message.media:
            logging.info(f"Forwarding media from {event.chat.username if event.chat else event.chat_id} to @{destination_channel}")
            await client.send_file(destination_channel, file=message.media)

        # For other message types, just log for debug
        else:
            logging.info(f"Message from {event.chat.username if event.chat else event.chat_id} not forwarded (unknown type): {message.to_dict()}")

    except FloodWaitError as e:
        logging.warning(f"Hit rate limit. Sleeping for {e.seconds} seconds.")
        await asyncio.sleep(e.seconds)
    except Exception as e:
        logging.error(f"Error forwarding message: {e}")

async def main():
    logging.info("🔄 Starting Telegram client...")
    await client.start()
    logging.info("✅ Logged in successfully!")

    logging.info(f"👂 Listening to: {', '.join([str(ch) for ch in source_channels])}")
    await client.run_until_disconnected()

if __name__ == "__main__":
    if os.name == 'nt':  # Windows fix
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.info("🔌 Bot stopped by user.")
