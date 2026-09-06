"""The paper month has to be scored the way the backtest was, or it says nothing.

If paper trading used its own exit logic, the first disagreement with the
backtest would be impossible to attribute — a real drop in the edge and a
drifted reimplementation look identical. So paper.py imports entry_exit rather
than rewriting it, and these tests pin that.
"""
import sys, pathlib, json
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import config as C
import paper
import zero_dte


def bar(t, o, h, l, c):
    return {"time": f"2026-09-04T{t}:00", "open": o, "high": h, "low": l,
            "close": c, "avg_price": c, "bid": 0.0, "ask": 0.0, "volume": 100,
            "ask_volume": 60, "bid_volume": 40, "iv": 0.5,
            "ask_px": c * 1.02, "bid_px": c * 0.98, "_keys": []}


def alert(**over):
    p = {"ticker": "NVDA", "score": 91, "direction": "call",
         "reasoning": {"links": [{"step": "break", "text": "كسر المقاومة",
                                  "numbers": {}}], "gaps": []},
         "tiers": [{"tier": "🟡 <100$", "option_symbol": "NVDA260904C00185000",
                    "strike": 185.0, "type": "call", "expiry": "2026-09-04",
                    "dte": 0, "ask": 0.95, "cost": 95.0, "delta": 0.42}]}
    p.update(over)
    return p


def fresh(monkeypatch, tmp_path):
    monkeypatch.setattr(paper, "PAPER_FILE", tmp_path / "paper.json")


# ── The scoring must be the same code, not the same idea ────────
def test_paper_scores_with_the_backtest_s_own_exit_function():
    """Not 'the same logic' — the same function object."""
    assert paper.entry_exit is zero_dte.entry_exit


def test_the_take_and_stop_come_from_config_not_from_paper():
    """Live alerts, the paper book and the backtest all have to read one
    number, or a change to the rule silently applies to some of them."""
    assert paper.rule_for(0) == (40, 30)
    zero_dte_row = next(r for r in C.EXIT_RULES if r[0] == 0)
    assert paper.rule_for(0) == (zero_dte_row[1], abs(zero_dte_row[2]))


# ── Opening ─────────────────────────────────────────────────────
def test_an_alert_opens_a_position_and_keeps_its_reasoning(monkeypatch, tmp_path):
    fresh(monkeypatch, tmp_path)
    pos = paper.record(alert())
    assert pos["option_symbol"] == "NVDA260904C00185000"
    assert pos["take"] == 40 and pos["stop"] == 30
    assert pos["reasoning"] == ["كسر المقاومة"]
    assert paper.summary()["open"] == 1


def test_the_same_contract_is_not_opened_twice(monkeypatch, tmp_path):
    fresh(monkeypatch, tmp_path)
    paper.record(alert())
    assert paper.record(alert()) is None


def test_an_unpriced_tier_is_refused_not_recorded(monkeypatch, tmp_path):
    """A position that cannot be priced could never be scored, so recording it
    would only inflate the 'open' count forever."""
    fresh(monkeypatch, tmp_path)
    assert paper.record(alert(tiers=[{"tier": "🔴 <50$",
                                      "option_symbol": None}])) is None


# ── Closing ─────────────────────────────────────────────────────
def test_a_position_is_scored_from_the_minute_it_was_opened(monkeypatch, tmp_path):
    """Entering at 14:05 and being judged on the 09:30 bar would score a trade
    nobody took."""
    fresh(monkeypatch, tmp_path)
    rows = [bar("09:30", 1.0, 5.0, 1.0, 5.0)]        # a move before the entry
    rows += [bar(f"14:{m:02d}", 1.0, 1.0, 1.0, 1.0) for m in range(0, 10)]
    assert paper._index_of(rows, "14:05") == 6


def test_a_winner_closes_at_the_target_and_leaves_the_open_book(monkeypatch, tmp_path):
    fresh(monkeypatch, tmp_path)
    paper.record(alert())
    rows = [bar("14:00", 1.0, 1.0, 1.0, 1.0)]
    rows += [bar(f"14:{m:02d}", 1.0, 3.0, 1.0, 3.0) for m in range(1, 5)]
    monkeypatch.setattr(paper.uw, "contract_intraday", lambda *a, **k: rows)
    monkeypatch.setattr(paper, "measured_spread", lambda rows: 5.0)
    book = paper._load()
    book["open"][0]["entry_minute"] = "14:00"
    paper._save(book)
    closed = paper.mark(verbose=False)
    assert len(closed) == 1 and closed[0]["why"] == "take"
    assert paper.summary()["open"] == 0
    assert round(closed[0]["multiple"], 2) == 1.40      # nets exactly +40%


def test_a_tape_that_cannot_be_fetched_holds_the_position(monkeypatch, tmp_path):
    """A failed request is not a result. Dropping the position would quietly
    delete a trade from the record."""
    fresh(monkeypatch, tmp_path)
    paper.record(alert())

    def boom(*a, **k):
        raise paper.uw.UWError("503")

    monkeypatch.setattr(paper.uw, "contract_intraday", boom)
    assert paper.mark(verbose=False) == []
    assert paper.summary()["open"] == 1


# ── Reading the record ──────────────────────────────────────────
def test_a_thin_record_is_reported_as_saying_nothing(monkeypatch, tmp_path, capsys):
    fresh(monkeypatch, tmp_path)
    paper._save({"open": [], "closed": [
        {"ticker": "X", "why": "take", "multiple": 1.4} for _ in range(4)]})
    paper.report()
    out = capsys.readouterr().out
    assert "says nothing either way" in out
    assert str(C.PAPER_MIN_TRADES) in out


def test_the_record_is_shown_against_the_backtest_it_is_testing(monkeypatch,
                                                                tmp_path, capsys):
    """The paper month exists to check the backtest, so the backtest's number
    has to be on the same line."""
    fresh(monkeypatch, tmp_path)
    paper._save({"open": [], "closed": [
        {"ticker": f"T{i}", "why": "take" if i % 2 else "stop",
         "multiple": 1.4 if i % 2 else 0.7} for i in range(40)]})
    paper.report()
    out = capsys.readouterr().out
    assert "backtest said 43.2%" in out
    assert "backtest said $1.033" in out


# ── The two circuit breakers ────────────────────────────────────
def test_the_daily_loss_cap_closes_the_day(monkeypatch, tmp_path):
    """At 30 alerts a day and a 41% loss rate a bad session is a certainty. A
    rule that keeps firing into one turns a thin edge into a large loss."""
    fresh(monkeypatch, tmp_path)
    import datetime
    today = datetime.datetime.now().isoformat(timespec="seconds")
    paper._save({"open": [], "closed": [
        {"ticker": "A", "why": "stop", "multiple": 0.70, "cost": 500.0,
         "closed_at": today},
        {"ticker": "B", "why": "stop", "multiple": 0.70, "cost": 500.0,
         "closed_at": today}]})                      # -$300 on paper today
    monkeypatch.setattr(C, "MAX_DAILY_LOSS_USD", 300)
    ok, why = paper.may_open("call")
    assert not ok and "daily loss cap" in why


def test_yesterdays_losses_do_not_count_today(monkeypatch, tmp_path):
    fresh(monkeypatch, tmp_path)
    paper._save({"open": [], "closed": [
        {"ticker": "A", "why": "stop", "multiple": 0.5, "cost": 1000.0,
         "closed_at": "2020-01-01T15:00:00"}]})
    monkeypatch.setattr(C, "MAX_DAILY_LOSS_USD", 300)
    assert paper.may_open("call")[0]


def test_the_same_direction_cap_stops_the_thirtieth_call(monkeypatch, tmp_path):
    """Thirty calls on a rally day are one bet placed thirty times."""
    fresh(monkeypatch, tmp_path)
    monkeypatch.setattr(C, "MAX_SAME_DIRECTION_OPEN", 2)
    paper._save({"open": [
        {"option_symbol": "A", "type": "call", "open": True},
        {"option_symbol": "B", "type": "call", "open": True}], "closed": []})
    ok, why = paper.may_open("call")
    assert not ok and "same bet" in why
    assert paper.may_open("put")[0]          # the other side is a different bet


# ── The paper record has its own Telegram section ───────────────
import datetime  # noqa: E402
import telegram_send  # noqa: E402


def closed(why="take", mult=1.40, when=None, **over):
    row = {"ticker": "NVDA", "strike": 185.0, "direction": "call", "why": why,
           "multiple": mult, "cost": 95.0, "minutes": 4, "entry_price": 0.95,
           "exit_price": round(0.95 * mult, 2),
           "closed_at": (when or datetime.date.today().isoformat()) + "T20:14:00"}
    row.update(over)
    return row


def test_paper_goes_to_its_own_chat_when_one_is_set(monkeypatch):
    """A month of 'closed +40% in 4 minutes' would bury the handful of alerts
    Salem actually acts on."""
    seen = {}
    monkeypatch.setattr(telegram_send, "TELEGRAM_TOKEN", "t")
    monkeypatch.setattr(telegram_send, "TELEGRAM_CHAT_ID", "main")
    monkeypatch.setattr(telegram_send.C, "TELEGRAM_PAPER_CHAT_ID", "paper")
    monkeypatch.setattr(telegram_send.requests, "post",
                        lambda url, json=None, **k: (seen.update(json), _Ok())[1])
    telegram_send.send_paper("x")
    assert seen["chat_id"] == "paper"


def test_it_falls_back_to_the_main_chat_when_none_is_set(monkeypatch):
    """Unset, nothing breaks — both land in the same place."""
    seen = {}
    monkeypatch.setattr(telegram_send, "TELEGRAM_TOKEN", "t")
    monkeypatch.setattr(telegram_send, "TELEGRAM_CHAT_ID", "main")
    monkeypatch.setattr(telegram_send.C, "TELEGRAM_PAPER_CHAT_ID", "")
    monkeypatch.setattr(telegram_send.requests, "post",
                        lambda url, json=None, **k: (seen.update(json), _Ok())[1])
    telegram_send.send_paper("x")
    assert seen["chat_id"] == "main"


class _Ok:
    ok = True
    text = ""

    @staticmethod
    def json():
        return {"ok": True}


def test_a_close_message_fits_on_a_phone():
    """It arrives dozens of times a month. It is a record, not a decision."""
    msg = paper.close_message(closed())
    assert msg.count("\n") == 2
    assert "NVDA 185 كول" in msg and "وصل الهدف ✅" in msg
    assert "+40.0%" in msg and "(+38$)" in msg


def test_a_stop_and_a_timeout_read_differently():
    assert "ضرب الوقف ❌" in paper.close_message(closed("stop", 0.70))
    assert "انتهت المهلة ⏳" in paper.close_message(closed("timeout", 0.98))


def test_the_daily_summary_shows_today_beside_the_record():
    book = {"open": [], "closed": [closed(), closed("stop", 0.70),
                                   closed("timeout", 0.98)]}
    msg = paper.daily_message(book)
    assert "اليوم: 3 صفقات" in msg
    assert "الاختبار قال 43.2%" in msg          # the number being tested
    assert "الرقم لا يعني شيئاً بعد" in msg      # under 30 trades


def test_yesterdays_trades_are_not_counted_as_todays():
    old = closed(when="2026-01-02")
    msg = paper.daily_message({"open": [], "closed": [old]})
    assert "لا صفقات اليوم" in msg
    assert "الإجمالي: 1 صفقة" in msg            # still in the running record


def test_the_summary_is_sent_once_a_day(monkeypatch, tmp_path):
    fresh(monkeypatch, tmp_path)
    sent = []
    monkeypatch.setattr(paper, "send_paper", lambda m: (sent.append(m), True)[1])
    paper._save({"open": [], "closed": [closed()]})
    assert paper.send_daily() is True
    assert paper.send_daily() is False          # the monitor runs every 5 min
    assert len(sent) == 1
