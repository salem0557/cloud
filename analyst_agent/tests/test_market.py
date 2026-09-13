"""Loading rules that decide what the indicators are even allowed to see."""
import pandas as pd
import pytest

from analyst_agent import frames, market


def _bars(end, periods=20, freq="30min", tz="UTC", last_volume=1000):
    idx = pd.date_range(end=end, periods=periods, freq=freq, tz=tz)
    return pd.DataFrame({
        "Open": 100.0, "High": 101.0, "Low": 99.0, "Close": 100.5,
        "Volume": [1000] * (periods - 1) + [last_volume],
    }, index=idx)


def test_the_forming_bar_is_split_off():
    """Its volume is a fraction of a real bar's and its close is just the price."""
    now = pd.Timestamp.now(tz="UTC")
    df = _bars(now.floor("30min"), last_volume=20)
    closed, live, at = market.split_forming(df, frames.get("30m"))
    assert len(closed) == len(df) - 1
    assert live == 100.5
    assert at == df.index[-1]
    assert closed["Volume"].iloc[-1] == 1000       # the partial bar is gone


def test_a_closed_last_bar_is_kept():
    now = pd.Timestamp.now(tz="UTC")
    df = _bars(now.floor("30min") - pd.Timedelta(minutes=60))
    closed, live, at = market.split_forming(df, frames.get("30m"))
    assert len(closed) == len(df) and live is None and at is None


def test_a_naive_index_is_handled():
    now = pd.Timestamp.utcnow().floor("30min")
    df = _bars(now, tz=None)
    closed, live, _ = market.split_forming(df, frames.get("30m"))
    assert len(closed) == len(df) - 1 and live is not None


def test_a_short_series_is_never_trimmed():
    """Trimming a nearly-empty frame would leave nothing to analyse."""
    now = pd.Timestamp.now(tz="UTC")
    df = _bars(now.floor("30min"), periods=4)
    assert len(market.split_forming(df, frames.get("30m"))[0]) == 4


@pytest.mark.parametrize("df", [None, pd.DataFrame()])
def test_empty_input_is_returned_as_is(df):
    closed, live, at = market.split_forming(df, frames.get("1h"))
    assert live is None and at is None


def test_step_up_order():
    assert [f.key for f in market._step_up(frames.get("45m"))][:2] == ["1h", "2h"]
    assert market._step_up(frames.get("1mo")) == []


def _series(price, volume, spread=0.01, n=40):
    idx = pd.date_range("2026-08-01", periods=n, freq="D", tz="UTC")
    return pd.DataFrame({
        "Open": price, "High": price * (1 + spread), "Low": price * (1 - spread),
        "Close": price, "Volume": volume,
    }, index=idx)


def test_a_dead_token_is_rejected():
    """No volume and flat candles: indicators on it are noise dressed as analysis."""
    alive, why = market.is_tradable(_series(0.00019, volume=0.0, spread=0.0))
    assert alive is False and "الفوليوم" in why


def test_a_frozen_market_is_rejected():
    alive, why = market.is_tradable(_series(1.0, volume=1000.0, spread=0.00001))
    assert alive is False and "جامد" in why


def test_a_zero_price_is_rejected():
    assert market.is_tradable(_series(0.0, volume=1000.0))[0] is False


def test_a_real_market_passes():
    assert market.is_tradable(_series(180.0, volume=5_000_000.0))[0] is True


def test_a_penny_asset_with_real_trading_passes():
    """Cheap is not the same as dead."""
    assert market.is_tradable(_series(0.00019, volume=9_000_000.0, spread=0.02))[0] is True


def test_load_skips_a_dead_symbol_for_the_next_candidate(monkeypatch):
    from analyst_agent import frames

    served = {"DEAD": _series(0.0002, volume=0.0, spread=0.0),
              "LIVE": _series(180.0, volume=5_000_000.0)}
    monkeypatch.setattr(market, "fetch", lambda symbol, frame: served.get(symbol))
    monkeypatch.setattr(market, "_download", lambda *a, **k: served["LIVE"])
    monkeypatch.setattr(market, "_meta", lambda symbol: {"symbol": symbol})
    data = market.load(["DEAD", "LIVE"], frames.get("1d"))
    assert data is not None and data.symbol == "LIVE"
