"""The trade Salem actually makes: 0DTE, sold at +40% within minutes.

Every earlier run measured 22+ DTE contracts held five days to a 2x target.
These tests pin the thing that decides whether the new engine can be trusted:
what it does when a minute contains both the target and the stop.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import zero_dte as z


def bar(t, o, h, l, c, bid=0.0, ask=0.0, vol=100):
    return {"time": f"2026-09-04T{t}:00", "open": o, "high": h, "low": l,
            "close": c, "avg_price": c, "bid": bid, "ask": ask, "volume": vol,
            "ask_volume": 60, "bid_volume": 40, "iv": 0.5, "delta": 0.3,
            "_keys": []}


# ── The break-even a +40% / -25% trade has to clear ──────────────
def test_taking_forty_against_a_twenty_five_stop_needs_under_forty_percent():
    """A 2x target needed 50% of trades to hit. +40% against -25% needs 38.5%,
    which is a completely different bar — and the reason every earlier verdict
    said nothing about this strategy."""
    assert round(z.break_even(40, 25), 1) == 38.5


def test_a_tighter_stop_lowers_the_bar():
    assert z.break_even(40, 20) < z.break_even(40, 25) < z.break_even(40, 40)


# ── The assumption that invents edges ───────────────────────────
def test_the_spread_is_charged_at_both_ends():
    """Buy above the mid, sell below it. At 10% the entry costs 5% more than
    the print and the exit gives back 5%."""
    rows = [bar("10:00", 1.0, 1.0, 1.0, 1.0),
            bar("10:01", 1.0, 5.0, 1.0, 5.0)]
    free = z.entry_exit(rows, 0, 40, 25, 15, 0.0, "15:30")
    paid = z.entry_exit(rows, 0, 40, 25, 15, 10.0, "15:30")
    assert free["entry"] == 1.0 and paid["entry"] == 1.05
    assert round(free["multiple"] if "multiple" in free
                 else free["exit"] / free["entry"], 4) == 1.4
    assert round(paid["exit"] / paid["entry"], 4) == 1.4    # nets the same +40%
    assert paid["exit"] > free["exit"]     # but the contract had to travel further


def test_a_minute_holding_both_target_and_stop_counts_as_the_stop():
    """An OHLC bar cannot say which came first. Assuming the good one is how a
    backtest manufactures a win rate."""
    rows = [bar("10:00", 1.0, 1.0, 1.0, 1.0, bid=0.99, ask=1.00),
            bar("10:01", 1.0, 1.50, 0.70, 1.0, bid=0.99, ask=1.00)]
    t = z.entry_exit(rows, 0, 40, 25, 15, 0.0, "15:30")
    assert t["why"] == "stop"
    assert t["exit"] == 0.75                    # entry 1.00, stop -25%


def test_the_target_pays_the_limit_price_not_the_spike():
    """A limit at +40% fills at +40%. Crediting the minute's high would pay
    for a print nobody's order reached."""
    rows = [bar("10:00", 1.0, 1.0, 1.0, 1.0, bid=0.99, ask=1.00),
            bar("10:01", 1.0, 3.00, 1.0, 2.9, bid=2.80, ask=2.90)]
    t = z.entry_exit(rows, 0, 40, 25, 15, 0.0, "15:30")
    assert t["why"] == "take" and t["exit"] == 1.40


def test_the_spread_can_turn_a_winner_into_a_loser():
    """This endpoint serves no bid/ask, so the spread is a parameter. A move
    that clears +40% on the trade price does not clear it once the round trip
    is paid for — and on a 0DTE contract the round trip is not small."""
    rows = [bar("10:00", 1.0, 1.0, 1.0, 1.0, bid=0.99, ask=1.00),
            bar("10:01", 1.0, 1.45, 1.0, 1.4, bid=1.20, ask=1.45)]
    assert z.entry_exit(rows, 0, 40, 25, 15, 0.0, "15:30")["why"] == "take"
    assert z.entry_exit(rows, 0, 40, 25, 15, 30.0, "15:30")["why"] != "take"


# ── Getting out ─────────────────────────────────────────────────
def test_the_trade_is_closed_when_the_clock_runs_out():
    """Neither target nor stop. A 0DTE contract is not held hoping."""
    rows = [bar("10:00", 1.0, 1.0, 1.0, 1.0, bid=0.99, ask=1.00)]
    rows += [bar(f"10:{m:02d}", 1.0, 1.05, 0.95, 0.90, bid=0.89, ask=0.91)
             for m in range(1, 6)]
    t = z.entry_exit(rows, 0, 40, 25, 5, 0.0, "15:30")
    assert t["why"] == "timeout" and t["minutes"] == 5
    assert t["exit"] < t["entry"]


def test_nothing_is_held_past_the_hard_exit():
    """The contract expires tonight; 15:30 is the last minute that matters."""
    rows = [bar("15:28", 1.0, 1.0, 1.0, 1.0, bid=0.99, ask=1.00),
            bar("15:29", 1.0, 1.05, 0.98, 1.0, bid=0.99, ask=1.01),
            bar("15:31", 1.0, 2.00, 1.0, 2.0, bid=1.99, ask=2.01)]
    t = z.entry_exit(rows, 0, 40, 25, 15, 0.0, "15:30")
    assert t["why"] == "timeout"                # the 2x minute is past the exit
    assert t["minutes"] == 1


def test_an_entry_with_no_minutes_left_is_not_a_trade():
    rows = [bar("15:29", 1.0, 1.0, 1.0, 1.0, bid=0.99, ask=1.00),
            bar("15:31", 1.0, 2.0, 1.0, 2.0, bid=1.99, ask=2.01)]
    assert z.entry_exit(rows, 0, 40, 25, 15, 0.0, "15:30") is None


def test_a_contract_with_no_price_is_skipped():
    rows = [bar("10:00", 0, 0, 0, 0), bar("10:01", 0, 0, 0, 0)]
    assert z.entry_exit(rows, 0, 40, 25, 15, 0.0, "15:30") is None


# ── Reading the minute ──────────────────────────────────────────
def test_the_minute_is_read_from_the_timestamp():
    assert z.minute_of({"time": "2026-09-04T10:47:00"}) == "10:47"
    assert z.minute_of({"time": "09:31:00"}) == "09:31"
    assert z.minute_of({}) == ""


def test_time_of_day_buckets_split_the_session():
    assert z.bucket("minute", "09:35") == "time=09:30-10"
    assert z.bucket("minute", "10:59") == "time=10-11:30"
    assert z.bucket("minute", "13:00") == "time=11:30-14"
    assert z.bucket("minute", "15:29") == "time=14-15:30"


def test_budget_buckets_match_the_three_salem_uses():
    assert z.bucket("price", 0.45) == "budget=$50"
    assert z.bucket("price", 0.90) == "budget=$100"
    assert z.bucket("price", 1.80) == "budget=$200"


def test_a_missing_feature_is_marked_not_guessed():
    assert z.bucket("spread_pct", None) == "spread_pct=?"


# ── Asking for a specific expiry ────────────────────────────────
import uw  # noqa: E402


def test_expiry_is_requested_by_date_not_by_dte(monkeypatch):
    """min_dte/max_dte are measured from TODAY, so asking for dte 0 on a past
    session matched nothing on all four dates tested. The screener names its
    array filters with a literal [] suffix, which is not a valid Python
    keyword, so the bare name is translated here."""
    seen = {}

    def fake(path, params=None):
        seen.update(params or {})
        return []

    monkeypatch.setattr(uw, "_get", fake)
    uw.screen_contracts(is_otm="true", expiry_dates=["2026-09-04"],
                        date="2026-09-04")
    assert seen["expiry_dates[]"] == ["2026-09-04"]
    assert "expiry_dates" not in seen
    assert "min_dte" not in seen and "max_dte" not in seen
