import types

import pytest

telethon = pytest.importorskip("telethon", reason="telethon not installed")

from analyst_agent import config, userbot  # noqa: E402


class FakeMessage:
    def __init__(self, text="", photo=False, out=False, reply=None, grouped_id=None):
        self.message = text
        self.photo = photo
        self.out = out
        self._reply = reply
        self.is_reply = reply is not None
        self.grouped_id = grouped_id

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
    """The gate is async; tests only care about its answer."""
    import asyncio

    return asyncio.run(userbot._should_answer(event, ME))


@pytest.fixture(autouse=True)
def _open_config(monkeypatch):
    monkeypatch.setattr(config, "ALLOWED_CHATS", set())
    monkeypatch.setattr(config, "BLOCKED_CHATS", set())
    monkeypatch.setattr(config, "DM_ALWAYS_ANSWER", True)
    monkeypatch.setattr(config, "ANSWER_BARE_PHOTOS", False)
    monkeypatch.setattr(userbot, "_last_request", {})


def test_group_needs_addressing():
    # a plain group photo with no trigger: stay quiet
    answer, _ = (_gate(FakeEvent(FakeMessage("شوفوا هذا", photo=True))))
    assert answer is False

    # trigger word: answer
    answer, why = (_gate(FakeEvent(FakeMessage("حلل هذا الشارت", photo=True))))
    assert answer is True and why == "trigger word"

    # @mention without a trigger word: answer
    answer, why = (_gate(FakeEvent(FakeMessage("وش رايك؟", photo=True), mentioned=True)))
    assert answer is True and why == "mention/reply"

    # reply to the agent's own message: answer
    replied = FakeMessage("تحليل سابق")
    replied.sender_id = ME.id
    answer, why = (_gate(FakeEvent(FakeMessage("والهدف الثاني؟", reply=replied))))
    assert answer is True and why == "mention/reply"

    # blocked chat: silent
    config.BLOCKED_CHATS.add(-1001)
    answer, _ = (_gate(FakeEvent(FakeMessage("حلل", photo=True))))
    assert answer is False
    config.BLOCKED_CHATS.clear()

    # private chat with a photo: answer
    answer, why = (_gate(FakeEvent(FakeMessage("", photo=True), private=True)))
    assert answer is True and why == "private chat"

    # the owner's own outgoing chatter: silent unless it triggers
    answer, _ = (_gate(FakeEvent(FakeMessage("خلاص شريت", out=True))))
    assert answer is False
    answer, _ = (_gate(FakeEvent(FakeMessage("حلل تسلا يومي", out=True))))
    assert answer is True


def test_allowed_chats_whitelist(monkeypatch):
    monkeypatch.setattr(config, "ALLOWED_CHATS", {-1002})
    assert userbot._chat_allowed(-1002) is True
    assert userbot._chat_allowed(-1001) is False


def test_trigger_detection():
    assert userbot._has_trigger("حلل لي")
    assert userbot._has_trigger("ANALYZE this")
    assert not userbot._has_trigger("صباح الخير")
    assert not userbot._has_trigger(None)


def test_cooldown_blocks_a_second_request(monkeypatch):
    monkeypatch.setattr(config, "USER_COOLDOWN", 60)
    monkeypatch.setattr(config, "OWNER_IDS", set())
    assert userbot._cooldown_ok(5) is True
    assert userbot._cooldown_ok(5) is False
    assert userbot._cooldown_ok(6) is True


def test_owner_skips_the_cooldown(monkeypatch):
    monkeypatch.setattr(config, "OWNER_IDS", {42})
    assert userbot._cooldown_ok(42) is True
    assert userbot._cooldown_ok(42) is True


def test_chunks_respect_the_telegram_limit():
    text = "\n".join(f"سطر رقم {i}" for i in range(1200))
    chunks = userbot._chunks(text)
    assert len(chunks) > 1
    assert all(len(chunk) <= userbot.MESSAGE_LIMIT for chunk in chunks)
    assert "".join(chunks).replace("\n", "") == text.replace("\n", "")


def test_short_text_is_one_chunk():
    assert userbot._chunks("سطر واحد") == ["سطر واحد"]
