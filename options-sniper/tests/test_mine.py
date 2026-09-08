"""Salem's own book — the trades HE takes, from the alerts, with fake money.

Two Telegram sections, two owners. Only the automatic one existed: topic 943
sent alerts and nothing came back, so his own results did not exist anywhere.
journal.csv had an empty `outcome` column waiting to be filled by hand, and
paper.py's own docstring already said nobody does that for a month.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import pytest

import config as C
import mine


@pytest.fixture(autouse=True)
def book(tmp_path, monkeypatch):
    monkeypatch.setattr(mine, "MINE_FILE", tmp_path / "mine.json")
    monkeypatch.setattr(mine, "SENT_FILE", tmp_path / "sent.json")
    monkeypatch.setattr(mine, "OFFSET_FILE", tmp_path / "off.json")


ALERT = {"ticker": "NVDA", "direction": "call", "time_riyadh": "16:47:12",
         "tiers": [{"tier": "🟢", "option_symbol": "NVDA...C183", "strike": 183,
                    "type": "call", "expiry": "2026-09-08", "ask": 1.85,
                    "bid": 1.75, "cost": 185, "delta": 0.44, "dte": 0},
                   {"tier": "🔴", "option_symbol": "NVDA...C186", "strike": 186,
                    "type": "call", "expiry": "2026-09-08", "ask": 0.42,
                    "bid": 0.38, "cost": 42, "delta": 0.10, "dte": 0}]}


def _reply(text, to=7):
    return {"message": {"text": text, "reply_to_message": {"message_id": to}}}


# ── reading what he typed ──────────────────────────────────────
def test_the_words_he_would_actually_type_are_understood():
    assert mine.parse("دخلت") == ("in", None)
    assert mine.parse("شريت 186") == ("in", 186.0)
    assert mine.parse("خرجت 1.40") == ("out", 1.40)
    assert mine.parse("بعت") == ("out", None)


def test_anything_else_is_ignored_in_silence():
    """A chat is not a command line. An error under every stray word would
    make the section unusable."""
    for t in ("شكرا", "ممتاز", "", None, "👍"):
        assert mine.parse(t) == (None, None)
    assert mine.apply_reply({"message": {"text": "ممتاز"}}) == ""


# ── the fill ───────────────────────────────────────────────────
def test_a_reply_opens_the_contract_that_alert_offered():
    """He never has to name a ticker: the message he replied to already says
    which contract it was."""
    mine.remember_alert(7, ALERT)
    out = mine.apply_reply(_reply("دخلت"))
    assert "NVDA" in out and "1.85" in out
    book = mine._load(mine.MINE_FILE, {})
    assert book["open"][0]["option_symbol"] == "NVDA...C183"
    assert book["open"][0]["price_source"] == "alert ask"


def test_a_number_picks_the_strike_from_that_same_alert():
    mine.remember_alert(7, ALERT)
    mine.apply_reply(_reply("دخلت 186"))
    assert mine._load(mine.MINE_FILE, {})["open"][0]["strike"] == 186


def test_a_strike_that_was_not_offered_is_refused_with_what_was():
    """Guessing which contract he meant is how a book records a trade nobody
    made."""
    mine.remember_alert(7, ALERT)
    out = mine.apply_reply(_reply("دخلت 999"))
    assert "183" in out and "186" in out
    assert mine._load(mine.MINE_FILE, {"open": []})["open"] == []


def test_a_reply_to_something_that_is_not_an_alert_says_so():
    out = mine.apply_reply(_reply("دخلت", to=999))
    assert "رد على رسالة التنبيه" in out


def test_the_same_contract_is_not_opened_twice():
    mine.remember_alert(7, ALERT)
    mine.apply_reply(_reply("دخلت"))
    assert "ماسكه أصلاً" in mine.apply_reply(_reply("دخلت"))


# ── the exit, which is his ─────────────────────────────────────
def test_he_can_close_at_a_price_he_names():
    """"اما الخروج فهو علي" — so the book takes his price when he gives one,
    and records that it was his."""
    mine.remember_alert(7, ALERT)
    mine.apply_reply(_reply("دخلت"))
    out = mine.apply_reply({"message": {"text": "خرجت 2.59"}})
    assert "+40" in out
    p = mine._load(mine.MINE_FILE, {})["closed"][0]
    assert p["exit_source"] == "his" and p["multiple"] == pytest.approx(1.4, abs=0.01)


def test_closing_with_nothing_open_says_so_rather_than_inventing_one():
    assert "ما عندك صفقة مفتوحة" in mine.apply_reply(
        {"message": {"text": "خرجت"}})


def test_two_open_positions_are_not_closed_by_a_guess():
    mine.remember_alert(7, ALERT)
    mine.apply_reply(_reply("دخلت"))
    mine.apply_reply(_reply("دخلت 186"))
    out = mine.apply_reply({"message": {"text": "خرجت 1.0"}})
    assert "أي وحدة" in out
    assert len(mine._load(mine.MINE_FILE, {})["open"]) == 2


# ── the record ─────────────────────────────────────────────────
def test_the_summary_counts_only_what_he_actually_closed():
    mine.remember_alert(7, ALERT)
    mine.apply_reply(_reply("دخلت"))
    mine.apply_reply({"message": {"text": "خرجت 2.59"}})
    s = mine.summary()
    assert s["n"] == 1 and s["won"] == 1
    assert s["avg"] == pytest.approx(1.4, abs=0.01)


def test_the_daily_message_names_the_open_ones_he_forgot():
    mine.remember_alert(7, ALERT)
    mine.apply_reply(_reply("دخلت"))
    msg = mine.daily_message()
    assert "مفتوح: 1" in msg and "خرجت" in msg


def test_the_lookup_does_not_grow_without_end():
    for i in range(450):
        mine.remember_alert(i + 1, ALERT)
    assert len(mine._load(mine.SENT_FILE, {})) <= 400
