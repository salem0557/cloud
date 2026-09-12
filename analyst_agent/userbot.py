"""Telethon userbot: the agent's face in Telegram groups and DMs.

Behaviour in a group is deliberately quiet — it answers only when addressed
(a trigger word, a mention, or a reply to it), so it can live in a busy chat
without spamming. In a private chat every chart is analysed.

Run:  python -m analyst_agent.userbot
Login once to get a session string:  python -m analyst_agent.login
"""
from __future__ import annotations

import asyncio
import logging
import time

from telethon import TelegramClient, events
from telethon.sessions import StringSession

from . import analyst, config, frames

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    level=logging.INFO)
logging.getLogger("telethon").setLevel(logging.WARNING)
log = logging.getLogger("analyst.userbot")

CAPTION_LIMIT = 1024
MESSAGE_LIMIT = 4000

HELP = """أنا محلل فني آلي 📈

كيف تستخدمني:
• أرسل صورة التشارت واكتب معها الرمز والفريم: «حلل TSLA 15 دقيقة»
• أو بدون صورة: «حلل أرامكو يومي» / «BTC 4 ساعات»
• أو رد على صورة قديمة بكلمة «حلل»

الفريم هو الأساس: أكتبه أو خلّه ظاهر في الصورة، وإلا سأستخدم اليومي.
الفريمات المدعومة: 1m 5m 15m 30m 1h 2h 4h يومي أسبوعي شهري

ماذا ترجع لك: تشارت جديد بالمؤشرات (EMA 20/50/200، بولنجر، RSI، MACD، فوليوم،
الدعوم والمقاومات، فيبوناتشي، VWAP) + قراءة فنية مع خطة دخول وستوب وأهداف.

الأوامر: ‎.تحليل‎ | ‎/help‎ | ‎/frames‎ | ‎/ping"""

COMMANDS = {"/help", ".help", "/start", "مساعدة", "/frames", "/ping", ".ping"}

_last_request: dict[int, float] = {}
_seen_albums: set[int] = set()


def _cooldown_ok(user_id: int | None) -> bool:
    if not user_id or user_id in config.OWNER_IDS:
        return True
    now = time.time()
    last = _last_request.get(user_id, 0)
    if now - last < config.USER_COOLDOWN:
        return False
    _last_request[user_id] = now
    return True


def _chat_allowed(chat_id: int) -> bool:
    if chat_id in config.BLOCKED_CHATS:
        return False
    return not config.ALLOWED_CHATS or chat_id in config.ALLOWED_CHATS


def _has_trigger(text: str | None) -> bool:
    if not text:
        return False
    low = frames.normalize(text)
    return any(frames.normalize(trigger) in low for trigger in config.TRIGGERS)


async def _should_answer(event, me) -> tuple[bool, str]:
    """(answer?, why) — the gate that keeps the agent quiet in busy groups."""
    message = event.message
    text = message.message or ""

    if not _chat_allowed(event.chat_id):
        return False, "chat not allowed"
    # The userbot runs as the owner's own account, so his normal chatter shows
    # up here as an outgoing message: only act on it when he asks explicitly.
    if message.out and not _has_trigger(text):
        return False, "own message without trigger"
    if text.strip().lower() in COMMANDS:
        return True, "command"

    has_photo = bool(message.photo)
    replied = None
    if message.is_reply:
        try:
            replied = await message.get_reply_message()
        except Exception:
            log.debug("could not fetch replied message", exc_info=True)
    replied_photo = bool(replied and replied.photo)
    reply_to_me = bool(replied and replied.sender_id == me.id)

    if event.is_private and config.DM_ALWAYS_ANSWER:
        if has_photo or replied_photo or _has_trigger(text) or frames.parse(text):
            return True, "private chat"
        return False, "private but nothing to analyse"

    if _has_trigger(text):
        return True, "trigger word"
    if bool(getattr(event, "mentioned", False)) or reply_to_me:
        return True, "mention/reply"
    if has_photo and config.ANSWER_BARE_PHOTOS and not text:
        return True, "bare photo"
    return False, "not addressed"


async def _image_bytes(event) -> bytes | None:
    """The photo in this message, or in the message it replies to."""
    message = event.message
    if message.photo:
        return await message.download_media(file=bytes)
    if message.is_reply:
        try:
            replied = await message.get_reply_message()
            if replied and replied.photo:
                return await replied.download_media(file=bytes)
        except Exception:
            log.warning("could not download replied photo", exc_info=True)
    return None


async def _send_answer(event, answer: analyst.Answer) -> None:
    text = answer.text.strip()
    if answer.chart_png:
        from io import BytesIO

        image = BytesIO(answer.chart_png)
        image.name = f"{(answer.symbol or 'chart')}_{answer.frame_key or ''}.png".replace("/", "-")
        caption = text if len(text) <= CAPTION_LIMIT else answer.headline
        await event.reply(caption, file=image)
        if len(text) > CAPTION_LIMIT:
            for chunk in _chunks(text):
                await event.reply(chunk)
    else:
        for chunk in _chunks(text):
            await event.reply(chunk)


def _chunks(text: str, limit: int = MESSAGE_LIMIT) -> list[str]:
    """Split on blank lines so a section never breaks mid-sentence."""
    if len(text) <= limit:
        return [text]
    out, current = [], ""
    for block in text.split("\n"):
        if len(current) + len(block) + 1 > limit:
            out.append(current.rstrip())
            current = ""
        current += block + "\n"
    if current.strip():
        out.append(current.rstrip())
    return out


def build_client() -> TelegramClient:
    if not config.TELEGRAM_API_ID or not config.TELEGRAM_API_HASH:
        raise SystemExit("ضع TELEGRAM_API_ID و TELEGRAM_API_HASH في ملف .env "
                         "(من my.telegram.org)")
    session = (StringSession(config.TELEGRAM_SESSION) if config.TELEGRAM_SESSION
               else config.TELEGRAM_SESSION_NAME)
    return TelegramClient(session, config.TELEGRAM_API_ID, config.TELEGRAM_API_HASH)


async def main() -> None:
    client = build_client()
    await client.start()
    me = await client.get_me()
    log.info("logged in as %s (id=%s)", me.username or me.first_name, me.id)
    semaphore = asyncio.Semaphore(config.MAX_CONCURRENT)

    @client.on(events.NewMessage())
    async def handler(event):  # noqa: ANN001 - telethon callback
        try:
            answer_it, why = await _should_answer(event, me)
            if not answer_it:
                return
            text = (event.message.message or "").strip()
            low = text.lower()
            if low in ("/help", ".help", "/start", "مساعدة"):
                await event.reply(HELP)
                return
            if low == "/frames":
                await event.reply("الفريمات المدعومة: " + " | ".join(frames.all_keys()))
                return
            if low in ("/ping", ".ping"):
                await event.reply("شغّال ✅")
                return

            if not _cooldown_ok(event.sender_id):
                return
            album = getattr(event.message, "grouped_id", None)
            if album:
                if album in _seen_albums:
                    return
                _seen_albums.add(album)
                if len(_seen_albums) > 500:
                    _seen_albums.clear()

            log.info("analysing for chat=%s user=%s (%s): %r",
                     event.chat_id, event.sender_id, why, text[:80])
            async with semaphore:
                if config.SEND_TYPING:
                    async with event.client.action(event.chat_id, "typing"):
                        image = await _image_bytes(event)
                        answer = await asyncio.to_thread(analyst.analyze, text, image)
                else:
                    image = await _image_bytes(event)
                    answer = await asyncio.to_thread(analyst.analyze, text, image)
            await _send_answer(event, answer)
        except Exception:
            log.exception("handler failed")
            try:
                await event.reply("صار خطأ غير متوقع عندي، جرّب مرة ثانية 🙏")
            except Exception:
                pass

    log.info("analyst userbot is running — waiting for charts")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
