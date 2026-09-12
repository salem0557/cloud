"""The BotFather adapter: same gate, fields taken from a PTB update."""
import types

import pytest

pytest.importorskip("telegram", reason="python-telegram-bot not installed")

from analyst_agent import telebot  # noqa: E402


def _update(text="", photo=False, chat_type="supergroup", reply_from=None,
            reply_photo=False, entities=(), user_id=777, chat_id=-1001,
            caption=False, topic_id=None, is_forum=False):
    message = types.SimpleNamespace(
        message_thread_id=topic_id,
        text=None if caption else text,
        caption=text if caption else None,
        photo=[types.SimpleNamespace(file_id="x")] if photo else [],
        entities=list(entities), caption_entities=[],
        reply_to_message=(types.SimpleNamespace(
            photo=[types.SimpleNamespace(file_id="y")] if reply_photo else [],
            from_user=types.SimpleNamespace(id=reply_from))
            if reply_from is not None or reply_photo else None),
    )
    return types.SimpleNamespace(
        effective_message=message,
        effective_chat=types.SimpleNamespace(id=chat_id, type=chat_type,
                                             is_forum=is_forum),
        effective_user=types.SimpleNamespace(id=user_id),
    )


BOT_ID = 555


def test_caption_is_read_as_the_request():
    incoming = telebot._incoming(_update(text="حلل 15 دقيقة", photo=True, caption=True), BOT_ID)
    assert incoming.text == "حلل 15 دقيقة"
    assert incoming.has_photo is True
    assert incoming.is_own is False


def test_private_chat_is_detected():
    assert telebot._incoming(_update(chat_type="private"), BOT_ID).is_private is True


def test_reply_to_the_bot_is_detected():
    assert telebot._incoming(_update(text="والهدف؟", reply_from=BOT_ID), BOT_ID).reply_to_me is True
    assert telebot._incoming(_update(text="والهدف؟", reply_from=42), BOT_ID).reply_to_me is False


def test_replied_photo_is_detected():
    incoming = telebot._incoming(_update(text="حلل", reply_photo=True, reply_from=42), BOT_ID)
    assert incoming.replied_has_photo is True


def test_mention_entity_is_detected():
    mention = types.SimpleNamespace(type="mention")
    assert telebot._incoming(_update(text="@bot وش رايك", entities=[mention]), BOT_ID).mentioned


def test_forum_topic_is_carried_through():
    incoming = telebot._incoming(
        _update(text="حلل", photo=True, topic_id=1773, is_forum=True), BOT_ID)
    assert incoming.topic_id == 1773 and incoming.is_forum is True


def test_general_topic_reads_as_topic_one():
    from analyst_agent import gate

    incoming = telebot._incoming(_update(text="حلل", is_forum=True), BOT_ID)
    assert gate.topic_of(incoming) == gate.GENERAL_TOPIC


def test_only_the_configured_topic_is_answered(monkeypatch):
    from analyst_agent import config, gate

    monkeypatch.setattr(config, "QA_TOPIC", 1773)
    inside = telebot._incoming(_update(text="وش رايك", photo=True, topic_id=1773,
                                       is_forum=True), BOT_ID)
    outside = telebot._incoming(_update(text="وش رايك", photo=True, topic_id=1,
                                        is_forum=True), BOT_ID)
    assert gate.decide(inside)[0] is True
    assert gate.decide(outside)[0] is False


def test_missing_token_exits(monkeypatch):
    monkeypatch.delenv("ANALYST_BOT_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        telebot.token()


def test_token_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("ANALYST_BOT_TOKEN", " 123:abc ")
    assert telebot.token() == "123:abc"


def _context(error):
    return types.SimpleNamespace(error=error)


def test_token_conflict_is_one_readable_line(caplog):
    import asyncio

    from telegram.error import Conflict

    with caplog.at_level("ERROR"):
        asyncio.run(telebot.on_error(None, _context(Conflict("terminated by other getUpdates"))))
    assert "تعارض توكن" in caplog.text
    assert "Traceback" not in caplog.text


def test_network_hiccup_is_a_warning_not_an_error(caplog):
    import asyncio

    from telegram.error import TimedOut

    with caplog.at_level("WARNING"):
        asyncio.run(telebot.on_error(None, _context(TimedOut())))
    assert "انقطاع شبكة" in caplog.text
    assert "ERROR" not in caplog.text


def test_rate_limit_says_how_long(caplog):
    import asyncio

    from telegram.error import RetryAfter

    with caplog.at_level("WARNING"):
        asyncio.run(telebot.on_error(None, _context(RetryAfter(12))))
    assert "12" in caplog.text


def test_unexpected_errors_still_get_a_traceback(caplog):
    import asyncio

    with caplog.at_level("ERROR"):
        asyncio.run(telebot.on_error(None, _context(ValueError("boom"))))
    assert "خطأ غير متوقع" in caplog.text


def test_error_handler_is_registered(monkeypatch):
    monkeypatch.setenv("ANALYST_BOT_TOKEN", "123:abc")
    app = telebot.build()
    assert app.error_handlers
