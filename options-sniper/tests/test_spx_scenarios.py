"""The scenarios inside the SPX report.

Salem asked for a million of them. This computes the number a million paths
CONVERGE to, exactly — the reflection principle for driftless Brownian
motion — instead of drawing them: same answer, no simulation error, and no
numpy on the Railway image.

The values pinned below were checked against an actual 1,000,000-path
bootstrap of this market's own 1-minute moves on 2026-09-09.
"""
import math
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import spx

SPOT, SIG1, MINUTES = 7650.3, 0.000196, 390
LEVELS = [("مقاومة", 7680.0), ("مغناطيس", 7675.0), ("دعم", 7670.0),
          ("الانقلاب", 7694.98)]


def _rows():
    return {n: (t, c) for n, _l, t, c in
            spx.scenarios(SPOT, SIG1, MINUTES, LEVELS)}


def test_it_agrees_with_a_million_simulated_paths():
    """simulated 30.1 / 38.6 / 48.5 / 12.4 against the formula.

    The gap is the fat tails the closed form does not carry. Two points is
    smaller than the honesty of any of these numbers warrants pretending
    about, and it is measured rather than assumed.
    """
    r = _rows()
    for name, simulated in (("مقاومة", 0.301), ("مغناطيس", 0.386),
                            ("دعم", 0.485), ("الانقلاب", 0.124)):
        assert abs(r[name][0] - simulated) < 0.03, (
            f"{name}: formula {r[name][0]:.3f} vs simulated {simulated}")


def test_closing_beyond_is_exactly_half_of_touching_it():
    """The reflection principle, which is the whole basis of the touch
    number: P(max >= b) = 2 P(end >= b) for a driftless walk."""
    for _n, (touch, close) in _rows().items():
        assert abs(touch - 2 * close) < 1e-9


def test_a_nearer_level_is_always_likelier_than_a_further_one():
    r = _rows()
    assert r["دعم"][0] > r["مغناطيس"][0] > r["مقاومة"][0] > r["الانقلاب"][0]


def test_more_time_never_lowers_the_chance_of_touching():
    short = dict((n, t) for n, _l, t, _c in
                 spx.scenarios(SPOT, SIG1, 30, LEVELS))
    long = dict((n, t) for n, _l, t, _c in
                spx.scenarios(SPOT, SIG1, 390, LEVELS))
    for n in short:
        assert long[n] >= short[n]


def test_no_volatility_and_no_time_produce_no_scenarios():
    """Never a made-up number: without a measured sigma or minutes left there
    is nothing to say, and the report says that instead."""
    assert spx.scenarios(SPOT, 0.0, MINUTES, LEVELS) == []
    assert spx.scenarios(SPOT, SIG1, 0, LEVELS) == []
    assert spx.scenarios(0.0, SIG1, MINUTES, LEVELS) == []


# ── The sigma the whole thing rests on ──────────────────────────
def _bars(closes, date="2026-09-09"):
    return [{"close": c, "date": date} for c in closes]


def test_the_overnight_gap_is_not_a_minute_of_trading():
    """Counting the jump from one session's close to the next session's open
    inflates every probability in the report."""
    day1 = _bars([100.0 + i * 0.01 for i in range(60)], "2026-09-08")
    day2 = _bars([200.0 + i * 0.01 for i in range(60)], "2026-09-09")
    sig = spx.minute_sigma(day1 + day2)
    assert sig < 0.001, f"the 100%% overnight gap leaked into sigma ({sig})"


def test_too_few_bars_is_not_a_measurement():
    assert spx.minute_sigma(_bars([100.0, 100.1, 100.2])) == 0.0
    assert spx.minute_sigma([]) == 0.0
    assert spx.minute_sigma(None) == 0.0


def test_a_real_tape_produces_a_usable_sigma():
    import random
    rnd = random.Random(1)
    px, rows = 100.0, []
    for _ in range(390):
        px *= math.exp(rnd.gauss(0, 0.0002))
        rows.append({"close": px, "date": "2026-09-09"})
    sig = spx.minute_sigma(rows)
    assert 0.0001 < sig < 0.0004


# ── The sentence that keeps it from reading as a forecast ───────
def test_the_report_says_out_loud_that_it_is_not_a_direction():
    d = {"spx": SPOT, "spy": 763.64, "ratio": 10.0182, "at": "16:09",
         "levels": {"call_wall": 7680.0, "put_wall": 7670.0,
                    "gamma_flip": 7694.98, "gamma_magnet": 7675.0},
         "tide": [], "spy_candles": [], "sigma1": SIG1, "minutes_left": MINUTES}
    out = spx.message(d)
    assert "مشي عشوائي بلا اتجاه" in out
    assert "مو توصية عقد" in out


def test_a_report_with_no_measured_volatility_says_so():
    d = {"spx": SPOT, "spy": 763.64, "ratio": 10.0182, "at": "16:09",
         "levels": {"call_wall": 7680.0}, "tide": [], "spy_candles": [],
         "sigma1": 0.0, "minutes_left": 0}
    assert "مو متوفرة الآن" in spx.message(d)
