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
    monkeypatch.setattr(config, "ANSWER_ALL_PHOTOS", True)
    monkeypatch.setattr(gate, "_last_request", {})


def group(**kwargs):
    return gate.Incoming(chat_id=-1001, user_id=777, **kwargs)


def test_any_photo_is_a_request_whatever_the_caption():
    """Salem writes freely: "وش رايك؟", "ادخل ولا أنتظر؟", or nothing."""
    for caption in ("وش رايك فيه؟", "هذا ينفع للدخول", "شكله كاسر المقاومة", ""):
        answer, why = gate.decide(group(text=caption, has_photo=True))
        assert answer is True and why == "photo", caption


def test_photos_can_be_switched_off(monkeypatch):
    monkeypatch.setattr(config, "ANSWER_ALL_PHOTOS", False)
    assert gate.decide(group(text="شوفوا هذا", has_photo=True))[0] is False
    assert gate.decide(group(text="حلل هذا", has_photo=True))[0] is True


def test_trigger_word_answers():
    answer, why = gate.decide(group(text="حلل هذا الشارت"))
    assert answer is True and why == "trigger word"


def test_trigger_word_without_a_photo_still_answers():
    assert gate.decide(group(text="حلل NVDA يومي"))[0] is True


def test_mention_answers():
    answer, why = gate.decide(group(text="وش رايك؟", mentioned=True))
    assert answer is True and why == "mention/reply"


def test_reply_to_the_agent_answers():
    answer, why = gate.decide(group(text="والهدف الثاني؟", reply_to_me=True))
    assert answer is True and why == "mention/reply"


def test_text_without_a_photo_still_needs_addressing():
    """A group full of tickers must not trigger an answer per message."""
    assert gate.decide(group(text="نفيديا طالعة اليوم"))[0] is False
    assert gate.decide(group(text="حلل نفيديا"))[0] is True


def test_private_free_text_naming_a_symbol_is_enough():
    answer, why = gate.decide(gate.Incoming(chat_id=5, text="نفيديا؟", is_private=True))
    assert answer is True and why == "private chat"


def test_addressed_explicitly():
    assert gate.addressed_explicitly(group(text="حلل", has_photo=True)) is True
    assert gate.addressed_explicitly(group(text="وش رايك", has_photo=True)) is False
    assert gate.addressed_explicitly(group(text="وش رايك", mentioned=True)) is True
    assert gate.addressed_explicitly(gate.Incoming(text="", is_private=True)) is True


def test_private_chat_analyses_any_chart():
    answer, why = gate.decide(gate.Incoming(chat_id=5, has_photo=True, is_private=True))
    assert answer is True and why == "private chat"


def test_private_small_talk_is_ignored():
    assert gate.decide(gate.Incoming(chat_id=5, text="مرحبا", is_private=True))[0] is False


def test_own_outgoing_message_needs_a_trigger_or_a_photo():
    assert gate.decide(group(text="خلاص شريت", is_own=True))[0] is False
    assert gate.decide(group(text="حلل تسلا يومي", is_own=True))[0] is True
    assert gate.decide(group(text="وش رايك", has_photo=True, is_own=True))[0] is True


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


def forum(topic, text="حلل", photo=True):
    return gate.Incoming(text=text, has_photo=photo, chat_id=-1002,
                         user_id=777, is_forum=True, topic_id=topic)


def test_only_the_configured_topic_is_answered(monkeypatch):
    monkeypatch.setattr(config, "QA_TOPIC", 1773)
    assert gate.decide(forum(1773))[0] is True
    assert gate.decide(forum(1))[0] is False
    assert gate.decide(forum(None))[0] is False      # General reads as topic 1


def test_here_works_in_any_topic(monkeypatch):
    """You cannot configure a topic id you have no way to read."""
    monkeypatch.setattr(config, "QA_TOPIC", 1773)
    assert gate.decide(forum(1, text="/here", photo=False))[0] is True
    assert gate.decide(forum(999, text="/here", photo=False))[0] is True


def test_topic_limit_does_not_apply_to_private_chats(monkeypatch):
    monkeypatch.setattr(config, "QA_TOPIC", 1773)
    assert gate.decide(gate.Incoming(chat_id=5, has_photo=True, is_private=True))[0] is True


def test_no_topic_limit_when_unset(monkeypatch):
    monkeypatch.setattr(config, "QA_TOPIC", 0)
    assert gate.decide(forum(55))[0] is True
