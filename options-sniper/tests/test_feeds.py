"""Three feeds, one group. The topic id is the only thing keeping them apart.

Salem, 2026-09-09: "مسجلة مسبقا / TELEGRAM_CHAT_ID=-1004330143547 /
TELEGRAM_PAPER_CHAT_ID=-1004330143547".

Both chat ids are the SAME group, which is correct — his 943, 944 and 945 are
topics inside one forum, not three chats. It also means a missing topic id
cannot be noticed by looking at the chat ids: the message simply lands in the
group's General topic, beside another feed, and the split looks fine until
you open Telegram.
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import config as C
import spx
import telegram_send


def _sent(monkeypatch):
    calls = []
    monkeypatch.setattr(telegram_send, "send",
                        lambda text, chat_id=None, topic=None:
                        calls.append((chat_id, topic)) or 1)
    return calls


def test_the_spx_report_falls_back_to_the_main_group(monkeypatch):
    """He has one group. Setting only the TOPIC has to be enough."""
    monkeypatch.setattr(C, "TELEGRAM_CHAT_ID", "-1004330143547")
    monkeypatch.setattr(C, "TELEGRAM_SPX_CHAT_ID", "")
    monkeypatch.setattr(C, "TELEGRAM_SPX_TOPIC_ID", "945")
    monkeypatch.setattr(spx.market, "is_open", lambda *a, **k: True)
    monkeypatch.setattr(spx, "message", lambda *a, **k: "تقرير")
    calls = []
    monkeypatch.setattr(spx, "send",
                        lambda text, chat_id=None, topic=None:
                        calls.append((chat_id, topic)) or 1)
    assert spx.send_report() is True
    assert calls == [("-1004330143547", "945")]


def test_an_explicit_spx_chat_wins_over_the_fallback(monkeypatch):
    monkeypatch.setattr(C, "TELEGRAM_CHAT_ID", "-100111")
    monkeypatch.setattr(C, "TELEGRAM_SPX_CHAT_ID", "-100222")
    monkeypatch.setattr(C, "TELEGRAM_SPX_TOPIC_ID", "945")
    monkeypatch.setattr(spx.market, "is_open", lambda *a, **k: True)
    monkeypatch.setattr(spx, "message", lambda *a, **k: "تقرير")
    calls = []
    monkeypatch.setattr(spx, "send",
                        lambda text, chat_id=None, topic=None:
                        calls.append((chat_id, topic)) or 1)
    spx.send_report()
    assert calls == [("-100222", "945")]


def test_the_paper_feed_keeps_its_own_topic_with_a_shared_group(monkeypatch):
    """send_paper passes an explicit chat, so telegram_send must not fall back
    to the ALERTS topic — that would put the paper record in 943."""
    monkeypatch.setattr(telegram_send, "TELEGRAM_TOKEN", "")
    monkeypatch.setattr(C, "TELEGRAM_PAPER_CHAT_ID", "-1004330143547")
    monkeypatch.setattr(C, "TELEGRAM_PAPER_TOPIC_ID", "944")
    monkeypatch.setattr(C, "TELEGRAM_TOPIC_ID", "943")
    calls = []
    monkeypatch.setattr(telegram_send, "send",
                        lambda text, chat_id=None, topic=None:
                        calls.append((chat_id, topic)) or 1)
    telegram_send.send_paper("x")
    assert calls == [("-1004330143547", "944")]


def _check_src():
    return (pathlib.Path(__file__).resolve().parent.parent
            / "check.py").read_text()


def test_check_reports_a_collision_when_two_feeds_share_a_topic():
    """The failure this cannot be allowed to miss: same group, same topic, two
    feeds. Nothing errors; they just merge."""
    src = _check_src()
    assert "collision" in src
    assert "TELEGRAM_SPX_TOPIC_ID" in src


def test_check_sends_a_test_message_to_every_feed_not_just_the_first():
    """Salem, 2026-09-09: "ماجاني تنبيه اختبار الا على قناة وحدة فقط".

    check.py sent ONE message with no chat id — which telegram_send addresses
    to the ALERTS topic by default — and then reported "send: PASS". A green
    line that said nothing at all about 944 or 945.

    Configured is not delivered: a topic id can be a number Telegram rejects,
    a deleted thread, or one the bot was never added to, and all three look
    exactly like a correct setup until something is actually sent.
    """
    src = _check_src()
    body = src[src.index('section("7. Telegram")'):]
    assert "for name, chat, topic, _var in _FEEDS:" in body, (
        "the Telegram test must loop over every feed")
    # the old single unaddressed send is what produced one message
    assert 'send("✅ اختبار: بوت التنبيهات يعمل")' not in body
    assert "chat_id=chat, topic=topic" in body
