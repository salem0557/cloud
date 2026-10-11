"""Two ways to receive the source channel's posts. Both produce RawMessage.

* botapi  - this bot is an admin of the channel and gets channel_post /
            edited_channel_post updates. Telegram's Bot FAQ says bots do not
            see messages from other bots; whether that covers a channel post
            published by another bot is not documented, so it must be probed.
* userbot - a regular Telegram user account (Telethon, MTProto) subscribed to
            the channel. A user client sees every post a subscriber sees,
            including posts published by bots.
"""
from __future__ import annotations

import datetime as dt
import logging

from .models import RawMessage

log = logging.getLogger("alert_trader.channel")


def _aware(t):
    if t is None:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


def from_ptb(message) -> RawMessage:
    reply = getattr(message, "reply_to_message", None)
    via = getattr(message, "via_bot", None)
    sender = getattr(message, "sender_chat", None) or getattr(message, "from_user", None)
    return RawMessage(
        chat_id=message.chat.id, message_id=message.message_id, date=_aware(message.date),
        text=message.text or message.caption or "", edit_date=_aware(message.edit_date),
        reply_to_message_id=reply.message_id if reply else None,
        author_signature=getattr(message, "author_signature", None),
        via_bot=via.username if via else None,
        sender_id=sender.id if sender else None, transport="botapi")


def from_telethon(message, chat_id: int) -> RawMessage:
    reply = getattr(message, "reply_to", None)
    via = getattr(message, "via_bot_id", None)
    return RawMessage(
        chat_id=chat_id, message_id=message.id, date=_aware(message.date),
        text=message.message or "", edit_date=_aware(message.edit_date),
        reply_to_message_id=getattr(reply, "reply_to_msg_id", None),
        author_signature=getattr(message, "post_author", None),
        via_bot=str(via) if via else None, sender_id=getattr(message, "sender_id", None),
        transport="userbot")


def userbot_client(env):
    from telethon import TelegramClient
    from telethon.sessions import StringSession
    if not (env.tg_api_id and env.tg_api_hash and env.tg_session):
        raise RuntimeError("الربط بحساب مستخدم يتطلب TG_API_ID وTG_API_HASH وTG_SESSION")
    return TelegramClient(StringSession(env.tg_session), env.tg_api_id, env.tg_api_hash)


async def run_userbot(env, on_message) -> None:
    """Listen to the channel as a user; on_message(RawMessage) is awaited."""
    from telethon import events
    client = userbot_client(env)
    await client.start()

    async def handler(event):
        try:
            await on_message(from_telethon(event.message, event.chat_id))
        except Exception:
            log.exception("userbot message handling failed")

    client.add_event_handler(handler, events.NewMessage(chats=env.channel_id))
    client.add_event_handler(handler, events.MessageEdited(chats=env.channel_id))
    log.info("userbot listening on %s", env.channel_id)
    await client.run_until_disconnected()
