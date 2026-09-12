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

from telethon import TelegramClient, events
from telethon.sessions import StringSession

from . import analyst, config, doctor, gate

logging.basicConfig(format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    level=logging.INFO)
logging.getLogger("telethon").setLevel(logging.WARNING)
log = logging.getLogger("analyst.userbot")

_seen_albums: set[int] = set()


async def _should_answer(event, me) -> tuple[bool, str]:
    """Ask the shared gate, with the fields only Telethon can supply."""
    message = event.message
    replied = None
    if message.is_reply:
        try:
            replied = await message.get_reply_message()
        except Exception:
            log.debug("could not fetch replied message", exc_info=True)
    return gate.decide(_incoming(event, me, replied))


def _incoming(event, me, replied=None) -> gate.Incoming:
    """Telethon message -> the shared gate's view of it."""
    message = event.message
    reply_to = getattr(message, "reply_to", None)
    topic_id = None
    is_forum = bool(getattr(reply_to, "forum_topic", False))
    if is_forum:
        # In a topic, Telegram threads every message under the topic's root id.
        topic_id = (getattr(reply_to, "reply_to_top_id", None)
                    or getattr(reply_to, "reply_to_msg_id", None))
    return gate.Incoming(
        text=message.message or "",
        chat_id=event.chat_id,
        topic_id=topic_id,
        is_forum=is_forum,
        user_id=event.sender_id,
        has_photo=bool(message.photo),
        replied_has_photo=bool(replied and replied.photo),
        is_private=bool(event.is_private),
        is_own=bool(message.out),
        mentioned=bool(getattr(event, "mentioned", False)),
        reply_to_me=bool(replied and replied.sender_id == me.id),
    )


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


async def _publish(event, me) -> None:
    """/post as a reply: re-send that message into the recommendations topic."""
    incoming = _incoming(event, me)
    if not gate.diag_allowed(event.sender_id, bool(event.is_private)):
        return
    if not config.ALERTS_TOPIC:
        await event.reply("قسم التوصيات غير مضبوط: أضف ANALYST_ALERTS_TOPIC "
                          "(خذ رقمه بأمر /here داخل ذلك القسم).")
        return
    target = await event.message.get_reply_message() if event.message.is_reply else None
    if not target:
        await event.reply("استخدم /post كـ«رد» على التحليل الذي تبي تنشره.")
        return
    try:
        await event.client.send_message(incoming.chat_id, target.text or "",
                                        file=target.media,
                                        reply_to=config.ALERTS_TOPIC)
        await event.reply("تم النشر في قسم التوصيات ✅")
    except Exception as exc:
        log.warning("publish failed: %s", exc)
        await event.reply(f"تعذّر النشر: {exc}"[:200])


async def _send_answer(event, answer: analyst.Answer) -> None:
    text = answer.text.strip()
    if answer.chart_png:
        from io import BytesIO

        image = BytesIO(answer.chart_png)
        image.name = f"{(answer.symbol or 'chart')}_{answer.frame_key or ''}.png".replace("/", "-")
        caption = text if len(text) <= gate.CAPTION_LIMIT else answer.headline
        await event.reply(caption, file=image)
        if len(text) > gate.CAPTION_LIMIT:
            for chunk in gate.chunks(text):
                await event.reply(chunk)
    else:
        for chunk in gate.chunks(text):
            await event.reply(chunk)


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
            if gate.is_here(text):
                await event.reply(gate.here_report(_incoming(event, me)))
                return
            if gate.is_post(text):
                await _publish(event, me)
                return
            if gate.is_diag(text):
                if not gate.diag_allowed(event.sender_id, bool(event.is_private)):
                    return
                await event.reply("جاري الفحص… ⏳")
                checks = await asyncio.to_thread(doctor.run_all)
                await event.reply(doctor.report(checks))
                return
            canned = gate.command_reply(text)
            if canned:
                await event.reply(canned)
                return
            if not gate.cooldown_ok(event.sender_id):
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
            addressed = gate.addressed_explicitly(gate.Incoming(
                text=text, has_photo=bool(event.message.photo),
                is_private=bool(event.is_private),
                mentioned=bool(getattr(event, "mentioned", False))))
            async with semaphore:
                if config.SEND_TYPING:
                    async with event.client.action(event.chat_id, "typing"):
                        image = await _image_bytes(event)
                        answer = await asyncio.to_thread(analyst.analyze, text, image,
                                                         None, True, addressed)
                else:
                    image = await _image_bytes(event)
                    answer = await asyncio.to_thread(analyst.analyze, text, image,
                                                     None, True, addressed)
            if answer.silent:
                return      # the photo was not a chart: no reply at all
            await _send_answer(event, answer)
        except Exception:
            log.exception("handler failed")
            try:
                await event.reply("صار خطأ غير متوقع عندي، جرّب مرة ثانية 🙏")
            except Exception:
                pass

    if config.STARTUP_CHECK:
        for line in doctor.report(doctor.run_all(quick=True)).splitlines():
            if line.strip():
                log.info("%s", line)
    log.info("analyst userbot is running — waiting for charts")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
