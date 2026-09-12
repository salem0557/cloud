"""The BotFather adapter: same gate, fields taken from a PTB update."""
import types

import pytest

pytest.importorskip("telegram", reason="python-telegram-bot not installed")

from analyst_agent import telebot  # noqa: E402


def _update(text="", photo=False, chat_type="supergroup", reply_from=None,
            reply_photo=False, entities=(), user_id=777, chat_id=-1001,
            caption=False):
    message = types.SimpleNamespace(
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
        effective_chat=types.SimpleNamespace(id=chat_id, type=chat_type),
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


def test_missing_token_exits(monkeypatch):
    monkeypatch.delenv("ANALYST_BOT_TOKEN", raising=False)
    with pytest.raises(SystemExit):
        telebot.token()


def test_token_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("ANALYST_BOT_TOKEN", " 123:abc ")
    assert telebot.token() == "123:abc"
