"""Contract-level measurement: what a contract bought that day went on to be worth."""
import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import explosion


def row(date, ask, high=None, last=None, **kw):
    d = {"date": date, "ask": ask, "bid": max(0.0, ask - 0.02),
         "high": high if high is not None else ask,
         "low": ask, "last": last if last is not None else ask,
         "open": ask, "iv": 0.6, "iv_high": 0.7, "iv_low": 0.5,
         "open_interest": 1000, "volume": 500, "ask_volume": 400,
         "bid_volume": 100, "sweep_volume": 50, "premium": 1000.0}
    d.update(kw)
    return d


# The UBER 76C Salem sent: ~0.05, peaks at 0.87, ends at 0.01
UBER = ([row(f"2026-08-{d:02d}", 0.05) for d in range(1, 6)]
        + [row("2026-08-06", 0.20, high=0.35)]
        + [row("2026-08-07", 0.45, high=0.87)]
        + [row("2026-08-08", 0.30, high=0.45)]
        + [row("2026-08-09", 0.01, high=0.05, last=0.01)])


def test_it_finds_the_multiple_the_chart_shows():
    """0.05 into a 0.87 high is 17x — the trade Salem is asking to catch."""
    f = explosion.forward_multiple(UBER, 0, horizon=10)
    assert f["entry"] == 0.05
    assert f["peak_multiple"] == 17.4


def test_the_peak_is_not_the_result():
    """Same entry, held to the end of the window, is a near-total loss. This is
    the number that decides whether the strategy works, not the peak."""
    f = explosion.forward_multiple(UBER, 0, horizon=10)
    assert f["peak_multiple"] > 17
    assert f["end_multiple"] < 0.5


def test_days_to_peak_is_recorded():
    f = explosion.forward_multiple(UBER, 0, horizon=10)
    assert f["days_to_peak"] == 6          # the 0.87 bar


def test_a_short_horizon_misses_the_move():
    f = explosion.forward_multiple(UBER, 0, horizon=3)
    assert f["peak_multiple"] < 2          # nothing has happened yet


def test_entry_is_the_ask_not_the_last():
    rows = [row("2026-08-01", 0.10, last=0.06), row("2026-08-02", 0.10, high=0.50)]
    assert explosion.forward_multiple(rows, 0, 5)["entry"] == 0.10


def test_worthless_and_final_rows_are_skipped():
    assert explosion.forward_multiple([row("2026-08-01", 0.0)], 0, 5) is None
    assert explosion.forward_multiple(UBER, len(UBER) - 1, 5) is None


# ── Features must be visible on the entry day only ──────────────
def test_features_use_no_future_data():
    feats = explosion.features(UBER, 0, {"expiry": "2026-09-04"})
    assert feats["price"] == 0.05
    assert feats["ask_share"] == 0.8       # 400 of 500 lifted at the ask
    assert feats["vol_oi"] == 0.5
    assert feats["dte"] == 34
    assert set(feats) <= {"price", "iv", "vol_oi", "vol_vs_avg", "ask_share",
                          "sweep_share", "open_interest", "dte"}


def test_volume_against_its_own_recent_average():
    rows = [row(f"2026-08-{d:02d}", 0.05, volume=100) for d in range(1, 6)]
    rows.append(row("2026-08-06", 0.05, volume=1000))
    assert explosion.features(rows, 5, {})["vol_vs_avg"] == 10.0


def test_missing_expiry_gives_no_dte_rather_than_a_guess():
    assert explosion.features(UBER, 0, {})["dte"] is None


# ── Buckets ─────────────────────────────────────────────────────
def test_price_buckets_separate_lottery_tickets():
    assert explosion.bucket("price", 0.05) == "price=<0.10"
    assert explosion.bucket("price", 0.30) == "price=0.10-0.50"
    assert explosion.bucket("price", 2.00) == "price=1.50+"


def test_unknown_values_are_marked_not_bucketed():
    assert explosion.bucket("dte", None) == "dte=?"


# ── Summary ─────────────────────────────────────────────────────
def test_explosion_rate_and_the_gap_to_the_end_value():
    obs = [{"peak_multiple": 17.4, "end_multiple": 0.2, "days_to_peak": 6},
           {"peak_multiple": 1.1, "end_multiple": 0.4, "days_to_peak": 2},
           {"peak_multiple": 0.3, "end_multiple": 0.1, "days_to_peak": 1},
           {"peak_multiple": 6.0, "end_multiple": 3.0, "days_to_peak": 4}]
    s = explosion.summarise(obs, threshold=5.0)
    assert s["count"] == 4
    assert s["explosion_rate"] == 50.0     # two of four reached 5x
    assert s["median_end"] < s["median_peak"]


def test_empty_summary_is_safe():
    assert explosion.summarise([], 5.0) == {"count": 0}
