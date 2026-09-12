import pandas as pd

from analyst_agent import indicators as I
from analyst_agent.tests.conftest import make_df


def test_rsi_bounds_and_direction():
    up = make_df(trend=1.2, seed=3)
    down = make_df(trend=-1.2, seed=4)
    rsi_up = I.rsi(up["Close"]).iloc[-1]
    rsi_down = I.rsi(down["Close"]).iloc[-1]
    assert 0 <= rsi_up <= 100 and 0 <= rsi_down <= 100
    assert rsi_up > rsi_down


def test_ema_tracks_a_constant_series():
    series = pd.Series([10.0] * 50)
    assert abs(I.ema(series, 20).iloc[-1] - 10.0) < 1e-6


def test_atr_is_positive_and_scales_with_range():
    df = make_df(seed=5)
    wide = df.copy()
    wide["High"] = wide["High"] * 1.05
    wide["Low"] = wide["Low"] * 0.95
    assert I.atr(df).iloc[-1] > 0
    assert I.atr(wide).iloc[-1] > I.atr(df).iloc[-1]


def test_bollinger_order():
    df = make_df(seed=6)
    low, mid, up = I.bollinger(df["Close"])
    assert low.iloc[-1] < mid.iloc[-1] < up.iloc[-1]


def test_levels_split_around_price():
    df = make_df(seed=7)
    price = float(df["Close"].iloc[-1])
    lv = I.levels(df, price)
    assert all(s["price"] < price for s in lv["supports"])
    assert all(r["price"] > price for r in lv["resistances"])
    assert lv["supports"] == sorted(lv["supports"], key=lambda s: -s["price"])


def test_cluster_levels_merges_close_prices():
    merged = I.cluster_levels([100.0, 100.2, 100.1, 120.0], tolerance=0.01)
    assert len(merged) == 2
    assert merged[0]["touches"] == 3


def test_bullish_engulfing_is_detected():
    idx = pd.date_range("2025-01-01", periods=4, freq="D")
    df = pd.DataFrame({
        "Open": [10, 10, 10.5, 9.6],
        "High": [10.4, 10.2, 10.6, 11.2],
        "Low": [9.8, 9.6, 9.4, 9.5],
        "Close": [10.1, 9.9, 9.7, 11.0],
        "Volume": [1000] * 4,
    }, index=idx)
    assert "bullish_engulfing" in I.candle_patterns(df)


def test_shooting_star_is_detected():
    idx = pd.date_range("2025-01-01", periods=3, freq="D")
    df = pd.DataFrame({
        "Open": [10, 10, 10.0],
        "High": [10.2, 10.3, 11.5],
        "Low": [9.9, 9.9, 9.95],
        "Close": [10.1, 10.2, 10.05],
        "Volume": [1000] * 3,
    }, index=idx)
    assert "shooting_star" in I.candle_patterns(df)


def test_analyze_snapshot_is_complete_and_serialisable():
    import json

    df = make_df(trend=0.5, seed=8, vol_spike=True)
    facts = I.analyze(df, "1h", daily_df=make_df(freq="1D", n=400, seed=9, tz=None))
    for section in ("trend", "momentum", "volatility", "volume", "levels"):
        assert section in facts
    assert facts["volume"]["spike"] is True
    assert facts["price"] == round(float(df["Close"].iloc[-1]), 2)
    assert facts["vwap"] is not None          # intraday frame -> VWAP present
    json.dumps(facts, default=str)            # must survive the prompt payload


def test_daily_frame_has_no_vwap():
    facts = I.analyze(make_df(freq="1D", n=300, tz=None), "1d")
    assert "vwap" not in facts


def test_structure_reads_uptrend_and_downtrend():
    up = I.trend_structure(make_df(trend=1.0, seed=21))
    down = I.trend_structure(make_df(trend=-1.0, seed=22))
    assert up["structure"] in ("uptrend", "expanding")
    assert down["structure"] in ("downtrend", "expanding")


def test_fib_levels_inside_the_swing():
    fib = I.fib_levels(make_df(trend=0.7, seed=23))
    assert fib is not None
    for value in fib["levels"].values():
        assert fib["low"] <= value <= fib["high"]


def test_rsi_survives_a_flat_series():
    flat = pd.Series([5.0] * 60)
    assert I.rsi(flat).notna().all()
