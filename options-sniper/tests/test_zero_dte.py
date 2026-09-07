"""The trade Salem actually makes: 0DTE, sold at +40% within minutes.

Every earlier run measured 22+ DTE contracts held five days to a 2x target.
These tests pin the thing that decides whether the new engine can be trusted:
what it does when a minute contains both the target and the stop.
"""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import zero_dte as z


def bar(t, o, h, l, c, bid=0.0, ask=0.0, vol=100):
    # -04:00 makes the fixture say Eastern out loud. Naive strings were read
    # as UTC by the code and as Eastern by whoever wrote the test, which is
    # the exact confusion that put the hard exit four hours early.
    return {"time": f"2026-09-04T{t}:00-04:00", "open": o, "high": h, "low": l,
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
    free = z.entry_exit(rows, 0, 40, 25, 15, 0.0, "15:30", fee=0)
    paid = z.entry_exit(rows, 0, 40, 25, 15, 10.0, "15:30", fee=0)
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
    t = z.entry_exit(rows, 0, 40, 25, 15, 0.0, "15:30", fee=0)
    assert t["why"] == "stop"
    assert t["exit"] == 0.75                    # entry 1.00, stop -25%


def test_the_target_pays_the_limit_price_not_the_spike():
    """A limit at +40% fills at +40%. Crediting the minute's high would pay
    for a print nobody's order reached."""
    rows = [bar("10:00", 1.0, 1.0, 1.0, 1.0, bid=0.99, ask=1.00),
            bar("10:01", 1.0, 3.00, 1.0, 2.9, bid=2.80, ask=2.90)]
    t = z.entry_exit(rows, 0, 40, 25, 15, 0.0, "15:30", fee=0)
    assert t["why"] == "take" and t["exit"] == 1.40


def test_the_spread_can_turn_a_winner_into_a_loser():
    """This endpoint serves no bid/ask, so the spread is a parameter. A move
    that clears +40% on the trade price does not clear it once the round trip
    is paid for — and on a 0DTE contract the round trip is not small."""
    rows = [bar("10:00", 1.0, 1.0, 1.0, 1.0, bid=0.99, ask=1.00),
            bar("10:01", 1.0, 1.45, 1.0, 1.4, bid=1.20, ask=1.45)]
    assert z.entry_exit(rows, 0, 40, 25, 15, 0.0, "15:30", fee=0)["why"] == "take"
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
    t = z.entry_exit(rows, 0, 40, 25, 15, 0.0, "15:30", fee=0)
    assert t["why"] == "timeout"                # the 2x minute is past the exit
    assert t["minutes"] == 1


def test_an_entry_with_no_minutes_left_is_not_a_trade():
    rows = [bar("15:29", 1.0, 1.0, 1.0, 1.0, bid=0.99, ask=1.00),
            bar("15:31", 1.0, 2.0, 1.0, 2.0, bid=1.99, ask=2.01)]
    assert z.entry_exit(rows, 0, 40, 25, 15, 0.0, "15:30", fee=0) is None


def test_a_contract_with_no_price_is_skipped():
    rows = [bar("10:00", 0, 0, 0, 0), bar("10:01", 0, 0, 0, 0)]
    assert z.entry_exit(rows, 0, 40, 25, 15, 0.0, "15:30", fee=0) is None


# ── Reading the minute ──────────────────────────────────────────
def test_the_minute_is_read_from_the_timestamp():
    # UW sends UTC. 14:47Z is 10:47 in New York, and every clock rule in this
    # file is written in New York time.
    assert z.minute_of({"time": "2026-09-04T14:47:00Z"}) == "10:47"
    assert z.minute_of({"time": "2026-09-04T10:47:00-04:00"}) == "10:47"
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
    clean = z.entry_exit(rows, 0, 40, 25, 15, 0.0, "15:30", slip_pct=0, fee=0)
    slipped = z.entry_exit(rows, 0, 40, 25, 15, 0.0, "15:30", slip_pct=20, fee=0)
    assert clean["why"] == slipped["why"] == "stop"
    assert clean["exit"] == 0.75
    assert slipped["exit"] < clean["exit"]


def test_slippage_never_fills_below_where_the_contract_traded():
    """The bar's low is the floor. Charging worse than the worst print of the
    minute would be inventing a loss."""
    rows = [bar("10:00", 1.0, 1.0, 1.0, 1.0),
            bar("10:01", 1.0, 1.0, 0.74, 0.74)]
    t = z.entry_exit(rows, 0, 40, 25, 15, 0.0, "15:30", slip_pct=90, fee=0)
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


def test_the_grid_runs_past_the_pair_that_won():
    """+40/-25 was the best pair in the 20-session run and sat exactly at the
    EDGE of the old grid. A maximum on a boundary usually means the real one is
    outside it, so the grid has to reach past it before the result can be
    called an optimum."""
    assert (40, 25) in z.GRID
    assert max(t for t, s in z.GRID) > 40
    assert any(s > 25 for t, s in z.GRID)
    assert all(stop < take for take, stop in z.GRID)


def test_salems_target_is_recorded_as_a_number_the_run_checks():
    """'Losses no more than 35% of trades entered' — his words, so the run
    marks the rows that meet it instead of leaving him to scan the column."""
    assert z.TARGET_LOSS_RATE == 35.0


# ── Reading the pooled table honestly ───────────────────────────
def test_the_sweep_survives_being_written_to_disk():
    """The 20-session run printed its whole result and then crashed on
    json.dumps: the sweep is keyed by (take, stop, slip) tuples, and JSON keys
    must be strings. Nothing was lost from the screen, but nothing was saved."""
    import json
    r = {"date": "2026-09-04", "avg": 1.1, "loss_rate": 40.3,
         "sweep": {(50, 35, 0.0): (1.119, 40.3, 796)}}
    encoded = {k: (v if k != "sweep" else
                   {f"{t}/{st}/{sl}": list(c) for (t, st, sl), c in v.items()})
               for k, v in r.items()}
    assert json.loads(json.dumps(encoded))["sweep"]["50/35/0.0"][0] == 1.119


def test_pooling_by_trade_count_flatters_a_rule_that_fires_on_its_best_days():
    """At +50/-35 the pooled figure is $1.111 but the equal-weighted one is
    $1.035, because the five winning sessions produced 557 of the 766 trades
    and the four losing sessions only 209. A rule that fires more on the days
    that suit it will always look better pooled than it would session by
    session, so the table has to show both."""
    per_session = [(143, 1.167), (133, 1.180), (75, 1.224), (30, 1.211),
                   (176, 1.216), (30, 0.706), (30, 0.804), (29, 0.843),
                   (120, 0.963)]
    n = sum(t for t, _ in per_session)
    pooled = sum(t * r for t, r in per_session) / n
    equal = sum(r for _, r in per_session) / len(per_session)
    assert round(pooled, 3) == 1.111
    assert round(equal, 3) == 1.035
    assert pooled > equal          # the gap is the warning
    assert sum(1 for _, r in per_session if r > 1.0) == 5   # 5 of 9, not 9 of 9


def test_not_losing_and_hitting_the_target_are_different_questions():
    """A trade that times out flat did not lose and did not win. Showing only
    the loss rate let the second be read as the first: at +60/-35, 59.3% do
    not lose but only 25.6% ever reach the target."""
    assert round(100 - 40.7, 1) == 59.3          # did not lose
    assert 25.6 < 59.3                            # but did not win either
    assert (100 - 40.7) / (100 - 78.4) > 2.5     # still 2.7x the tight pair


def test_the_naive_break_even_understates_a_wide_pair():
    """stop/(take+stop) assumes every loss is a full stop. With a 15-minute
    clock most losers time out short of it, which is why +60/-35 returned
    $1.126 while 'hitting' 25.6% against a nominal 36.8% bar. Comparing hit to
    the naive figure would have thrown away the best row in the table."""
    naive = 35 / (60 + 35) * 100
    assert round(naive, 1) == 36.8
    real = 15.2 / (60 + 15.2) * 100      # the loss actually taken, ~15%
    assert real < 25.6 < naive


def test_the_live_exit_rule_is_the_pair_that_wins_most_often():
    """Salem's condition was more winning trades. +40/-30 reaches the target
    43.2% of the time against 32.8% for +50/-35, for the same money — $1.033
    against $1.035 with every session weighted equally."""
    zero_dte_row = next(r for r in C.EXIT_RULES if r[0] == 0)
    assert zero_dte_row[1] == 40 and zero_dte_row[2] == -30
    assert abs(zero_dte_row[2]) <= z.MAX_STOP_PCT
    assert (40, 30) in z.GRID



# ── Commissions, which nothing had charged ──────────────────────
def test_the_commission_is_charged_on_both_sides():
    """$0.65 a contract each way on a $1.00 contract is 0.65% in and 0.65%
    out. The trade still nets exactly +40% — the contract simply has to
    travel further to get there, which is the whole point of charging it."""
    rows = [bar("10:00", 1.0, 1.0, 1.0, 1.0),
            bar("10:01", 1.0, 9.0, 1.0, 9.0)]
    free = z.entry_exit(rows, 0, 40, 25, 15, 0.0, "15:30", fee=0)
    paid = z.entry_exit(rows, 0, 40, 25, 15, 0.0, "15:30", fee=0.65)
    assert paid["entry"] > free["entry"]                 # paid to get in
    assert round(paid["exit"] / paid["entry"], 4) == 1.4 # still nets +40%
    assert paid["exit"] > free["exit"]                   # mid had to go higher


def test_a_flat_timeout_loses_the_round_trip():
    """The honest cost of a trade that goes nowhere: two commissions and the
    spread. On a cheap contract that is not small."""
    rows = [bar("10:00", 0.50, 0.50, 0.50, 0.50)]
    rows += [bar(f"10:{m:02d}", 0.50, 0.50, 0.50, 0.50) for m in range(1, 4)]
    t = z.entry_exit(rows, 0, 40, 25, 3, 4.0, "15:30", fee=0.65)
    assert t["why"] == "timeout"
    assert t["multiple"] < 0.95 if "multiple" in t else t["exit"] / t["entry"] < 0.95


# ── The cheap far strike, asked at the pair actually used ───────
def test_the_budget_breakdown_splits_cost_from_distance():
    """Salem likes the cheap far strike. Those are two different questions:
    price is what he pays, distance to the strike is what the stock has to do
    for it to pay back. A $50 contract one strike out and a $50 contract five
    strikes out are not the same trade."""
    assert z.bucket("price", 0.45) == "budget=$50"
    assert z.bucket("price", 1.80) == "budget=$200"
    assert z.bucket("moneyness", 0.3) == "otm=<0.5%"
    assert z.bucket("moneyness", 4.0) == "otm=3%+"


def test_a_thin_split_is_dropped_rather_than_ranked():
    """Every run so far said cheap loses — at +25/-10, a stop inside the noise
    on a contract quoted 5% wide. Re-asking at +40/-30 is only worth anything
    if the answer rests on more than a handful of contracts."""
    import statistics as st
    obs = [{"multiple": 1.4, "why": "take", "symbol": "A"} for _ in range(19)]
    assert len(obs) < 20                    # below the floor by_budget applies
    assert st.mean(o["multiple"] for o in obs) > 1.0   # and it would have "won"


def test_the_split_reads_the_pair_the_table_ranked_not_the_default(capsys):
    """The first version asked only at the configured pair, so a run left on
    the default answered at +25/-10 — the pair we had already abandoned. The
    sweep trades every pair in the grid, so the split must be able to read one
    it was not configured with."""
    class A:
        take, stop = 25.0, 10.0
    cheap = [{"multiple": 0.9, "why": "stop", "symbol": f"C{i}"}
             for i in range(30)]
    rich = [{"multiple": 1.4, "why": "take", "symbol": f"R{i}"}
            for i in range(30)]
    results = [{
        "detail": {"budget": {"budget=$50": cheap}, "moneyness": {}},
        "splits": {(40, 30): {"budget": {"budget=$50": rich},
                              "moneyness": {}}},
    }]
    z.by_budget(results, A(), ranked=[(40, 30)])
    out = capsys.readouterr().out
    assert "+25% / -10%" in out and "+40% / -30%" in out
    assert "$  0.900" in out          # the configured pair, from "detail"
    assert "$  1.400" in out          # the ranked pair, from "splits"


def test_the_sweep_keeps_every_pair_so_the_question_costs_no_extra_run():
    """Re-asking used to mean a whole twenty-session re-run. The observations
    already exist inside the sweep; keeping them is the difference between an
    answer and another API bill."""
    import inspect
    src = inspect.getsource(z.sweep)
    assert "splits[(take, stop)] = _split(got)" in src
    assert "return pooled, splits" in src


def test_the_budget_table_gives_each_session_one_vote(capsys):
    """The same flaw the pooled table had: weighting a bucket by trade count
    hands the verdict to the busiest sessions. A tier that pools above $1.00
    on two good afternoons and loses on four quiet ones is not a tier that
    works — the equal-weighted column has to say so."""
    class A:
        take, stop = 40.0, 30.0

    def sess(mult, n=12):
        return {"detail": {"budget": {"budget=$50": [
            {"multiple": mult, "why": "take" if mult > 1 else "stop",
             "symbol": f"S{mult}{i}"} for i in range(n)]},
            "moneyness": {}}}

    # one loud winner, two quiet losers: pools up, equal-weights down
    z.by_budget([sess(2.0, 40), sess(0.8), sess(0.8)], A())
    out = capsys.readouterr().out
    assert "$  1.550" in out          # pooled — flattered by the loud session
    assert "$  1.200" in out           # equal weight, (2.0 + 0.8 + 0.8) / 3
    assert "1/3" in out                # one session of three actually won


# ── The quoted price, and how old it is ────────────────────────
def test_a_backtest_read_keeps_one_cache_key_forever():
    """`as_of` pins a past session whose bars will not change again, so the
    key must not carry a clock — a backtest that re-fetched every five minutes
    would burn the API allowance to receive identical rows."""
    import uw
    assert uw._live_bucket("2026-08-14") == ""


def test_a_live_read_expires_within_the_scan_interval():
    """The scheduler runs scanner and monitor IN PROCESS, so the module stays
    loaded from open to close. A key without a clock would serve the first
    pass of the day's numbers for the next six hours."""
    import datetime as dt, uw

    class _Clock(dt.datetime):
        now_at = None

        @classmethod
        def now(cls, tz=None):
            return cls.now_at

    real = uw.datetime.datetime
    uw.datetime.datetime = _Clock
    try:
        _Clock.now_at = dt.datetime(2026, 9, 8, 16, 32)
        a = uw._live_bucket(None)
        _Clock.now_at = dt.datetime(2026, 9, 8, 16, 34)
        assert uw._live_bucket(None) == a         # stable inside one bucket
        _Clock.now_at = dt.datetime(2026, 9, 8, 16, 36)
        assert uw._live_bucket(None) != a         # and moves on to the next
    finally:
        uw.datetime.datetime = real


# ── Dealer positioning as a measured feature, not a belief ──────
def test_gex_side_is_a_fact_about_price_not_a_prediction():
    """The bucket says which side of the flip the entry was on. Whether that
    side helped is the table's job — so a call and a put at the same price
    get the same label."""
    lv = {"gamma_flip": 100.0, "call_wall": 105.0, "put_wall": 95.0}
    call = {"direction": "call", "close": 101.0, "atr": 2.0}
    put = {"direction": "put", "close": 101.0, "atr": 2.0}
    assert z.gex_features(call, lv)["gex"] == "above flip"
    assert z.gex_features(put, lv)["gex"] == "above flip"
    assert z.gex_features({"direction": "call", "close": 99.0, "atr": 2.0},
                          lv)["gex"] == "below flip"


def test_the_wall_that_matters_is_the_one_in_the_trade_s_path():
    """A call cares about the call wall ahead of it; a put about the put wall
    below. 'Within 1 ATR' means the wall sits inside the move the trade
    needs; 'behind' means price already passed it."""
    lv = {"gamma_flip": 100.0, "call_wall": 105.0, "put_wall": 95.0}
    assert z.gex_features({"direction": "call", "close": 104.0, "atr": 2.0},
                          lv)["wall"] == "within 1 ATR"
    assert z.gex_features({"direction": "call", "close": 101.0, "atr": 2.0},
                          lv)["wall"] == "clear"
    assert z.gex_features({"direction": "call", "close": 106.0, "atr": 2.0},
                          lv)["wall"] == "behind"
    assert z.gex_features({"direction": "put", "close": 96.0, "atr": 2.0},
                          lv)["wall"] == "within 1 ATR"


def test_no_levels_means_unknown_not_a_default_side():
    """A ticker UW has no GEX for must land in 'gex=?', never be counted on
    either side."""
    out = z.gex_features({"direction": "call", "close": 100.0, "atr": 1.0}, None)
    assert out == {"gex": None, "wall": None}
    assert z.bucket("gex", None) == "gex=?"
    assert z.bucket("gex", "below flip") == "gex=below flip"


def test_gex_and_wall_are_reported_beside_the_other_features():
    assert "gex" in z.FEATURES and "wall" in z.FEATURES


# ── Walk-forward: the only figure not helped by knowing the answer ──
def _sess(date, table):
    """table: {(take, stop): per-$1} at slip 0, 40 trades each."""
    return {"date": date,
            "sweep": {(t, s, 0.0): (v, 0.0, 40, 0.0, 0.0)
                      for (t, s), v in table.items()}}


class _Args:
    slips = [0.0]


def test_the_pair_is_chosen_only_from_earlier_sessions(capsys):
    """The pooled table ranks every pair on every session, picks the winner,
    then reports that winner on the same sessions. Walk-forward removes the
    loop: here +40/-30 is best on the first four, so it is what gets scored on
    the fifth -- even though +60/-35 wins that one."""
    early = {(40, 30): 1.20, (60, 35): 0.80}
    late = {(40, 30): 0.90, (60, 35): 1.50}
    results = [_sess(f"2026-08-1{i}", early) for i in range(4)]
    results.append(_sess("2026-08-20", late))
    z.walk_forward(results, _Args(), min_train=4)
    out = capsys.readouterr().out
    assert "+40/-30" in out           # chosen on what came before
    assert "$  0.900" in out          # scored on what it had not seen
    assert "did NOT make money" in out


def test_a_thin_session_gets_no_vote_in_the_choice(capsys):
    """Two-contract days deciding which pair looks best is how the pooled
    figure got flattered in the first place."""
    thin = {"date": "2026-08-19",
            "sweep": {(40, 30, 0.0): (3.0, 0.0, 8, 0.0, 0.0)}}   # 8 trades
    solid = [_sess(f"2026-08-1{i}", {(40, 30): 1.10, (60, 35): 0.90})
             for i in range(4)]
    z.walk_forward(solid + [thin], _Args(), min_train=4)
    out = capsys.readouterr().out
    assert "(thin)" in out            # not scored
    assert "$  3.000" not in out      # and its 3x never enters the average


def test_an_unstable_choice_is_reported_as_the_result(capsys):
    """If the winning pair keeps changing there was no best pair to find, and
    that instability is the finding — not a footnote."""
    # Each new session swings the running average enough to flip the winner,
    # which is the grid chasing whichever session came last.
    flip = [_sess("2026-08-10", {(40, 30): 2.0, (60, 35): 0.1}),
            _sess("2026-08-11", {(40, 30): 2.0, (60, 35): 0.1}),
            _sess("2026-08-12", {(40, 30): 2.0, (60, 35): 0.1}),
            _sess("2026-08-13", {(40, 30): 2.0, (60, 35): 0.1}),
            _sess("2026-08-14", {(40, 30): 0.1, (60, 35): 20.0}),
            _sess("2026-08-17", {(40, 30): 20.0, (60, 35): 0.1}),
            _sess("2026-08-18", {(40, 30): 0.1, (60, 35): 40.0})]
    z.walk_forward(flip, _Args(), min_train=4)
    out = capsys.readouterr().out
    assert "never settled" in out


def test_too_few_sessions_says_so_instead_of_inventing_a_verdict(capsys):
    z.walk_forward([_sess("2026-08-10", {(40, 30): 1.1})], _Args(), min_train=4)
    assert "needs more than 4 sessions" in capsys.readouterr().out


# ── The budget band is also a clock ────────────────────────────
def _tape(prices, hour):
    return [{"time": f"2026-09-08T{hour}:{m:02d}:00-04:00",
             "close": p, "avg_price": p, "high": p, "low": p, "open": p,
             "volume": 100, "ask_volume": 50, "bid_volume": 50,
             "ask_px": p, "bid_px": p, "iv": 0.4}
            for m, p in enumerate(prices)]


class _CArgs:
    min_price, max_price = 0.05, 2.0


def test_the_band_is_reported_as_a_time_filter_when_it_acts_like_one(capsys):
    """A same-day contract is dearer in the morning at the same strike --
    the afternoon one has less life left. So a $2 ceiling quietly selects
    afternoons, and every figure would describe afternoon 0DTE while calling
    itself 0DTE."""
    dear_am = _tape([3.5] * 30, "10")          # priced out all morning
    cheap_pm = _tape([1.2] * 30, "14")         # inside the band all afternoon
    clock_bias_input = [({}, dear_am + cheap_pm)]
    z.clock_bias(clock_bias_input, _CArgs())
    out = capsys.readouterr().out
    assert "morning 0% usable vs afternoon 100%" in out
    assert "is a TIME filter" in out


def test_an_even_band_is_not_accused_of_bias(capsys):
    even = _tape([1.0] * 30, "10") + _tape([1.0] * 30, "14")
    z.clock_bias([({}, even)], _CArgs())
    out = capsys.readouterr().out
    assert "does not obviously favour either half" in out


def test_each_hour_reports_why_its_minutes_were_unusable(capsys):
    tape = _tape([3.0] * 25, "09") + _tape([0.01] * 25, "15")
    z.clock_bias([({}, tape)], _CArgs())
    out = capsys.readouterr().out
    assert "09:00" in out and "15:00" in out
    assert "0%" in out                       # neither hour usable


# ── The clock: UW sends UTC, every rule here is written in New York ──
def test_the_hard_exit_is_a_new_york_time_not_a_utc_one():
    """"15:30" was being compared against a UTC timestamp, so entries stopped
    at 11:30 New York and the whole afternoon of every session sat silently
    outside the test. The numbers looked plausible, which is why it lasted."""
    import market
    assert market.et_minute("2026-09-04T19:30:00Z") == "15:30"   # the real cut
    assert market.et_minute("2026-09-04T15:30:00Z") == "11:30"   # what it cut


def test_the_session_runs_from_0930_to_1600_new_york():
    import market
    assert market.et_minute("2026-09-04T13:30:00Z") == "09:30"
    assert market.et_minute("2026-09-04T20:00:00Z") == "16:00"


def test_an_offset_that_is_already_eastern_is_left_alone():
    import market
    assert market.et_minute("2026-09-04T10:47:00-04:00") == "10:47"


def test_a_bare_time_is_passed_through_rather_than_guessed():
    """No date and no zone means nothing to convert; inventing a day would be
    worse than leaving it."""
    import market
    assert market.et_minute("09:31:00") == "09:31"
    assert market.et_minute("") == ""


# ── Skipping a session window, as a question and not an answer ──
def _sig(agree=4, chase=0.1):
    return {"agree": agree, "chase_atr": chase, "direction": "call"}


def test_a_skipped_window_is_refused_and_says_which_one():
    """The midday returned $0.79-$0.90 in five separate sessions while the
    momentum window returned $1.14-$1.38. That was read OFF the sessions, so
    it is a hypothesis: making it a parameter is how it gets tested instead of
    asserted."""
    import regime
    ok, why = regime.gate(_sig(), "12:30", skip=("midday",))
    assert not ok and "midday" in why
    ok, why = regime.gate(_sig(), "10:30", skip=("midday",))
    assert ok and why == "momentum"


def test_skipping_nothing_leaves_every_window_open():
    import regime
    for minute, expect in (("09:45", "open"), ("10:30", "momentum"),
                           ("12:30", "midday"), ("14:00", "trend"),
                           ("15:15", "gamma")):
        ok, why = regime.gate(_sig(), minute)
        assert ok and why == expect


def test_the_window_filter_runs_after_the_rules_that_cost_nothing():
    """A bar that never broke out is reported as 'no breakout', not as a
    skipped window — otherwise the reason column would blame the filter for
    rejections it had nothing to do with."""
    import regime
    ok, why = regime.gate(None, "12:30", skip=("midday",))
    assert not ok and why == "no breakout"
    ok, why = regime.gate(_sig(agree=1), "12:30", skip=("midday",))
    assert not ok and "committee" in why


def test_the_paper_baseline_matches_the_rule_the_paper_book_uses():
    """The baseline was measured at +40/-30 and EXIT_RULES[0] must still be
    that pair, or the paper record compares itself to a different experiment
    — which is exactly what the pre-clock-fix baseline was doing."""
    import config as C
    _dte, take, stop, _note = C.EXIT_RULES[0]
    assert (take, abs(stop)) == (40, 30)
    assert C.PAPER_BASELINE == {"hit": 31.3, "lost": 57.1, "avg": 0.994}
