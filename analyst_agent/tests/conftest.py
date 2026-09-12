"""Synthetic price series — every test runs offline, no market data calls."""
import numpy as np
import pandas as pd
import pytest


def make_df(n=300, trend=0.0, freq="1h", seed=1, start="2025-04-01 09:30",
            tz="America/New_York", vol_spike=False):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq=freq, tz=tz)
    close = 100 * np.exp(np.linspace(0, trend, n) + rng.normal(0, 0.01, n).cumsum())
    high = close * (1 + abs(rng.normal(0, 0.004, n)))
    low = close * (1 - abs(rng.normal(0, 0.004, n)))
    volume = rng.integers(1_000_000, 5_000_000, n).astype(float)
    if vol_spike:
        volume[-1] = volume[:-1].mean() * 4
    return pd.DataFrame({"Open": np.r_[close[0], close[:-1]], "High": high,
                         "Low": low, "Close": close, "Volume": volume}, index=idx)


@pytest.fixture
def uptrend():
    return make_df(trend=0.6, seed=11)


@pytest.fixture
def downtrend():
    return make_df(trend=-0.6, seed=12)


@pytest.fixture
def sideways():
    return make_df(trend=0.0, seed=13)
