"""The Telethon adapter: it must hand the gate the right fields."""
import types

import pytest

pytest.importorskip("telethon", reason="telethon not installed")

from analyst_agent import userbot  # noqa: E402


class FakeMessage:
    def __init__(self, text="", photo=False, out=False, reply=None, sender_id=1):
        self.message = text
        self.photo = photo
        self.out = out
        self.sender_id = sender_id
        self._reply = reply
        self.is_reply = reply is not None

    async def get_reply_message(self):
        return self._reply


class FakeEvent:
    def __init__(self, message, chat_id=-1001, private=False, mentioned=False,
                 sender_id=777):
        self.message = message
        self.chat_id = chat_id
        self.is_private = private
        self.mentioned = mentioned
        self.sender_id = sender_id


ME = types.SimpleNamespace(id=999, username="analyst")


def _gate(event):
    import asyncio

    return asyncio.run(userbot._should_answer(event, ME))


def test_trigger_word_in_a_group():
    assert _gate(FakeEvent(FakeMessage("حلل هذا", photo=True))) == (True, "trigger word")


def test_free_caption_on_a_photo_is_enough():
    assert _gate(FakeEvent(FakeMessage("وش رايك فيه؟", photo=True))) == (True, "photo")


def test_group_text_without_addressing_is_ignored():
    assert _gate(FakeEvent(FakeMessage("نفيديا طالعة")))[0] is False


def test_reply_to_the_agent_is_recognised():
    replied = FakeMessage("تحليل سابق", sender_id=ME.id)
    assert _gate(FakeEvent(FakeMessage("والهدف؟", reply=replied))) == (True, "mention/reply")


def test_reply_carrying_a_photo_is_passed_through():
    replied = FakeMessage("", photo=True, sender_id=123)
    assert _gate(FakeEvent(FakeMessage("حلل", reply=replied)))[0] is True


def test_owner_chatter_is_ignored_without_a_trigger():
    assert _gate(FakeEvent(FakeMessage("خلاص شريت", out=True)))[0] is False


def test_build_client_needs_credentials(monkeypatch):
    from analyst_agent import config

    monkeypatch.setattr(config, "TELEGRAM_API_ID", 0)
    with pytest.raises(SystemExit):
        userbot.build_client()
