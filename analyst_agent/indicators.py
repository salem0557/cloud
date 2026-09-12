"""Every number the read is built from — computed here, never guessed later.

The language model downstream is given this dict and told it may not invent a
single figure. So anything the analysis wants to claim (trend, momentum,
levels, volume, volatility, patterns, divergence) has to be measurable here
first.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from . import config


# --- primitives -------------------------------------------------------------
def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50.0).where(avg_loss.notna() | avg_gain.notna(), 50.0)


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    line = ema(series, fast) - ema(series, slow)
    sig = ema(line, signal)
    return line, sig, line - sig


def bollinger(series: pd.Series, period: int = 20, std: float = 2.0):
    mid = series.rolling(period).mean()
    dev = series.rolling(period).std(ddof=0)
    return mid - std * dev, mid, mid + std * dev


def true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["Close"].shift(1)
    return pd.concat([
        df["High"] - df["Low"],
        (df["High"] - prev_close).abs(),
        (df["Low"] - prev_close).abs(),
    ], axis=1).max(axis=1)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    return true_range(df).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def stochastic(df: pd.DataFrame, k: int = 14, d: int = 3):
    low = df["Low"].rolling(k).min()
    high = df["High"].rolling(k).max()
    percent_k = 100 * (df["Close"] - low) / (high - low).replace(0, np.nan)
    return percent_k, percent_k.rolling(d).mean()


def adx(df: pd.DataFrame, period: int = 14):
    up = df["High"].diff()
    down = -df["Low"].diff()
    plus_dm = np.where((up > down) & (up > 0), up, 0.0)
    minus_dm = np.where((down > up) & (down > 0), down, 0.0)
    tr = true_range(df).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(
        alpha=1 / period, adjust=False, min_periods=period).mean() / tr
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(
        alpha=1 / period, adjust=False, min_periods=period).mean() / tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean(), plus_di, minus_di


def obv(df: pd.DataFrame) -> pd.Series:
    sign = np.sign(df["Close"].diff()).fillna(0.0)
    return (sign * df["Volume"].fillna(0)).cumsum()


def vwap(df: pd.DataFrame) -> pd.Series:
    """Session VWAP for intraday frames (resets each calendar day)."""
    typical = (df["High"] + df["Low"] + df["Close"]) / 3
    volume = df["Volume"].fillna(0)
    day = pd.Series(df.index.date, index=df.index)
    cum_pv = (typical * volume).groupby(day).cumsum()
    cum_v = volume.groupby(day).cumsum().replace(0, np.nan)
    return cum_pv / cum_v


# --- structure --------------------------------------------------------------
def swing_points(df: pd.DataFrame, window: int | None = None) -> tuple[pd.Series, pd.Series]:
    """Fractal swing highs/lows: a bar higher (lower) than `window` bars each side."""
    window = window or config.PIVOT_WINDOW
    highs, lows = df["High"], df["Low"]
    is_high = highs == highs.rolling(window * 2 + 1, center=True).max()
    is_low = lows == lows.rolling(window * 2 + 1, center=True).min()
    return highs[is_high.fillna(False)], lows[is_low.fillna(False)]


def cluster_levels(values: list[float], tolerance: float | None = None) -> list[dict]:
    """Group nearby swing prices into levels, strongest (most touches) first."""
    tolerance = tolerance if tolerance is not None else config.LEVEL_TOLERANCE
    clusters: list[list[float]] = []
    for value in sorted(values):
        if clusters and abs(value - np.mean(clusters[-1])) / max(value, 1e-9) <= tolerance:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    out = [{"price": float(np.mean(c)), "touches": len(c)} for c in clusters]
    return sorted(out, key=lambda d: (-d["touches"], -d["price"]))


def levels(df: pd.DataFrame, price: float) -> dict:
    """Support/resistance around the current price, from swing clusters."""
    highs, lows = swing_points(df)
    all_levels = cluster_levels([float(v) for v in list(highs) + list(lows)])
    supports = sorted([lv for lv in all_levels if lv["price"] < price * 0.999],
                      key=lambda lv: -lv["price"])
    resistances = sorted([lv for lv in all_levels if lv["price"] > price * 1.001],
                         key=lambda lv: lv["price"])
    return {
        "supports": supports[:4],
        "resistances": resistances[:4],
        "nearest_support": supports[0]["price"] if supports else None,
        "nearest_resistance": resistances[0]["price"] if resistances else None,
        "swing_high": float(highs.iloc[-1]) if len(highs) else None,
        "swing_low": float(lows.iloc[-1]) if len(lows) else None,
    }


def fib_levels(df: pd.DataFrame, lookback: int = 120) -> dict | None:
    """Fibonacci retracement over the dominant recent swing."""
    tail = df.tail(lookback)
    if len(tail) < 20:
        return None
    high_idx = tail["High"].idxmax()
    low_idx = tail["Low"].idxmin()
    high = float(tail["High"].max())
    low = float(tail["Low"].min())
    if high <= low:
        return None
    up_leg = low_idx < high_idx  # low came first -> the swing is up
    span = high - low
    ratios = (0.236, 0.382, 0.5, 0.618, 0.786)
    out = {r: (high - span * r if up_leg else low + span * r) for r in ratios}
    return {
        "direction": "up" if up_leg else "down",
        "high": high, "low": low,
        "levels": {f"{int(r * 1000) / 10:g}%": round(float(v), 6) for r, v in out.items()},
        "golden_zone": [round(float(out[0.618]), 6), round(float(out[0.5]), 6)],
    }


def pivot_points(df: pd.DataFrame) -> dict | None:
    """Classic floor-trader pivots from the previous completed bar."""
    if len(df) < 2:
        return None
    prev = df.iloc[-2]
    high, low, close = float(prev["High"]), float(prev["Low"]), float(prev["Close"])
    pp = (high + low + close) / 3
    return {
        "PP": pp,
        "R1": 2 * pp - low, "S1": 2 * pp - high,
        "R2": pp + (high - low), "S2": pp - (high - low),
        "R3": high + 2 * (pp - low), "S3": low - 2 * (high - pp),
    }


def trend_structure(df: pd.DataFrame) -> dict:
    """Higher-highs/higher-lows accounting on the last swings."""
    highs, lows = swing_points(df)
    hh = hl = lh = ll = 0
    if len(highs) >= 2:
        hh = int(highs.iloc[-1] > highs.iloc[-2])
        lh = int(highs.iloc[-1] < highs.iloc[-2])
    if len(lows) >= 2:
        hl = int(lows.iloc[-1] > lows.iloc[-2])
        ll = int(lows.iloc[-1] < lows.iloc[-2])
    if hh and hl:
        label, ar = "uptrend", "هيكل صاعد (قمم وقيعان أعلى)"
    elif lh and ll:
        label, ar = "downtrend", "هيكل هابط (قمم وقيعان أدنى)"
    elif hh and ll:
        label, ar = "expanding", "تذبذب متوسع"
    elif lh and hl:
        label, ar = "contracting", "انضغاط داخل مثلث"
    else:
        label, ar = "unclear", "هيكل غير واضح"
    scale = float(df["Close"].iloc[-1])
    return {"structure": label, "structure_ar": ar,
            "higher_high": bool(hh), "higher_low": bool(hl),
            "lower_high": bool(lh), "lower_low": bool(ll),
            "swing_highs": [_round(v, scale) for v in highs.tail(3)],
            "swing_lows": [_round(v, scale) for v in lows.tail(3)]}


def rsi_divergence(df: pd.DataFrame, rsi_series: pd.Series, lookback: int = 60) -> str | None:
    """Bullish/bearish RSI divergence against the last two swings."""
    tail = df.tail(lookback)
    highs, lows = swing_points(tail)
    if len(lows) >= 2:
        p1, p2 = lows.iloc[-2], lows.iloc[-1]
        r1, r2 = rsi_series.get(lows.index[-2]), rsi_series.get(lows.index[-1])
        if r1 is not None and r2 is not None and p2 < p1 and r2 > r1 + 2:
            return "bullish"
    if len(highs) >= 2:
        p1, p2 = highs.iloc[-2], highs.iloc[-1]
        r1, r2 = rsi_series.get(highs.index[-2]), rsi_series.get(highs.index[-1])
        if r1 is not None and r2 is not None and p2 > p1 and r2 < r1 - 2:
            return "bearish"
    return None


# --- candle patterns --------------------------------------------------------
def candle_patterns(df: pd.DataFrame) -> list[str]:
    """Named patterns on the last one to three bars (English keys)."""
    if len(df) < 3:
        return []
    out: list[str] = []
    c = df.iloc[-1]
    p = df.iloc[-2]
    body = abs(c["Close"] - c["Open"])
    rng = max(c["High"] - c["Low"], 1e-12)
    upper = c["High"] - max(c["Close"], c["Open"])
    lower = min(c["Close"], c["Open"]) - c["Low"]
    prev_body = abs(p["Close"] - p["Open"])
    bull = c["Close"] > c["Open"]

    if body / rng < 0.1:
        out.append("doji")
    if body / rng > 0.7:
        out.append("marubozu_bull" if bull else "marubozu_bear")
    # Wick tests are measured against the bar's range, not its body: a
    # near-zero body (a doji) would otherwise make every ratio blow up.
    if lower > rng * 0.55 and upper < rng * 0.25:
        out.append("hammer" if bull or c["Close"] > p["Close"] else "hanging_man")
    if upper > rng * 0.55 and lower < rng * 0.25:
        out.append("shooting_star")
    if body > prev_body and bull and p["Close"] < p["Open"] \
            and c["Close"] >= p["Open"] and c["Open"] <= p["Close"]:
        out.append("bullish_engulfing")
    if body > prev_body and not bull and p["Close"] > p["Open"] \
            and c["Close"] <= p["Open"] and c["Open"] >= p["Close"]:
        out.append("bearish_engulfing")
    if c["High"] <= p["High"] and c["Low"] >= p["Low"]:
        out.append("inside_bar")
    if c["High"] > p["High"] and c["Low"] < p["Low"]:
        out.append("outside_bar")
    if c["Low"] > p["High"]:
        out.append("gap_up")
    if c["High"] < p["Low"]:
        out.append("gap_down")
    window = df.tail(20)
    if c["Close"] >= window["High"].iloc[:-1].max():
        out.append("breakout_20")
    if c["Close"] <= window["Low"].iloc[:-1].min():
        out.append("breakdown_20")
    return out


PATTERNS_AR = {
    "doji": "دوجي (تردد)",
    "marubozu_bull": "شمعة صاعدة كاملة الجسم",
    "marubozu_bear": "شمعة هابطة كاملة الجسم",
    "hammer": "مطرقة (رفض للنزول)",
    "hanging_man": "رجل معلّق",
    "shooting_star": "شهاب (رفض للصعود)",
    "bullish_engulfing": "احتواء شرائي",
    "bearish_engulfing": "احتواء بيعي",
    "inside_bar": "شمعة داخلية (انضغاط)",
    "outside_bar": "شمعة خارجية (توسّع)",
    "gap_up": "فتحة سعرية صاعدة",
    "gap_down": "فتحة سعرية هابطة",
    "breakout_20": "اختراق أعلى 20 شمعة",
    "breakdown_20": "كسر أدنى 20 شمعة",
}


def _round(value, price: float):
    if value is None or (isinstance(value, float) and (math.isnan(value) or math.isinf(value))):
        return None
    digits = 4 if price < 1 else 3 if price < 10 else 2
    return round(float(value), digits)


def analyze(df: pd.DataFrame, frame_key: str = "1d",
            daily_df: pd.DataFrame | None = None) -> dict:
    """Full indicator snapshot for the last bar of `df`."""
    close = df["Close"]
    price = float(close.iloc[-1])
    r = lambda v: _round(v, price)  # noqa: E731 - local shorthand

    e_fast = ema(close, config.EMA_FAST)
    e_mid = ema(close, config.EMA_MID)
    e_slow = ema(close, config.EMA_SLOW)
    rsi_s = rsi(close, config.RSI_PERIOD)
    macd_line, macd_sig, macd_hist = macd(close, config.MACD_FAST, config.MACD_SLOW,
                                          config.MACD_SIGNAL)
    bb_low, bb_mid, bb_up = bollinger(close, config.BB_PERIOD, config.BB_STD)
    atr_s = atr(df, config.ATR_PERIOD)
    k, d = stochastic(df)
    adx_s, plus_di, minus_di = adx(df)
    obv_s = obv(df)
    volume = df["Volume"].fillna(0)
    vol_ma = volume.rolling(20).mean()

    atr_val = float(atr_s.iloc[-1]) if pd.notna(atr_s.iloc[-1]) else float(
        true_range(df).tail(14).mean())
    bb_width = ((bb_up - bb_low) / bb_mid.replace(0, np.nan))
    percent_b = ((price - bb_low.iloc[-1]) /
                 max(float(bb_up.iloc[-1] - bb_low.iloc[-1]), 1e-12)) * 100 \
        if pd.notna(bb_up.iloc[-1]) else None

    lvl = levels(df, price)
    struct = trend_structure(df)
    fib = fib_levels(df) if config.SHOW_FIB else None
    piv = pivot_points(df)
    patterns = candle_patterns(df)
    divergence = rsi_divergence(df, rsi_s)

    hist = daily_df if daily_df is not None and not daily_df.empty else df
    high_52w = float(hist["High"].tail(252).max())
    low_52w = float(hist["Low"].tail(252).min())

    ema_stack = ("bullish" if e_fast.iloc[-1] > e_mid.iloc[-1] > e_slow.iloc[-1]
                 else "bearish" if e_fast.iloc[-1] < e_mid.iloc[-1] < e_slow.iloc[-1]
                 else "mixed")
    obv_slope = float(obv_s.iloc[-1] - obv_s.iloc[-min(len(obv_s), 20)])
    rel_volume = (float(volume.iloc[-1] / vol_ma.iloc[-1])
                  if pd.notna(vol_ma.iloc[-1]) and vol_ma.iloc[-1] else None)

    facts = {
        "frame": frame_key,
        "bars": int(len(df)),
        "last_bar_time": str(df.index[-1]),
        "price": r(price),
        "open": r(df["Open"].iloc[-1]),
        "high": r(df["High"].iloc[-1]),
        "low": r(df["Low"].iloc[-1]),
        "change_pct": round(float((price / close.iloc[-2] - 1) * 100), 2) if len(close) > 1 else None,
        "change_5_bars_pct": round(float((price / close.iloc[-6] - 1) * 100), 2) if len(close) > 6 else None,
        "change_20_bars_pct": round(float((price / close.iloc[-21] - 1) * 100), 2) if len(close) > 21 else None,

        "trend": {
            "ema_fast": r(e_fast.iloc[-1]), "ema_mid": r(e_mid.iloc[-1]),
            "ema_slow": r(e_slow.iloc[-1]) if pd.notna(e_slow.iloc[-1]) else None,
            "ema_stack": ema_stack,
            "price_vs_ema_fast_pct": round(float((price / e_fast.iloc[-1] - 1) * 100), 2),
            "price_vs_ema_slow_pct": (round(float((price / e_slow.iloc[-1] - 1) * 100), 2)
                                      if pd.notna(e_slow.iloc[-1]) else None),
            "adx": round(float(adx_s.iloc[-1]), 1) if pd.notna(adx_s.iloc[-1]) else None,
            "di_plus": round(float(plus_di.iloc[-1]), 1) if pd.notna(plus_di.iloc[-1]) else None,
            "di_minus": round(float(minus_di.iloc[-1]), 1) if pd.notna(minus_di.iloc[-1]) else None,
            **struct,
        },
        "momentum": {
            "rsi": round(float(rsi_s.iloc[-1]), 1),
            "rsi_prev": round(float(rsi_s.iloc[-2]), 1) if len(rsi_s) > 1 else None,
            "rsi_state": ("تشبع شرائي" if rsi_s.iloc[-1] >= 70 else
                          "تشبع بيعي" if rsi_s.iloc[-1] <= 30 else "محايد"),
            "macd": r(macd_line.iloc[-1]), "macd_signal": r(macd_sig.iloc[-1]),
            "macd_hist": r(macd_hist.iloc[-1]),
            "macd_cross": ("bullish" if macd_hist.iloc[-1] > 0 >= macd_hist.iloc[-2]
                           else "bearish" if macd_hist.iloc[-1] < 0 <= macd_hist.iloc[-2]
                           else "none") if len(macd_hist) > 1 else "none",
            "stoch_k": round(float(k.iloc[-1]), 1) if pd.notna(k.iloc[-1]) else None,
            "stoch_d": round(float(d.iloc[-1]), 1) if pd.notna(d.iloc[-1]) else None,
            "rsi_divergence": divergence,
        },
        "volatility": {
            "atr": r(atr_val),
            "atr_pct": round(atr_val / price * 100, 2),
            "bb_upper": r(bb_up.iloc[-1]), "bb_mid": r(bb_mid.iloc[-1]),
            "bb_lower": r(bb_low.iloc[-1]),
            "percent_b": round(float(percent_b), 1) if percent_b is not None else None,
            "bb_width_pct": round(float(bb_width.iloc[-1] * 100), 2) if pd.notna(bb_width.iloc[-1]) else None,
            "squeeze": bool(pd.notna(bb_width.iloc[-1]) and len(bb_width.dropna()) > 40
                            and bb_width.iloc[-1] <= bb_width.dropna().tail(60).quantile(0.2)),
        },
        "volume": {
            "last": int(volume.iloc[-1]) if pd.notna(volume.iloc[-1]) else None,
            "avg_20": int(vol_ma.iloc[-1]) if pd.notna(vol_ma.iloc[-1]) else None,
            "relative": round(rel_volume, 2) if rel_volume else None,
            "spike": bool(rel_volume and rel_volume >= 1.8),
            "dry": bool(rel_volume and rel_volume <= 0.6),
            "obv_slope_20": obv_slope,
            "obv_direction": "up" if obv_slope > 0 else "down" if obv_slope < 0 else "flat",
        },
        "levels": {
            "supports": [{"price": r(s["price"]), "touches": s["touches"]} for s in lvl["supports"]],
            "resistances": [{"price": r(s["price"]), "touches": s["touches"]} for s in lvl["resistances"]],
            "nearest_support": r(lvl["nearest_support"]),
            "nearest_resistance": r(lvl["nearest_resistance"]),
            "swing_high": r(lvl["swing_high"]), "swing_low": r(lvl["swing_low"]),
            "high_52w": r(high_52w), "low_52w": r(low_52w),
            "pct_from_52w_high": round((price / high_52w - 1) * 100, 2) if high_52w else None,
            "pct_above_52w_low": round((price / low_52w - 1) * 100, 2) if low_52w else None,
            "fib": fib,
            "pivots": {k2: r(v) for k2, v in piv.items()} if piv else None,
        },
        "patterns": patterns,
        "patterns_ar": [PATTERNS_AR[p] for p in patterns if p in PATTERNS_AR],
    }

    if frame_key in ("1m", "2m", "3m", "5m", "10m", "15m", "30m", "45m", "1h", "2h", "3h", "4h"):
        try:
            vw = vwap(df)
            if pd.notna(vw.iloc[-1]):
                facts["vwap"] = r(vw.iloc[-1])
                facts["price_vs_vwap_pct"] = round(float((price / vw.iloc[-1] - 1) * 100), 2)
        except Exception:
            pass
    return facts


def series_for_chart(df: pd.DataFrame) -> dict[str, pd.Series]:
    """The overlays the rendered chart draws, on the same index as `df`."""
    close = df["Close"]
    bb_low, bb_mid, bb_up = bollinger(close, config.BB_PERIOD, config.BB_STD)
    macd_line, macd_sig, macd_hist = macd(close, config.MACD_FAST, config.MACD_SLOW,
                                          config.MACD_SIGNAL)
    return {
        "ema_fast": ema(close, config.EMA_FAST),
        "ema_mid": ema(close, config.EMA_MID),
        "ema_slow": ema(close, config.EMA_SLOW),
        "bb_lower": bb_low, "bb_mid": bb_mid, "bb_upper": bb_up,
        "rsi": rsi(close, config.RSI_PERIOD),
        "macd": macd_line, "macd_signal": macd_sig, "macd_hist": macd_hist,
        "volume_ma": df["Volume"].fillna(0).rolling(20).mean(),
    }
