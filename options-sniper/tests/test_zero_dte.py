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


# ── The gates from the previous system ──────────────────────────
import regime  # noqa: E402
import config as C  # noqa: E402


def sbar(t, h, l, c, v=1000):
    return {"start_time": f"2026-09-04T{t}:00", "date": "2026-09-04",
            "open": c, "high": h, "low": l, "close": c, "volume": v,
            "end_time": f"2026-09-04T{t}:00", "closed": True}


def tape(closes):
    """15m bars from a list of closes, starting at the open."""
    out = []
    for i, c in enumerate(closes):
        mins = 9 * 60 + 30 + i * 15
        out.append(sbar(f"{mins//60:02d}:{mins%60:02d}", c + 0.25, c - 0.25, c))
    return out


CHOP = [100 + (0.4 if i % 2 else -0.4) for i in range(20)]


def breakout(bars_up):
    """A range, then a break of it. `bars_up` decides how late the entry is."""
    return tape(CHOP + [100.6 + 0.55 * k for k in range(1, bars_up + 1)])


def test_a_minute_maps_to_its_fifteen_minute_bar():
    """The stock signal lives on 15m; the contract tape is per minute."""
    assert z.bar_key("10:47") == "10:45"
    assert z.bar_key("10:00") == "10:00"
    assert z.bar_key("15:29") == "15:15"
    assert z.bar_key("") == ""


def test_chasing_is_rejected_however_good_the_breakout():
    """Entering 0.30 ATR past the level is a late entry into a move that
    already happened. Our first run entered at EVERY minute of the session."""
    ok, why = regime.gate({"agree": 4, "chase_atr": 0.9}, "10:30")
    assert not ok and "chasing" in why
    ok, why = regime.gate({"agree": 4, "chase_atr": 0.1}, "10:30")
    assert ok


def test_a_split_committee_is_silent():
    """The old system sent nothing rather than a weak signal, which is the
    right default for an alert that gets acted on."""
    ok, why = regime.gate({"agree": 2, "chase_atr": 0.0}, "10:30")
    assert not ok and "committee" in why


def test_nothing_fires_outside_the_session():
    assert regime.gate({"agree": 4, "chase_atr": 0.0}, "08:15")[0] is False
    assert regime.gate({"agree": 4, "chase_atr": 0.0}, "15:45")[0] is False
    assert regime.time_window("10:30") == "momentum"
    assert regime.time_window("12:00") == "midday"
    assert regime.time_window("15:10") == "gamma"


def test_no_breakout_is_not_a_signal():
    """A flat tape must produce nothing at all."""
    flat = [sbar(f"{9 + (i*15)//60:02d}:{(30 + i*15) % 60:02d}",
                 100.2, 99.8, 100.0) for i in range(25)]
    assert all(regime.signal(flat, i) is None for i in range(len(flat)))


def test_a_clean_breakout_carries_the_committee_with_it():
    bars = breakout(2)
    sig = regime.signal(bars, len(bars) - 1)
    assert sig["direction"] == "call" and sig["agree"] == 4
    assert regime.gate(sig, "10:30")[0]


def test_the_same_breakout_three_bars_later_is_a_chase():
    """The signal is identical in every other respect. What changed is that
    the move already happened — which is what our first run kept buying."""
    early, late = regime.signal(breakout(2), 21), regime.signal(breakout(5), 24)
    assert early["direction"] == late["direction"] == "call"
    assert early["agree"] == late["agree"] == 4
    assert regime.gate(early, "10:30")[0] is True
    assert regime.gate(late, "10:30")[0] is False


def test_a_straight_line_rally_is_refused_as_exhausted():
    """RSI pins at 100 and the band is 48-72. Buying the top of a move that is
    already over is exactly what the RSI ceiling exists to stop."""
    vertical = tape([100 + i * 0.5 for i in range(25)])
    assert regime.signal(vertical, 24) is None


def test_vwap_sits_below_price_in_a_rally():
    assert regime.vwap(breakout(5)) < breakout(5)[-1]["close"]


def test_the_universe_is_the_liquid_names():
    """Their 0DTE contracts quote 1-3% wide; the population the first run
    measured quoted 10-25%, and the spread decides whether +40% is reachable."""
    assert C.LIQUID_0DTE == ["SPY", "QQQ", "IWM", "NVDA", "TSLA"]


# ── Salem's standing rule: not losing beats winning big ─────────
def test_a_smaller_target_with_the_same_stop_is_harder_not_easier():
    """The rule is 'profit at any size, but do not lose'. The arithmetic runs
    against the intuition: halving the target while keeping the stop raises the
    hit rate needed from 38.5% to 55.6%."""
    assert round(z.break_even(40, 25), 1) == 38.5
    assert round(z.break_even(20, 25), 1) == 55.6
    assert z.break_even(20, 25) > z.break_even(40, 25)


def test_the_stop_is_the_lever():
    """Same +25% target. Moving the stop from -25% to -10% takes the bar from
    50% down to 28.6% — a far bigger effect than any change to the target."""
    assert round(z.break_even(25, 25), 1) == 50.0
    assert round(z.break_even(25, 10), 1) == 28.6


def test_the_grid_pairs_every_target_with_a_matching_stop():
    """Tuning one without the other is what makes a small target look safe."""
    assert (25, 10) in z.GRID and (40, 25) in z.GRID
    assert all(stop < take for take, stop in z.GRID)


# ── The two bugs the first gated run exposed ────────────────────
def test_the_spread_comes_from_prints_not_from_a_dead_quote(monkeypatch):
    """Reading nbbo_bid off the DAILY tape gave a median of 200% on every
    session — which is what (ask-bid)/mid returns when the bid is zero, and the
    closing bid of a 0DTE contract that expired worthless IS zero. The prints
    are the only live source: buyers lifting the offer against sellers hitting
    the bid, minute by minute."""
    rows = [{"ask_px": 1.05, "bid_px": 0.95} for _ in range(10)]
    assert round(z.measured_spread(rows), 1) == 10.0


def test_a_dead_quote_no_longer_reads_as_a_tradeable_contract():
    """No bid-side prints at all -> None, not a number."""
    assert z.measured_spread([{"ask_px": 1.0, "bid_px": 0.0}] * 10) is None
    assert z.measured_spread([{"ask_px": 1.05, "bid_px": 0.95}] * 4) is None


def test_the_breakout_rule_is_given_the_history_it_needs(monkeypatch):
    """Filtering to the target session leaves 26 bars, so the first 16 have no
    level to break and the rest measure resistance over half a day. That is why
    the first gated run found 0 signals across 4 sessions: 355 of 361 bars came
    back 'no breakout'. Three days of context come back; only the target
    session is offered as entries."""
    bars = []
    for d in ("2026-09-02", "2026-09-03", "2026-09-04"):
        for i in range(26):
            mins = 9 * 60 + 30 + i * 15
            bars.append({"start_time": f"{d}T{mins//60:02d}:{mins%60:02d}:00",
                         "date": d, "open": 100.0, "high": 100.2, "low": 99.8,
                         "close": 100.0, "volume": 10, "closed": True})
    monkeypatch.setattr(regime.uw, "candles", lambda *a, **k: bars)
    got, todays = regime.session_bars("SPY", "2026-09-04")
    assert len(got) == 78                    # every bar is available as context
    assert len(todays) == 26                 # only the session is entryable
    assert todays[0] == 52                   # and it starts after two full days


# ── Sample size, and the alert cap ──────────────────────────────
def test_sessions_walk_back_over_weekends():
    """Four sessions cannot separate a rule from luck any better than 'three
    wins out of five' could. Hand-listing twenty dates is also how a date list
    quietly becomes a choice about which dates flatter the answer."""
    days = z.trading_days("2026-09-07", 6)      # a Monday
    assert days[0] == "2026-09-07"
    assert "2026-09-05" not in days and "2026-09-06" not in days   # weekend
    assert days == ["2026-09-07", "2026-09-04", "2026-09-03",
                    "2026-09-02", "2026-09-01", "2026-08-31"]


def test_the_alert_cap_is_a_volume_limit_not_a_quality_gate(monkeypatch):
    """THRESHOLD is what decides whether a setup is good enough; the scanner
    sorts by score and stops below it. The cap only truncates, so raising it
    stops discarding setups that qualified but arrived late in the day."""
    import importlib, os
    monkeypatch.setenv("MAX_ALERTS_PER_DAY", "30")
    importlib.reload(C)
    assert C.MAX_ALERTS_PER_DAY == 30
    monkeypatch.delenv("MAX_ALERTS_PER_DAY")
    importlib.reload(C)
    assert C.MAX_ALERTS_PER_DAY == 5
    assert C.THRESHOLD == 85          # unchanged: the cap is not the gate


# ── What the twenty-session run actually showed ─────────────────
def test_a_stop_can_fill_below_its_level():
    """The winning configuration in the 20-session run was the WIDEST pair,
    +40/-25 pooling to $1.079 against $0.975 for +25/-10. A wide stop only pays
    if the stop holds, so that result is the one most exposed to a 0DTE
    contract gapping through it — the assumption minute bars cannot check."""
    rows = [bar("10:00", 1.0, 1.0, 1.0, 1.0),
            bar("10:01", 1.0, 1.0, 0.70, 0.70)]
    clean = z.entry_exit(rows, 0, 40, 25, 15, 0.0, "15:30", slip_pct=0)
    slipped = z.entry_exit(rows, 0, 40, 25, 15, 0.0, "15:30", slip_pct=20)
    assert clean["why"] == slipped["why"] == "stop"
    assert clean["exit"] == 0.75
    assert slipped["exit"] < clean["exit"]


def test_slippage_never_fills_below_where_the_contract_traded():
    """The bar's low is the floor. Charging worse than the worst print of the
    minute would be inventing a loss."""
    rows = [bar("10:00", 1.0, 1.0, 1.0, 1.0),
            bar("10:01", 1.0, 1.0, 0.74, 0.74)]
    t = z.entry_exit(rows, 0, 40, 25, 15, 0.0, "15:30", slip_pct=90)
    assert t["exit"] == 0.74          # not 0.075


def test_a_tight_stop_is_triggered_by_noise_not_by_direction():
    """78-88% of trades stopped out at -10%. On a contract quoted 5% wide the
    stop sits inside the minute-to-minute bounce, so it fires whether or not
    the read was right. This is why 'small profit, no loss' measured worse."""
    noise = [bar("10:00", 1.0, 1.0, 1.0, 1.0)]
    noise += [bar(f"10:{m:02d}", 1.0, 1.02, 0.89, 1.0) for m in range(1, 6)]
    tight = z.entry_exit(noise, 0, 25, 10, 15, 0.0, "15:30")
    wide = z.entry_exit(noise, 0, 40, 25, 15, 0.0, "15:30")
    assert tight["why"] == "stop"          # the bounce alone takes it out
    assert wide["why"] == "timeout"        # the same tape leaves it alone
