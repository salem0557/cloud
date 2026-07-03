"""Technical indicators and the four scan filters.

Each filter takes an OHLCV DataFrame (columns: Open, High, Low, Close, Volume)
and returns (matched: bool, detail: str) where detail is a short human-readable
note used in the Telegram message.
"""
import numpy as np
import pandas as pd

from . import config


# ---------------------------------------------------------------- indicators

def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0.0)
    loss = -delta.clip(upper=0.0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    # When avg_loss is 0 (straight rally) RSI is 100 by definition
    return out.fillna(100.0).where(avg_gain.notna(), np.nan)


def bollinger(close: pd.Series, period: int = 20, num_std: float = 2.0):
    mid = close.rolling(period).mean()
    std = close.rolling(period).std(ddof=0)
    return mid - num_std * std, mid, mid + num_std * std


def find_pivots(values: np.ndarray, order: int, highs: bool) -> list[int]:
    """Indexes of local maxima (highs=True) or minima within +/- `order` bars."""
    pivots = []
    n = len(values)
    for i in range(order, n - order):
        window = values[i - order:i + order + 1]
        if highs:
            if values[i] == window.max() and (window == values[i]).sum() == 1:
                pivots.append(i)
        else:
            if values[i] == window.min() and (window == values[i]).sum() == 1:
                pivots.append(i)
    return pivots


# ------------------------------------------------------------------- filters

def check_bollinger_lower(df: pd.DataFrame):
    close = df["Close"]
    if len(close) < config.BB_PERIOD + 1:
        return False, "بيانات غير كافية"
    lower, _, _ = bollinger(close, config.BB_PERIOD, config.BB_STD)
    last_close = close.iloc[-1]
    last_lower = lower.iloc[-1]
    if np.isnan(last_lower):
        return False, "بيانات غير كافية"
    touched = (last_close <= last_lower * (1 + config.BB_TOUCH_TOLERANCE)
               or df["Low"].iloc[-1] <= last_lower)
    return bool(touched), f"الحد السفلي {last_lower:.2f}$"


def check_rsi_oversold(df: pd.DataFrame):
    close = df["Close"]
    if len(close) < config.RSI_PERIOD + 1:
        return False, "بيانات غير كافية"
    value = rsi(close, config.RSI_PERIOD).iloc[-1]
    if np.isnan(value):
        return False, "بيانات غير كافية"
    return bool(value < config.RSI_OVERSOLD), f"RSI={value:.1f}"


def check_support(df: pd.DataFrame):
    """Price sitting on a support level formed by clustered pivot lows."""
    lows = df["Low"].to_numpy()[-config.SUPPORT_LOOKBACK:]
    close = float(df["Close"].iloc[-1])
    pivots = find_pivots(lows, order=config.WEDGE_PIVOT_ORDER, highs=False)
    if len(pivots) < config.SUPPORT_MIN_TOUCHES:
        return False, "لا توجد قيعان كافية"

    # Cluster pivot lows whose prices are within SUPPORT_CLUSTER_TOL of each other
    prices = sorted(lows[i] for i in pivots)
    levels = []  # (level_price, touches)
    cluster = [prices[0]]
    for p in prices[1:]:
        if p <= cluster[0] * (1 + config.SUPPORT_CLUSTER_TOL):
            cluster.append(p)
        else:
            levels.append((float(np.mean(cluster)), len(cluster)))
            cluster = [p]
    levels.append((float(np.mean(cluster)), len(cluster)))

    supports = [lv for lv, touches in levels if touches >= config.SUPPORT_MIN_TOUCHES]
    for level in supports:
        near_above = 0 <= (close - level) / level <= config.SUPPORT_PROXIMITY
        slight_break = 0 <= (level - close) / level <= config.SUPPORT_BREAK_TOL
        if near_above or slight_break:
            return True, f"دعم عند {level:.2f}$"
    return False, "بعيد عن الدعم"


def check_falling_wedge(df: pd.DataFrame):
    """Falling wedge: descending, converging trendlines through pivot highs/lows,
    with the upper line falling faster and current price inside the pattern."""
    data = df.iloc[-config.WEDGE_LOOKBACK:]
    highs = data["High"].to_numpy()
    lows = data["Low"].to_numpy()
    close = float(data["Close"].iloc[-1])
    n = len(data)

    ph = find_pivots(highs, order=config.WEDGE_PIVOT_ORDER, highs=True)
    pl = find_pivots(lows, order=config.WEDGE_PIVOT_ORDER, highs=False)
    if len(ph) < 2 or len(pl) < 2:
        return False, "لا توجد قمم/قيعان كافية"

    # Use the most recent pivots (up to 4 of each) to define the wedge
    ph = ph[-4:]
    pl = pl[-4:]
    hs, hi = np.polyfit(ph, highs[ph], 1)  # slope, intercept of upper line
    ls, li = np.polyfit(pl, lows[pl], 1)   # slope, intercept of lower line

    pattern_start = min(ph[0], pl[0])
    if n - 1 - pattern_start < config.WEDGE_MIN_BARS:
        return False, "النموذج قصير جداً"

    both_falling = hs < 0 and ls < 0
    converging = hs < ls  # upper line falls faster than lower line
    if not (both_falling and converging):
        return False, "لا يوجد وتد هابط"

    # Convergence (apex) must lie ahead of the last bar, not inside the pattern
    apex = (li - hi) / (hs - ls)
    if apex <= n - 1:
        return False, "الخطوط تقاطعت"

    upper_now = hs * (n - 1) + hi
    lower_now = ls * (n - 1) + li
    margin = 0.005 * close
    inside = (lower_now - margin) <= close <= (upper_now + margin)
    if not inside:
        return False, "السعر خارج الوتد"
    return True, "وتد هابط مكتمل التكوين"


FILTERS = {
    "bollinger": ("بولينجر السفلي", check_bollinger_lower),
    "rsi": ("RSI تشبع بيعي", check_rsi_oversold),
    "support": ("منطقة دعم", check_support),
    "wedge": ("وتد هابط", check_falling_wedge),
}
