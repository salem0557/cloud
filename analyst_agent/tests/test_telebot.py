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


class _Bot:
    def __init__(self, status="member"):
        self._status = status

    async def get_chat_member(self, chat_id, user_id):
        return types.SimpleNamespace(status=self._status)


def _ctx(bot):
    return types.SimpleNamespace(bot=bot)


def _run(coro):
    import asyncio

    return asyncio.run(coro)


def test_group_admin_may_run_owner_commands_when_no_owner_is_set(monkeypatch):
    from analyst_agent import config, gate

    monkeypatch.setattr(config, "OWNER_IDS", set())
    incoming = gate.Incoming(text="/diag", chat_id=-1001, user_id=777)
    assert _run(telebot._may_manage(None, _ctx(_Bot("administrator")), incoming)) is True
    assert _run(telebot._may_manage(None, _ctx(_Bot("member")), incoming)) is False


def test_configured_owner_wins_over_admin_status(monkeypatch):
    from analyst_agent import config, gate

    monkeypatch.setattr(config, "OWNER_IDS", {42})
    owner = gate.Incoming(text="/diag", chat_id=-1001, user_id=42)
    stranger = gate.Incoming(text="/diag", chat_id=-1001, user_id=777)
    assert _run(telebot._may_manage(None, _ctx(_Bot("administrator")), owner)) is True
    # an admin who is not the configured owner is not trusted with settings
    assert _run(telebot._may_manage(None, _ctx(_Bot("administrator")), stranger)) is False


def test_private_chat_is_always_allowed_without_owners(monkeypatch):
    from analyst_agent import config, gate

    monkeypatch.setattr(config, "OWNER_IDS", set())
    incoming = gate.Incoming(text="/diag", chat_id=5, user_id=777, is_private=True)
    assert _run(telebot._may_manage(None, _ctx(_Bot("member")), incoming)) is True


class _Message:
    """Records what was sent, and can be told to fail the photo."""

    def __init__(self, fail_photo=False):
        self.photos = []
        self.texts = []
        self.fail_photo = fail_photo

    async def reply_photo(self, photo=None, caption=None, **kwargs):
        if self.fail_photo:
            raise TimeoutError("upload too slow")
        self.photos.append(caption)

    async def reply_text(self, text, **kwargs):
        self.texts.append(text)


def _answer(text, chart=b"\x89PNG"):
    from analyst_agent import analyst

    return analyst.Answer(True, text, headline="NVDA • ساعة", chart_png=chart,
                          symbol="NVDA", frame_key="1h")


def test_short_analysis_rides_the_caption(monkeypatch):
    message = _Message()
    monkeypatch.setattr(telebot.asyncio, "sleep", lambda *_: _noop())
    _run(telebot._send(types.SimpleNamespace(effective_message=message),
                       _answer("تحليل قصير")))
    assert message.photos == ["تحليل قصير"]
    assert message.texts == []


async def _noop():
    return None


def test_long_analysis_follows_the_chart(monkeypatch):
    message = _Message()
    long_text = "سطر تحليل طويل. " * 200
    _run(telebot._send(types.SimpleNamespace(effective_message=message),
                       _answer(long_text)))
    assert message.photos == ["NVDA • ساعة"]          # headline as caption
    assert message.texts                                # and the analysis followed


def test_a_failed_chart_still_delivers_the_analysis(monkeypatch):
    """The reader must never be left with a picture and no reading."""
    message = _Message(fail_photo=True)
    monkeypatch.setattr(telebot.asyncio, "sleep", lambda *_: _noop())
    _run(telebot._send(types.SimpleNamespace(effective_message=message),
                       _answer("تحليل قصير يكفي للتعليق")))
    assert message.photos == []
    assert message.texts == ["تحليل قصير يكفي للتعليق"]


def test_send_retries_once(monkeypatch):
    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise TimeoutError("first try")

    monkeypatch.setattr(telebot.asyncio, "sleep", lambda *_: _noop())
    assert _run(telebot._attempt("test", flaky)) is True
    assert calls["n"] == 2


def test_timeouts_are_configured(monkeypatch):
    from analyst_agent import config

    monkeypatch.setenv("ANALYST_BOT_TOKEN", "123:abc")
    app = telebot.build()
    request = app.bot._request[1]          # the non-getUpdates request object
    assert request._client.timeout.read >= config.TG_READ_TIMEOUT - 0.1
