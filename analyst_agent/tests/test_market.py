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
