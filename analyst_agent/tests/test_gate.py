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


def qa(text="", photo=False, topic=1773):
    return gate.Incoming(text=text, has_photo=photo, chat_id=-1002, user_id=777,
                         is_forum=True, topic_id=topic)


def test_qa_topic_needs_no_trigger_word(monkeypatch):
    """A topic dedicated to asking the agent: everything there is addressed to it."""
    monkeypatch.setattr(config, "QA_TOPIC", 1773)
    answer, why = gate.decide(qa("LINKUSD كم سيصل سعرها بعد ساعة؟"))
    assert answer is True and why == "qa topic"
    assert gate.decide(qa("NVDA؟"))[0] is True
    assert gate.decide(qa("على الخمس دقايق"))[0] is True
    assert gate.decide(qa(photo=True))[0] is True


def test_qa_topic_still_ignores_plain_chatter(monkeypatch):
    monkeypatch.setattr(config, "QA_TOPIC", 1773)
    assert gate.decide(qa("صباح الخير شباب"))[0] is False
    assert gate.decide(qa("الله يعطيك العافية"))[0] is False


def test_outside_the_qa_topic_nothing_is_answered(monkeypatch):
    monkeypatch.setattr(config, "QA_TOPIC", 1773)
    assert gate.decide(qa("NVDA وش رايك", topic=1))[0] is False


def test_groups_without_a_qa_topic_keep_the_trigger_rule(monkeypatch):
    monkeypatch.setattr(config, "QA_TOPIC", 0)
    assert gate.decide(group(text="NVDA وش رايك"))[0] is False
    assert gate.decide(group(text="حلل NVDA"))[0] is True


def test_analysable():
    assert gate.analysable(gate.Incoming(text="NVDA")) is True
    assert gate.analysable(gate.Incoming(text="يومي")) is True
    assert gate.analysable(gate.Incoming(has_photo=True)) is True
    assert gate.analysable(gate.Incoming(text="كيف الحال")) is False


def dm(user_id=777, text="حلل NVDA", photo=True):
    return gate.Incoming(text=text, has_photo=photo, chat_id=user_id,
                         user_id=user_id, is_private=True)


def test_private_chats_can_be_switched_off(monkeypatch):
    """ANALYST_DM_ALWAYS=false was not enough: a trigger word still passed."""
    monkeypatch.setattr(config, "ANSWER_PRIVATE", False)
    monkeypatch.setattr(config, "OWNER_IDS", set())
    assert gate.decide(dm())[1] == "private disabled"
    assert gate.decide(dm(text="حلل", photo=False))[0] is False
    assert gate.decide(dm(text="", photo=True))[0] is False


def test_the_owner_is_still_served_privately(monkeypatch):
    monkeypatch.setattr(config, "ANSWER_PRIVATE", False)
    monkeypatch.setattr(config, "OWNER_IDS", {42})
    assert gate.decide(dm(user_id=42))[0] is True
    assert gate.decide(dm(user_id=777))[0] is False


def test_the_group_is_unaffected(monkeypatch):
    monkeypatch.setattr(config, "ANSWER_PRIVATE", False)
    monkeypatch.setattr(config, "OWNER_IDS", set())
    assert gate.decide(group(text="حلل NVDA", has_photo=True))[0] is True


def test_private_chats_stay_on_by_default():
    assert gate.decide(dm())[0] is True


def test_the_private_notice_is_sent_once(monkeypatch):
    monkeypatch.setattr(config, "PRIVATE_NOTICE", "أنا أرد داخل القروب فقط")
    monkeypatch.setattr(gate, "_told_private", set())
    assert gate.private_notice(777) == "أنا أرد داخل القروب فقط"
    assert gate.private_notice(777) is None
    assert gate.private_notice(888) is not None


def test_no_notice_means_silence(monkeypatch):
    monkeypatch.setattr(config, "PRIVATE_NOTICE", "")
    monkeypatch.setattr(gate, "_told_private", set())
    assert gate.private_notice(777) is None


def test_an_owner_can_be_named_instead_of_numbered(monkeypatch):
    """Nobody knows their own Telegram id by heart."""
    monkeypatch.setattr(config, "OWNER_IDS", set())
    monkeypatch.setattr(config, "OWNER_USERNAMES", {"salem0557"})
    assert gate.is_owner(user_id=999, username="Salem0557") is True   # case-insensitive
    assert gate.is_owner(user_id=999, username="@salem0557") is True
    assert gate.is_owner(user_id=999, username="someone") is False


def test_an_owner_by_name_gets_into_a_closed_private_chat(monkeypatch):
    monkeypatch.setattr(config, "ANSWER_PRIVATE", False)
    monkeypatch.setattr(config, "OWNER_IDS", set())
    monkeypatch.setattr(config, "OWNER_USERNAMES", {"salem0557"})
    owner = gate.Incoming(text="حلل NVDA", has_photo=True, chat_id=5,
                          user_id=999, username="salem0557", is_private=True)
    stranger = gate.Incoming(text="حلل NVDA", has_photo=True, chat_id=5,
                             user_id=777, username="other", is_private=True)
    assert gate.decide(owner)[0] is True
    assert gate.decide(stranger)[0] is False


def test_here_survives_a_closed_private_chat(monkeypatch):
    """It is how you read the id that the setting is configured with."""
    monkeypatch.setattr(config, "ANSWER_PRIVATE", False)
    monkeypatch.setattr(config, "OWNER_IDS", set())
    monkeypatch.setattr(config, "OWNER_USERNAMES", set())
    assert gate.decide(gate.Incoming(text="/here", chat_id=5, user_id=777,
                                     is_private=True))[0] is True


def test_here_reports_your_own_id(monkeypatch):
    monkeypatch.setattr(config, "OWNER_IDS", set())
    monkeypatch.setattr(config, "OWNER_USERNAMES", set())
    report = gate.here_report(gate.Incoming(text="/here", chat_id=-1001,
                                            user_id=777, username="someone"))
    assert "777" in report and "someone" in report
    assert "لست ضمن الملاك" in report


def test_here_confirms_an_owner(monkeypatch):
    monkeypatch.setattr(config, "OWNER_USERNAMES", {"salem0557"})
    report = gate.here_report(gate.Incoming(text="/here", chat_id=-1, user_id=1,
                                            username="salem0557"))
    assert "ضمن الملاك ✅" in report


def test_owner_commands_accept_a_username(monkeypatch):
    monkeypatch.setattr(config, "OWNER_IDS", set())
    monkeypatch.setattr(config, "OWNER_USERNAMES", {"salem0557"})
    assert gate.diag_allowed(999, False, "salem0557") is True
    assert gate.diag_allowed(999, False, "stranger") is False


def test_the_cooldown_exempts_a_named_owner(monkeypatch):
    monkeypatch.setattr(config, "USER_COOLDOWN", 60)
    monkeypatch.setattr(config, "OWNER_IDS", set())
    monkeypatch.setattr(config, "OWNER_USERNAMES", {"salem0557"})
    assert gate.cooldown_ok(5, "salem0557") is True
    assert gate.cooldown_ok(5, "salem0557") is True
