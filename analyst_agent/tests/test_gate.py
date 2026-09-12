"""The gate decides when the agent speaks — the same rules for both backends,
so these tests need neither Telegram library installed."""
import pytest

from analyst_agent import config, gate


@pytest.fixture(autouse=True)
def _open_config(monkeypatch):
    monkeypatch.setattr(config, "ALLOWED_CHATS", set())
    monkeypatch.setattr(config, "BLOCKED_CHATS", set())
    monkeypatch.setattr(config, "OWNER_IDS", set())
    monkeypatch.setattr(config, "DM_ALWAYS_ANSWER", True)
    monkeypatch.setattr(config, "ANSWER_BARE_PHOTOS", False)
    monkeypatch.setattr(gate, "_last_request", {})


def group(**kwargs):
    return gate.Incoming(chat_id=-1001, user_id=777, **kwargs)


def test_group_photo_without_addressing_is_ignored():
    assert gate.decide(group(text="شوفوا هذا", has_photo=True))[0] is False


def test_trigger_word_answers():
    answer, why = gate.decide(group(text="حلل هذا الشارت", has_photo=True))
    assert answer is True and why == "trigger word"


def test_trigger_word_without_a_photo_still_answers():
    assert gate.decide(group(text="حلل NVDA يومي"))[0] is True


def test_mention_answers():
    answer, why = gate.decide(group(text="وش رايك؟", has_photo=True, mentioned=True))
    assert answer is True and why == "mention/reply"


def test_reply_to_the_agent_answers():
    answer, why = gate.decide(group(text="والهدف الثاني؟", reply_to_me=True))
    assert answer is True and why == "mention/reply"


def test_bare_photo_only_when_enabled(monkeypatch):
    assert gate.decide(group(has_photo=True))[0] is False
    monkeypatch.setattr(config, "ANSWER_BARE_PHOTOS", True)
    answer, why = gate.decide(group(has_photo=True))
    assert answer is True and why == "bare photo"


def test_private_chat_analyses_any_chart():
    answer, why = gate.decide(gate.Incoming(chat_id=5, has_photo=True, is_private=True))
    assert answer is True and why == "private chat"


def test_private_small_talk_is_ignored():
    assert gate.decide(gate.Incoming(chat_id=5, text="مرحبا", is_private=True))[0] is False


def test_own_outgoing_message_needs_a_trigger():
    assert gate.decide(group(text="خلاص شريت", is_own=True))[0] is False
    assert gate.decide(group(text="حلل تسلا يومي", is_own=True))[0] is True


def test_blocked_and_allowed_chats(monkeypatch):
    monkeypatch.setattr(config, "BLOCKED_CHATS", {-1001})
    assert gate.decide(group(text="حلل", has_photo=True))[0] is False
    monkeypatch.setattr(config, "BLOCKED_CHATS", set())
    monkeypatch.setattr(config, "ALLOWED_CHATS", {-1002})
    assert gate.decide(group(text="حلل", has_photo=True))[0] is False
    assert gate.chat_allowed(-1002) is True


def test_commands_always_pass():
    for command in ("/help", "/ping", "/frames", "/help@my_bot"):
        assert gate.decide(group(text=command))[0] is True


@pytest.mark.parametrize("text,expected", [
    ("/ping", "شغّال ✅"),
    ("/PING", "شغّال ✅"),
    ("/help@analyst_bot", gate.HELP),
    ("حلل", None),
])
def test_command_reply(text, expected):
    assert gate.command_reply(text) == expected


def test_frames_command_lists_frames():
    assert "15m" in gate.command_reply("/frames")


def test_trigger_detection():
    assert gate.has_trigger("حلل لي")
    assert gate.has_trigger("ANALYZE this")
    assert not gate.has_trigger("صباح الخير")
    assert not gate.has_trigger(None)


def test_cooldown_blocks_a_second_request(monkeypatch):
    monkeypatch.setattr(config, "USER_COOLDOWN", 60)
    assert gate.cooldown_ok(5) is True
    assert gate.cooldown_ok(5) is False
    assert gate.cooldown_ok(6) is True


def test_owner_skips_the_cooldown(monkeypatch):
    monkeypatch.setattr(config, "OWNER_IDS", {42})
    assert gate.cooldown_ok(42) is True
    assert gate.cooldown_ok(42) is True


def test_chunks_respect_the_telegram_limit():
    text = "\n".join(f"سطر رقم {i}" for i in range(1200))
    parts = gate.chunks(text)
    assert len(parts) > 1
    assert all(len(part) <= gate.MESSAGE_LIMIT for part in parts)
    assert "".join(parts).replace("\n", "") == text.replace("\n", "")


def test_short_text_is_one_chunk():
    assert gate.chunks("سطر واحد") == ["سطر واحد"]
