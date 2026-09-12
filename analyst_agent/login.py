"""One-time login: prints a session string to put in TELEGRAM_SESSION.

    python -m analyst_agent.login

Asks for the phone number of the account that will act as the agent, then the
code Telegram sends it. The printed string is a full login for that account —
keep it in .env, never in a group or a repository.
"""
import asyncio

from telethon import TelegramClient
from telethon.sessions import StringSession

from . import config


async def main() -> None:
    api_id = config.TELEGRAM_API_ID
    api_hash = config.TELEGRAM_API_HASH
    if not api_id or not api_hash:
        api_id = int(input("TELEGRAM_API_ID: ").strip())
        api_hash = input("TELEGRAM_API_HASH: ").strip()
    async with TelegramClient(StringSession(), api_id, api_hash) as client:
        me = await client.get_me()
        print("\n" + "=" * 60)
        print("logged in as:", me.username or me.first_name, f"(id={me.id})")
        print("TELEGRAM_SESSION=" + client.session.save())
        print("=" * 60)
        print("انسخ السطر أعلاه إلى ملف .env — لا تشاركه مع أحد.")


if __name__ == "__main__":
    asyncio.run(main())
