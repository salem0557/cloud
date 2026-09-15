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

from . import analyst, config, doctor, gate, journal, watcher

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
        username=getattr(getattr(event, "sender", None), "username", None),
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
    if not gate.diag_allowed(event.sender_id, bool(event.is_private), _username(event)):
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


async def _post_alert(client, alert: watcher.Alert) -> bool:
    """Send one automatic recommendation into the alerts topic."""
    target = watcher.destination()
    if not target:
        return False
    chat_id, topic_id = target
    try:
        from io import BytesIO

        file = None
        if alert.chart_png:
            file = BytesIO(alert.chart_png)
            file.name = f"{alert.symbol}_{alert.frame_key}.png"
        await client.send_message(chat_id, alert.text, file=file, reply_to=topic_id)
        return True
    except Exception:
        log.exception("failed to post alert for %s", alert.symbol)
        return False


async def run_watch(client, force: bool = False) -> list[watcher.Alert]:
    alerts = await asyncio.to_thread(watcher.scan, None, None, force)
    sent = [alert for alert in alerts if await _post_alert(client, alert)]
    if sent:
        await asyncio.to_thread(watcher.mark_posted, sent)
        log.info("posted %d recommendation(s): %s", len(sent),
                 ", ".join(a.symbol for a in sent))
    return sent


async def _followup_loop(client) -> None:
    """Report each call's outcome as a reply to the call itself."""
    await asyncio.sleep(120)
    while True:
        try:
            await asyncio.to_thread(journal.evaluate)
            pending = await asyncio.to_thread(journal.pending_notifications)
            done = []
            for call in pending:
                try:
                    await client.send_message(call.chat_id, journal.outcome_message(call),
                                              reply_to=call.message_id)
                except Exception:
                    log.warning("could not report %s", call.symbol, exc_info=True)
                done.append(call.id)
            if done:
                await asyncio.to_thread(journal.mark_notified, done)
        except Exception:
            log.exception("follow-up loop failed")
        await asyncio.sleep(max(60, config.FOLLOWUP_INTERVAL_MIN * 60))


async def _watch_loop(client) -> None:
    """The recommendations topic fills itself while the userbot runs."""
    await asyncio.sleep(60)
    while True:
        try:
            await run_watch(client)
        except Exception:
            log.exception("watch loop failed")
        await asyncio.sleep(max(60, config.WATCH_INTERVAL_MIN * 60))


async def _attempt(what: str, send):
    """One send, retried once. Returns the sent message, True, or False."""
    for attempt in (1, 2):
        try:
            return await send() or True
        except Exception as exc:
            log.warning("%s failed (try %d): %s: %s", what, attempt,
                        type(exc).__name__, exc)
            if attempt == 1:
                await asyncio.sleep(2)
    return False


def _username(event) -> str | None:
    return getattr(getattr(event, "sender", None), "username", None)


def incoming_topic(event) -> int | None:
    reply_to = getattr(event.message, "reply_to", None)
    if not getattr(reply_to, "forum_topic", False):
        return None
    return (getattr(reply_to, "reply_to_top_id", None)
            or getattr(reply_to, "reply_to_msg_id", None))


async def _send_answer(event, answer: analyst.Answer):
    """Chart then analysis, as independent sends: a slow upload must not cost
    the reader the reading. Returns the posted message when there is one."""
    text = answer.text.strip()
    fits_caption = bool(answer.chart_png) and len(text) <= gate.CAPTION_LIMIT
    posted = None

    if answer.chart_png:
        from io import BytesIO

        image = BytesIO(answer.chart_png)
        image.name = f"{(answer.symbol or 'chart')}_{answer.frame_key or ''}.png".replace("/", "-")
        caption = text if fits_caption else answer.headline
        sent = await _attempt("send chart", lambda: event.reply(caption, file=image))
        if not sent:
            fits_caption = False
        elif sent is not True:
            posted = sent

    if not fits_caption:
        for chunk in gate.chunks(text):
            sent = await _attempt("send analysis", lambda chunk=chunk: event.reply(chunk))
            if posted is None and sent is not True:
                posted = sent
    return posted


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
                if why == "private disabled":
                    notice = gate.private_notice(event.sender_id)
                    if notice:
                        await _attempt("private notice", lambda: event.reply(notice))
                return
            text = (event.message.message or "").strip()
            if gate.is_here(text):
                await event.reply(gate.here_report(_incoming(event, me)))
                return
            if gate.is_post(text):
                await _publish(event, me)
                return
            if gate.is_stats(text):
                if not gate.diag_allowed(event.sender_id, bool(event.is_private),
                                         _username(event)):
                    return
                await asyncio.to_thread(journal.evaluate)
                await event.reply(journal.stats())
                return
            if gate.is_watchlist(text):
                if gate.diag_allowed(event.sender_id, bool(event.is_private),
                                     _username(event)):
                    await event.reply(watcher.status())
                return
            if gate.is_scan(text):
                if not gate.diag_allowed(event.sender_id, bool(event.is_private),
                                         _username(event)):
                    return
                if not watcher.destination():
                    await event.reply(watcher.status())
                    return
                await event.reply("جاري مسح المراقبة… ⏳")
                sent = await run_watch(event.client, force=True)
                await event.reply(f"تم نشر {len(sent)} توصية" if sent else
                                  "لا يوجد سهم يحقق الشروط الآن — لا توصية.")
                return
            if gate.is_diag(text):
                if not gate.diag_allowed(event.sender_id, bool(event.is_private),
                                         _username(event)):
                    return
                await event.reply("جاري الفحص… ⏳")
                checks = await asyncio.to_thread(doctor.run_all)
                await event.reply(doctor.report(checks))
                return
            canned = gate.command_reply(text)
            if canned:
                await event.reply(canned)
                return
            if not gate.cooldown_ok(event.sender_id, _username(event)):
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
            posted = await _send_answer(event, answer)
            if posted is not None and answer.call_id:
                await asyncio.to_thread(
                    journal.attach_message, answer.call_id, event.chat_id,
                    getattr(posted, "id", None), incoming_topic(event))
        except Exception as exc:
            log.exception("handler failed")
            try:
                await event.reply(
                    f"صار خطأ غير متوقع ({type(exc).__name__})، جرّب مرة ثانية 🙏")
            except Exception:
                pass

    if config.STARTUP_CHECK:
        for line in doctor.report(doctor.run_all(quick=True)).splitlines():
            if line.strip():
                log.info("%s", line)
    if config.FOLLOWUP_ENABLED:
        asyncio.create_task(_followup_loop(client))
        log.info("follow-up on: every %s min", config.FOLLOWUP_INTERVAL_MIN)
    if watcher.enabled():
        asyncio.create_task(_watch_loop(client))
        log.info("automatic recommendations on: every %s min -> %s",
                 config.WATCH_INTERVAL_MIN, watcher.destination())
    log.info("analyst userbot is running — waiting for charts")
    await client.run_until_disconnected()


if __name__ == "__main__":
    asyncio.run(main())
