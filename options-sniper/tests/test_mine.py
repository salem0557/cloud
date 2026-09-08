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
    """Written for how he types — "اشتريت سترايك 186", not a command."""
    assert mine.parse("دخلت") == ("in", None, None)
    assert mine.parse("اشتريت سترايك 186") == ("in", 186.0, None)
    assert mine.parse("اشتريت NVDA 186") == ("in", 186.0, "NVDA")
    assert mine.parse("خرجت 1.40") == ("out", 1.40, None)
    assert mine.parse("بعت") == ("out", None, None)


def test_anything_else_is_ignored_in_silence():
    """A chat is not a command line. An error under every stray word would
    make the section unusable."""
    for t in ("شكرا", "ممتاز", "", None, "👍"):
        assert mine.parse(t) == (None, None, None)
    assert mine.apply_reply({"message": {"text": "ممتاز"}}) == ""


# ── the fill ───────────────────────────────────────────────────
def test_a_reply_opens_the_contract_that_alert_offered():
    """He never has to name a ticker: the message he replied to already says
    which contract it was."""
    mine.remember_alert(7, ALERT)
    out = mine.apply_reply(_reply("دخلت"))
    assert out == "✅ NVDA 183 دخول $1.85"        # short, as he asked
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
    assert "183" in out and "186" in out          # what WAS offered
    assert mine._load(mine.MINE_FILE, {"open": []})["open"] == []


def test_no_reply_needed_when_he_names_the_strike():
    """"اشتريت سترايك 186" arrives on its own, and the strike he names is
    matched against today's alerts."""
    mine.remember_alert(7, ALERT)
    out = mine.apply_reply({"message": {"text": "اشتريت سترايك 186"}})
    assert "NVDA 186" in out
    assert mine._load(mine.MINE_FILE, {})["open"][0]["strike"] == 186


def test_a_strike_in_two_tickers_asks_which_rather_than_picking():
    """Ambiguity is reported, never resolved by choosing one."""
    mine.remember_alert(7, ALERT)
    other = dict(ALERT, ticker="AMD")
    mine.remember_alert(8, other)
    out = mine.apply_reply({"message": {"text": "اشتريت سترايك 186"}})
    assert "أي سهم" in out and "AMD" in out and "NVDA" in out
    assert mine._load(mine.MINE_FILE, {"open": []})["open"] == []


def test_a_strike_nobody_alerted_on_is_refused():
    mine.remember_alert(7, ALERT)
    out = mine.apply_reply({"message": {"text": "اشتريت سترايك 999"}})
    assert "999" in out
    assert mine._load(mine.MINE_FILE, {"open": []})["open"] == []


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
    assert out == "✅ NVDA 183 خروج $2.59 (+40.0%)"
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


# ── "ابيع سترايك 186؟" is a question, never a fill ─────────────
def test_asking_about_selling_is_not_read_as_a_sale():
    """Reading it as a sale would close a position he still holds. Checked
    before the fill words because "بعت" is one and "ابيع" is not."""
    assert mine.parse("ابيع سترايك 186")[0] == "ask"
    assert mine.parse("امسك ولا ابيع")[0] == "ask"
    assert mine.parse("وش رايك 186")[0] == "ask"
    assert mine.parse("بعت 0.59")[0] == "out"       # still a sale


def test_a_question_with_nothing_open_says_so_rather_than_advising():
    assert "ما عندك صفقة مفتوحة" in mine.apply_reply(
        {"message": {"text": "ابيع سترايك 186"}})


def test_a_question_does_not_close_anything():
    mine.remember_alert(7, ALERT)
    mine.apply_reply(_reply("دخلت"))
    mine.apply_reply({"message": {"text": "ابيع سترايك 183"}})
    assert len(mine.open_positions()) == 1          # still his


def test_two_open_positions_and_no_strike_asks_which():
    mine.remember_alert(7, ALERT)
    mine.apply_reply(_reply("دخلت"))
    mine.apply_reply(_reply("دخلت 186"))
    assert "أي وحدة" in mine.advise()


def test_advice_given_is_remembered_so_it_is_not_repeated():
    """An adviser repeating "اخرج" every five minutes is noise, and noise is
    how a real exit signal gets ignored."""
    mine.remember_alert(7, ALERT)
    mine.apply_reply(_reply("دخلت"))
    sym = mine.open_positions()[0]["option_symbol"]
    assert mine.set_flag(sym, "last_advice", "اخرج")
    assert mine.open_positions()[0]["last_advice"] == "اخرج"
    assert mine.set_flag(sym, "last_advice", None)
    assert "last_advice" not in mine.open_positions()[0]


def test_the_backlog_from_before_the_first_run_is_dropped():
    """getUpdates with no offset returns everything Telegram has been holding.
    Replaying that at the open would act on messages from days ago — an old
    "اشتريت" opening a position he never took today."""
    mine.remember_alert(7, ALERT)
    old = [{"update_id": 1, "message": {"text": "اشتريت سترايك 186"}}]
    sent = []
    import telegram_send
    real = telegram_send.poll
    telegram_send.poll = lambda off=None, timeout=0: (old, 2)
    try:
        assert mine.poll_and_apply(send_fn=sent.append) == 0
        assert sent == []
        assert mine._load(mine.MINE_FILE, {"open": []})["open"] == []
        # and from here on it acts normally
        telegram_send.poll = lambda off=None, timeout=0: (old, 3)
        assert mine.poll_and_apply(send_fn=sent.append) == 1
        assert mine._load(mine.MINE_FILE, {})["open"][0]["strike"] == 186
    finally:
        telegram_send.poll = real
