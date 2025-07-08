import asyncio
import re
from telethon import TelegramClient, events

# Your Telegram credentials
api_id = 29294284
api_hash = '63944c40162b622bb87bb9e9a633b025'

# Prompt for channel usernames at runtime
source_channels = input("Enter source channel usernames (comma-separated, without @): ")
destination_channel = input("Enter the destination channel username (without @): ")

# Prepare list of sources
source_channels = [ch.strip() for ch in source_channels.split(',') if ch.strip()]

# Create the Telegram client
client = TelegramClient('forwarder_session', api_id, api_hash)

# Enhanced cleaner
def remove_mentions(text):
    if not text:
        return text
    # Remove @mentions and hashtags
    text = re.sub(r'@\w+', '', text)
    text = re.sub(r'#\w+', '', text)
    # Remove Credit/Via lines
    text = re.sub(r'(?i)^.*(credit|via):.*$', '', text, flags=re.MULTILINE)
    # Remove links
    text = re.sub(r'https?://\S+|t\.me/\S+|telegram\.me/\S+', '', text)
    # Remove extra spaces but preserve line breaks
    text = re.sub(r'[ \t]+', ' ', text)  # Collapse spaces/tabs but not newlines
    text = re.sub(r' *\n *', '\n', text)  # Clean up spaces around newlines
    text = re.sub(r'\n{3,}', '\n\n', text)  # Limit to max 2 consecutive newlines
    return text.strip()

async def main():
    print("🔄 Starting Telegram client...")

    # Connect and login
    await client.connect()
    if not await client.is_user_authorized():
        phone = input("📱 Enter your phone number with country code (e.g., +91xxxxxxxxxx): ")
        await client.send_code_request(phone)
        code = input("🔑 Enter the code you received: ")
        try:
            await client.sign_in(phone, code)
        except Exception as e:
            from telethon.errors import SessionPasswordNeededError
            if isinstance(e, SessionPasswordNeededError):
                password = input("🔒 Two-step verification enabled. Enter your Telegram password: ")
                await client.sign_in(password=password)
            else:
                raise

    print("✅ Logged in successfully!")

    @client.on(events.NewMessage(chats=source_channels))
    async def handler(event):
        message = event.message

        if message.text:
            clean_text = remove_mentions(message.text)
            print(f"📩 Forwarding text message from {event.chat.username} to @{destination_channel}")
            await client.send_message(destination_channel, clean_text)

        elif message.media and message.caption:
            clean_caption = remove_mentions(message.caption)
            print(f"📩 Forwarding media with caption from {event.chat.username} to @{destination_channel}")
            await client.send_file(destination_channel, file=message.media, caption=clean_caption)

        else:
            print(f"📩 Forwarding media without caption from {event.chat.username} to @{destination_channel}")
            await client.send_message(destination_channel, message)

    print(f"👂 Listening for messages from: {', '.join(source_channels)}... Leave this window open.")
    await client.run_until_disconnected()

# Run the bot
if __name__ == "__main__":
    import sys
    if sys.platform.startswith('win'):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
