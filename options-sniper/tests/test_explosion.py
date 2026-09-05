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
    """0.05 into a 0.87 print is the 17x Salem is asking to catch — but only
    at the print. Selling into the bid that day gets about half of it, and
    that gap is the difference between the chart and a fill."""
    printed = explosion.forward_multiple(UBER, 0, horizon=10, model="high")
    assert printed["entry"] == 0.05
    assert printed["peak_multiple"] == 17.4

    fillable = explosion.forward_multiple(UBER, 0, horizon=10)
    assert 8.0 < fillable["peak_multiple"] < 17.4


def test_the_peak_is_not_the_result():
    """Same entry, held to the end of the window, is a near-total loss. This is
    the number that decides whether the strategy works, not the peak."""
    f = explosion.forward_multiple(UBER, 0, horizon=10)
    assert f["peak_multiple"] > 8
    assert f["end_multiple"] < 0.5


def test_days_to_peak_is_recorded():
    f = explosion.forward_multiple(UBER, 0, horizon=10)
    assert f["days_to_peak"] == 6          # the 0.87 bar


def test_a_short_horizon_misses_the_move():
    f = explosion.forward_multiple(UBER, 0, horizon=3)
    assert f["peak_multiple"] < 2          # nothing has happened yet


def test_entry_prices_by_the_fill_model_not_the_last_trade():
    """`last` is where somebody else traded. What you pay is the ask, or a
    tick-rounded mid if the book allows one."""
    rows = [row("2026-08-01", 0.10, last=0.06, bid=0.08),
            row("2026-08-02", 0.10, high=0.50, bid=0.08)]
    assert explosion.forward_multiple(rows, 0, 5, model="bid")["entry"] == 0.10
    assert explosion.forward_multiple(rows, 0, 5, model="mid")["entry"] == 0.09


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
                          "sweep_share", "open_interest", "dte", "spread_pct"}


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


# ── The screener speaks a different dialect from the chain endpoints ──
import uw


SCREENER_ROW = {
    "option_symbol": "TSLA230908C00255000", "option_type": "call",
    "expiry": "2023-09-08", "strike": "255.0",
    "close": "0.03", "high": "2.95", "low": "0.02", "open": "0.92",
    "chain_prev_close": "1.29", "volume": 264899, "open_interest": 18680,
    "ask_side_volume": 119403, "bid_side_volume": 122789,
    "sweep_volume": 18260, "premium": "27723806.00", "stock_price": "247.94",
    "next_earnings_date": "2023-10-18", "sector": "Consumer Cyclical",
}


def test_screener_rows_carry_a_usable_price():
    """The screener reports `close`, not an NBBO pair. Reading it with the
    chain normaliser produced ask=0 on every row, so a $0.02-$1.00 filter
    rejected the whole result and the run blamed the price band."""
    n = uw._normalise_screener_row(SCREENER_ROW)
    assert n["price"] == 0.03
    assert n["type"] == "call" and n["strike"] == 255.0


def test_the_chain_normaliser_would_have_zeroed_it():
    assert uw._normalise_contract(SCREENER_ROW)["ask"] == 0.0


def test_screener_side_volumes_are_mapped():
    """ask_side_volume, not ask_volume — the ask share is the whole point."""
    n = uw._normalise_screener_row(SCREENER_ROW)
    assert n["ask_volume"] == 119403 and n["bid_volume"] == 122789
    assert n["sweep_volume"] == 18260


def test_screener_carries_context_the_chain_does_not():
    n = uw._normalise_screener_row(SCREENER_ROW)
    assert n["stock_price"] == 247.94
    assert n["next_earnings_date"] == "2023-10-18"


def test_symbol_fills_in_missing_fields():
    n = uw._normalise_screener_row({"option_symbol": "UBER260904C00076000"})
    assert n["strike"] == 76.0 and n["type"] == "call"
    assert n["expiry"] == "2026-09-04"


def test_screener_failure_raises_rather_than_returning_empty(monkeypatch):
    """An empty list from a plan that does not serve the endpoint is
    indistinguishable from an empty result, and the caller cannot say which."""
    def denied(path, params=None, retries=3):
        raise uw.UWError(f"{path}: auth failed (403)")
    monkeypatch.setattr(uw, "_get", denied)
    try:
        uw.screen_contracts(is_otm="true")
    except uw.UWError as e:
        assert "403" in str(e)
    else:
        raise AssertionError("should have raised")


# ── Touch rate is not profit ────────────────────────────────────
def test_realised_credits_the_target_on_a_touch():
    o = {"peak_multiple": 17.4, "end_multiple": 0.2}
    assert explosion.realised(o, 5.0) == 5.0     # sold on the way up


def test_realised_falls_back_to_the_end_value():
    """The 69% that never touch do not return zero — they return whatever the
    contract was worth at the end, which is the half of the arithmetic the
    peak columns leave out."""
    o = {"peak_multiple": 1.1, "end_multiple": 0.4}
    assert explosion.realised(o, 5.0) == 0.4


def test_a_high_touch_rate_can_still_lose_money():
    """Nine near-total losses against one 5x is a 10% touch rate and a losing
    bucket. Ranking by hit rate alone would call it a find."""
    obs = [{"peak_multiple": 6.0, "end_multiple": 5.0, "days_to_peak": 3}] + \
          [{"peak_multiple": 0.9, "end_multiple": 0.05, "days_to_peak": 1}] * 9
    s = explosion.summarise(obs, 5.0)
    assert s["explosion_rate"] == 10.0
    assert s["realised_avg"] < 1.0               # $0.545 per $1


def test_a_lower_touch_rate_can_win():
    obs = [{"peak_multiple": 6.0, "end_multiple": 5.0, "days_to_peak": 3}] * 3 + \
          [{"peak_multiple": 0.9, "end_multiple": 0.6, "days_to_peak": 1}] * 7
    s = explosion.summarise(obs, 5.0)
    assert s["explosion_rate"] == 30.0
    assert s["realised_avg"] > 1.0


# ── Pairs, because marginals cannot answer what they raise ──────
def _o(price, dte, peak, end):
    return {"peak_multiple": peak, "end_multiple": end, "days_to_peak": 2,
            "features": {"price": price, "dte": dte}}


def test_pairs_find_the_intersection():
    """Cheap explodes and short-dated explodes; only the pair says whether
    cheap AND short-dated does."""
    obs = ([_o(0.05, 5, 8.0, 6.0)] * 25 +      # cheap + short: runs
           [_o(0.05, 30, 0.5, 0.2)] * 25 +     # cheap + long: dies
           [_o(0.80, 5, 0.6, 0.3)] * 25)       # dear + short: dies
    pairs = dict(explosion.combinations(obs, ["price", "dte"], 5.0, 20))
    best = max(pairs.items(), key=lambda kv: kv[1]["realised_avg"])
    assert "price=<0.10" in best[0] and "dte=3-7" in best[0]
    assert best[1]["explosion_rate"] == 100.0


def test_thin_pairs_are_excluded():
    obs = [_o(0.05, 5, 8.0, 6.0)] * 3
    assert explosion.combinations(obs, ["price", "dte"], 5.0, 20) == []


# ── Realistic fills: you sell into the bid, not the print ───────
# $0.02 contract quoted 0.01 x 0.02 — the spread is half the price
PENNY = [row("d1", 0.02, high=0.02, last=0.02, bid=0.01),
         row("d2", 0.06, high=0.08, last=0.06, bid=0.05),
         row("d3", 0.11, high=0.12, last=0.11, bid=0.10),
         row("d4", 0.03, high=0.11, last=0.03, bid=0.02)]


def test_the_three_fill_models_are_ordered():
    hi = explosion.forward_multiple(PENNY, 0, 10, model="high")
    mid = explosion.forward_multiple(PENNY, 0, 10, model="mid")
    bid = explosion.forward_multiple(PENNY, 0, 10, model="bid")
    assert hi["peak_multiple"] >= mid["peak_multiple"] >= bid["peak_multiple"]


def test_a_mid_off_the_tick_grid_is_not_available():
    """Quoted 0.01 x 0.02 the mid is 0.015, and no exchange takes that order.
    This is why a penny contract gains nothing from limit orders — the very
    contracts the first result called profitable."""
    assert explosion.fill_price(0.01, 0.02, "mid", buying=True) == 0.02
    assert explosion.fill_price(0.01, 0.02, "mid", buying=False) == 0.01


def test_a_real_book_does_gain_from_the_mid():
    assert explosion.fill_price(1.90, 1.98, "mid", buying=True) == 1.94
    assert explosion.fill_price(1.90, 1.98, "mid", buying=False) == 1.94


def test_the_tick_widens_above_three_dollars():
    assert explosion.tick(0.50) == 0.01
    assert explosion.tick(5.00) == 0.05
    assert explosion.fill_price(4.90, 5.10, "mid", buying=True) == 5.00


def test_the_spread_at_entry_is_recorded():
    """On a $0.02 contract the spread is 50% of the price — the single fact
    the multiple-based result hides."""
    assert explosion.forward_multiple(PENNY, 0, 10)["spread_pct"] == 50.0


def test_spread_is_a_feature_you_can_filter_on():
    assert "spread_pct" in explosion.FEATURES
    assert explosion.bucket("spread_pct", 50.0) == "spread=50%+"
    assert explosion.bucket("spread_pct", 5.0) == "spread=<10%"


def test_entries_with_no_bid_are_not_tradeable(monkeypatch):
    """A contract quoted 0.00 x 0.02 cannot be sold at all, so buying it is
    not an entry the backtest should count."""
    rows = [dict(r, bid=0.0) for r in PENNY]
    monkeypatch.setattr(explosion.uw, "contract_history", lambda *a, **k: rows)
    assert explosion.scan_contract("X", {}, 10, 0.01, 1.0, model="bid") == []
    assert explosion.scan_contract("X", {}, 10, 0.01, 1.0, model="high") != []


def test_wide_spreads_can_be_excluded(monkeypatch):
    """Every day is a candidate entry, so the filter is per-entry: the $0.02
    day is 50% wide and drops out, the $0.06 day is 17% and stays."""
    monkeypatch.setattr(explosion.uw, "contract_history", lambda *a, **k: PENNY)
    tight = explosion.scan_contract("X", {}, 10, 0.01, 1.0, max_spread_pct=25)
    loose = explosion.scan_contract("X", {}, 10, 0.01, 1.0, max_spread_pct=60)
    assert len(tight) < len(loose)
    assert all(o["spread_pct"] <= 25 for o in tight)
    assert 50.0 in [o["spread_pct"] for o in loose]      # the penny entry


def test_five_x_is_a_smaller_move_on_a_cheap_contract():
    """The reason the result favours cheap contracts almost tautologically:
    5x on $0.02 is eight cents, inside the spread; on $1.00 it is four
    dollars, a real move in the underlying."""
    assert round(0.02 * 5 - 0.02, 2) == 0.08
    assert round(1.00 * 5 - 1.00, 2) == 4.00


# ── Entry days are not independent observations ─────────────────
def _e(symbol, peak, end):
    return {"symbol": symbol, "peak_multiple": peak, "end_multiple": end,
            "days_to_peak": 3, "features": {}}


def test_distinct_contracts_are_counted():
    """A 10-day window starting Monday shares nine days with Tuesday's, so one
    contract that ran contributes a win on every entry day it had. n=13 from
    one contract is one event."""
    obs = [_e("BOOM", 8.0, 6.0)] * 13
    s = explosion.summarise(obs, 5.0)
    assert s["count"] == 13
    assert s["contracts"] == 1              # the sample size that matters


def test_thirteen_contracts_are_thirteen_contracts():
    obs = [_e(f"D{i}", 0.6, 0.4) for i in range(13)]
    assert explosion.summarise(obs, 5.0)["contracts"] == 13


def test_one_explosion_can_carry_a_bucket():
    """13 losing contracts and one winner counted 13 times reads as $2.70 per
    dollar. Per contract it is much closer to break-even."""
    obs = [_e("BOOM", 8.0, 6.0)] * 13 + [_e(f"D{i}", 0.6, 0.4) for i in range(13)]
    s = explosion.summarise(obs, 5.0)
    assert s["realised_avg"] > 2.5
    assert s["per_contract_avg"] < s["realised_avg"]
    assert s["contracts"] == 14


def test_per_contract_average_gives_each_contract_one_vote():
    obs = [_e("A", 8.0, 6.0)] * 10 + [_e("B", 0.5, 0.3)]
    s = explosion.summarise(obs, 5.0)
    # per day: ten wins to one loss. per contract: one to one.
    assert round(s["per_contract_avg"], 2) == round((5.0 + 0.3) / 2, 2)


def test_a_bucket_with_no_symbols_is_still_safe():
    obs = [{"peak_multiple": 1.0, "end_multiple": 1.0, "days_to_peak": 1}]
    s = explosion.summarise(obs, 5.0)
    assert s["contracts"] == 0


# ── Calls against puts: the period-bias check ───────────────────
def test_side_is_recorded_on_each_observation(monkeypatch):
    """A long-options result measured over a rising stretch looks profitable
    whatever the filters say. Splitting by side is what separates an edge in
    the setup from the market having gone up."""
    monkeypatch.setattr(explosion.uw, "contract_history", lambda *a, **k: PENNY)
    got = explosion.scan_contract("X", {"type": "call", "expiry": "2026-09-11"},
                                  10, 0.01, 1.0)
    assert got and all(o["side"] == "call" for o in got)


def test_missing_type_gives_an_empty_side_not_a_guess(monkeypatch):
    monkeypatch.setattr(explosion.uw, "contract_history", lambda *a, **k: PENNY)
    got = explosion.scan_contract("X", {}, 10, 0.01, 1.0)
    assert all(o["side"] == "" for o in got)
