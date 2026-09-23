"""Create the TELEGRAM_SESSION string for public mode — run it yourself, once.

    .venv/bin/python tools/make_session.py

Telegram sends a login code to your Telegram app; you type it (and your 2FA
password, if you have one) here. The printed string is full access to that
Telegram account: put it ONLY in Render → Environment as TELEGRAM_SESSION,
never in git or chat. Revoke it any time: Telegram → Settings → Devices.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from telethon import TelegramClient  # noqa: E402
from telethon.sessions import StringSession  # noqa: E402

from app.config import settings  # noqa: E402


async def main() -> None:
    if not settings.telegram_configured:
        sys.exit("Set TELEGRAM_API_ID and TELEGRAM_API_HASH in .env first.")
    client = TelegramClient(StringSession(), settings.telegram_api_id, settings.telegram_api_hash)
    await client.start()   # prompts for phone, code and 2FA password in this terminal
    me = await client.get_me()
    print(f"\nSigned in as {me.first_name} (@{me.username or me.id}).")
    print("\nTELEGRAM_SESSION=" + client.session.save())
    print("\nPaste that line into Render → Environment. Keep it secret.")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
