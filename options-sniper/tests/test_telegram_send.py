"""Salem's two feeds are two TOPICS in one forum group, not two chats.

t.me/c/4330143547/943 (alerts) and t.me/c/4330143547/944 (the paper record)
share the group and differ only in the topic. Telegram routes that with
message_thread_id. Get it wrong and every message lands in the group's General
topic — which looks like it worked until you open the group.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest

import config as C
import telegram_send as T


class _Resp:
    ok = True

    def json(self):
        return {"ok": True}


@pytest.fixture
def posted(monkeypatch):
    sent = []

    def fake_post(url, json=None, timeout=None):
        sent.append(json)
        return _Resp()

    monkeypatch.setattr(T.requests, "post", fake_post)
    monkeypatch.setattr(T, "TELEGRAM_TOKEN", "t")
    monkeypatch.setattr(T, "TELEGRAM_CHAT_ID", "-100433")
    return sent


def test_an_alert_carries_the_alert_topic(posted, monkeypatch):
    monkeypatch.setattr(C, "TELEGRAM_TOPIC_ID", "943")
    assert T.send("hi")
    assert posted[0]["message_thread_id"] == 943
    assert posted[0]["chat_id"] == "-100433"


def test_the_paper_record_carries_the_paper_topic(posted, monkeypatch):
    monkeypatch.setattr(C, "TELEGRAM_TOPIC_ID", "943")
    monkeypatch.setattr(C, "TELEGRAM_PAPER_TOPIC_ID", "944")
    monkeypatch.setattr(C, "TELEGRAM_PAPER_CHAT_ID", "")
    assert T.send_paper("closed +40%")
    assert posted[0]["message_thread_id"] == 944


def test_no_topic_configured_sends_none_rather_than_an_empty_field(posted,
                                                                  monkeypatch):
    """A group that is not a forum rejects message_thread_id. Unset must mean
    absent, not present-and-blank."""
    monkeypatch.setattr(C, "TELEGRAM_TOPIC_ID", "")
    assert T.send("hi")
    assert "message_thread_id" not in posted[0]


def test_an_explicit_chat_does_not_inherit_the_alert_topic(posted, monkeypatch):
    """A topic id belongs to its own group. Carrying the alert topic into a
    different chat would post into whatever thread happens to hold that id."""
    monkeypatch.setattr(C, "TELEGRAM_TOPIC_ID", "943")
    assert T.send("hi", chat_id="-100999")
    assert "message_thread_id" not in posted[0]
    assert posted[0]["chat_id"] == "-100999"
