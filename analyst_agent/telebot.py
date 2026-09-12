"""BotFather backend — the zero-setup way to run the agent.

Why this exists beside `userbot.py`: a userbot needs an interactive Telegram
login (a code sent to a phone) to produce a session string, which means a
local machine. A bot token does not — create the bot in Telegram, paste the
token into the host's variables, deploy. Same analysis, same gate, no login.

Run:  python -m analyst_agent.telebot

One Telegram rule to know: a bot in a group is subject to privacy mode, and
while it is on the bot only receives commands, mentions and replies to itself.
For the plain «حلل» trigger to work on a group photo, turn it off once in
@BotFather: /setprivacy -> pick the bot -> Disable.
"""
from __future__ import annotations

import asyncio
import logging
import os
from io import BytesIO

from telegram import Update
from telegram.constants import ChatAction, ChatType
from telegram.ext import (Application, ApplicationBuilder, ContextTypes,
                          MessageHandler, filters)

from . import analyst, config, gate

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("analyst.telebot")

NO_TOKEN = ("ضع ANALYST_BOT_TOKEN في ملف .env (توكن بوت جديد من @BotFather).\n"
            "لا تستخدم نفس توكن بوت الماسح — بوتان على توكن واحد يتعارضان.")


def token() -> str:
    value = (os.getenv("ANALYST_BOT_TOKEN") or "").strip()
    if not value:
        raise SystemExit(NO_TOKEN)
    return value


def _incoming(update: Update, bot_id: int) -> gate.Incoming:
    """The message, reduced to what the shared gate needs."""
    message = update.effective_message
    text = (message.caption or message.text or "").strip()
    replied = message.reply_to_message
    mentioned = False
    for entity in list(message.entities or []) + list(message.caption_entities or []):
        if entity.type in ("mention", "text_mention"):
            mentioned = True
            break
    return gate.Incoming(
        text=text,
        chat_id=update.effective_chat.id,
        user_id=update.effective_user.id if update.effective_user else None,
        has_photo=bool(message.photo),
        replied_has_photo=bool(replied and replied.photo),
        is_private=update.effective_chat.type == ChatType.PRIVATE,
        is_own=False,                       # a bot never sees its own messages
        mentioned=mentioned,
        reply_to_me=bool(replied and replied.from_user
                         and replied.from_user.id == bot_id),
    )


async def _photo_bytes(update: Update) -> bytes | None:
    """The largest size of the photo in this message, or in the one it replies to."""
    message = update.effective_message
    for candidate in (message, message.reply_to_message):
        if candidate and candidate.photo:
            file = await candidate.photo[-1].get_file()
            return bytes(await file.download_as_bytearray())
    return None


async def _send(update: Update, answer: analyst.Answer) -> None:
    message = update.effective_message
    text = answer.text.strip()
    if answer.chart_png:
        image = BytesIO(answer.chart_png)
        image.name = f"{(answer.symbol or 'chart')}_{answer.frame_key or ''}.png".replace("/", "-")
        caption = text if len(text) <= gate.CAPTION_LIMIT else answer.headline
        await message.reply_photo(photo=image, caption=caption)
        if len(text) > gate.CAPTION_LIMIT:
            for chunk in gate.chunks(text):
                await message.reply_text(chunk)
    else:
        for chunk in gate.chunks(text):
            await message.reply_text(chunk)


async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_message or not update.effective_chat:
        return
    incoming = _incoming(update, context.bot.id)
    answer_it, why = gate.decide(incoming)
    if not answer_it:
        return

    canned = gate.command_reply(incoming.text)
    if canned:
        await update.effective_message.reply_text(canned)
        return
    if not gate.cooldown_ok(incoming.user_id):
        return

    semaphore: asyncio.Semaphore = context.application.bot_data["semaphore"]
    log.info("analysing for chat=%s user=%s (%s): %r",
             incoming.chat_id, incoming.user_id, why, incoming.text[:80])
    try:
        async with semaphore:
            if config.SEND_TYPING:
                await context.bot.send_chat_action(incoming.chat_id, ChatAction.TYPING)
            image = await _photo_bytes(update)
            answer = await asyncio.to_thread(
                analyst.analyze, incoming.text, image, None, True,
                gate.addressed_explicitly(incoming))
        if answer.silent:
            return          # the photo was not a chart: no reply at all
        await _send(update, answer)
    except Exception:
        log.exception("handler failed")
        try:
            await update.effective_message.reply_text("صار خطأ غير متوقع عندي، جرّب مرة ثانية 🙏")
        except Exception:
            pass


def build() -> Application:
    app = (ApplicationBuilder()
           .token(token())
           .concurrent_updates(True)
           .build())
    app.bot_data["semaphore"] = asyncio.Semaphore(config.MAX_CONCURRENT)
    # One handler: photos, captions and plain text all go through the same gate.
    app.add_handler(MessageHandler(
        (filters.PHOTO | filters.TEXT | filters.CAPTION) & ~filters.StatusUpdate.ALL,
        on_message))
    return app


def main() -> None:
    app = build()
    log.info("analyst bot is running — waiting for charts")
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


if __name__ == "__main__":
    main()
