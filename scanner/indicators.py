"""Technical indicators and reversal-up filters, parameterized so each
module (stocks/crypto) can supply its own thresholds -- there is no shared
global config read here on purpose, since the two modules use different
lookback windows and tolerances.

Each filter takes an OHLCV DataFrame (columns: Open, High, Low, Close, Volume)
and returns (matched: bool, detail: str) where detail is a short human-readable
note used in the Telegram message.
"""
import numpy as np
import pandas as pd

from .utils import fmt_price


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

def check_bollinger_lower(df: pd.DataFrame, period: int = 20, num_std: float = 2.0,
                          tolerance: float = 0.005):
    close = df["Close"]
    if len(close) < period + 1:
        return False, "بيانات غير كافية"
    lower, _, _ = bollinger(close, period, num_std)
    last_close = close.iloc[-1]
    last_lower = lower.iloc[-1]
    if np.isnan(last_lower):
        return False, "بيانات غير كافية"
    touched = (last_close <= last_lower * (1 + tolerance)
               or df["Low"].iloc[-1] <= last_lower)
    return bool(touched), f"الحد السفلي {fmt_price(last_lower)}"


def check_rsi_oversold(df: pd.DataFrame, period: int = 14, oversold: float = 35.0):
    close = df["Close"]
    if len(close) < period + 1:
        return False, "بيانات غير كافية"
    value = rsi(close, period).iloc[-1]
    if np.isnan(value):
        return False, "بيانات غير كافية"
    return bool(value < oversold), f"RSI={value:.1f}"


def _cluster_levels(prices: list[float], cluster_tol: float) -> list[tuple[float, int]]:
    """Group sorted pivot prices within `cluster_tol` of each other into
    (level_price, touch_count) levels."""
    levels = []
    cluster = [prices[0]]
    for p in prices[1:]:
        if p <= cluster[0] * (1 + cluster_tol):
            cluster.append(p)
        else:
            levels.append((float(np.mean(cluster)), len(cluster)))
            cluster = [p]
    levels.append((float(np.mean(cluster)), len(cluster)))
    return levels


def find_nearest_support_info(df: pd.DataFrame, lookback: int = 250, pivot_order: int = 3,
                              cluster_tol: float = 0.01, min_touches: int = 2,
                              margin: float = 0.015, break_tol: float = 0.005):
    """(level, touches) للدعم الذي يقف عنده السعر حالياً أو تحته بقليل، إن
    وُجد -- touches (عدد مرات الاختبار التاريخية) يُستخدم لتقدير قوة الدعم
    في نظام النقاط (stocks_module.py/crypto_module.py)."""
    lows = df["Low"].to_numpy()[-lookback:]
    close = float(df["Close"].iloc[-1])
    pivots = find_pivots(lows, order=pivot_order, highs=False)
    if len(pivots) < min_touches:
        return None

    prices = sorted(lows[i] for i in pivots)
    levels = _cluster_levels(prices, cluster_tol)

    candidates = [(lv, touches) for lv, touches in levels if touches >= min_touches]
    for level, touches in candidates:
        # Support: price sitting just above, or a slight dip below
        near = 0 <= (close - level) / level <= margin
        slight_break = 0 <= (level - close) / level <= break_tol
        if near or slight_break:
            return level, touches
    return None


def find_nearest_support(df: pd.DataFrame, lookback: int = 250, pivot_order: int = 3,
                         cluster_tol: float = 0.01, min_touches: int = 2,
                         margin: float = 0.015, break_tol: float = 0.005):
    """The clustered pivot-low support level the price is sitting on or just
    broke below, if any (level only -- see find_nearest_support_info for the
    touch count too). Shared by check_support and the chart renderer so the
    plotted line always matches the filter's own reasoning."""
    info = find_nearest_support_info(df, lookback, pivot_order, cluster_tol,
                                     min_touches, margin, break_tol)
    return info[0] if info else None


def check_support(df: pd.DataFrame, lookback: int = 250, pivot_order: int = 3,
                  cluster_tol: float = 0.01, min_touches: int = 2,
                  margin: float = 0.015, break_tol: float = 0.005):
    """Price sitting on a support level formed by clustered pivot lows."""
    lows = df["Low"].to_numpy()[-lookback:]
    pivots = find_pivots(lows, order=pivot_order, highs=False)
    if len(pivots) < min_touches:
        return False, "لا توجد قيعان كافية"
    level = find_nearest_support(df, lookback, pivot_order, cluster_tol,
                                 min_touches, margin, break_tol)
    if level is None:
        return False, "بعيد عن الدعم"
    return True, f"دعم عند {fmt_price(level)}"


def find_nearest_resistance(df: pd.DataFrame, lookback: int = 250, pivot_order: int = 3,
                            cluster_tol: float = 0.01, min_touches: int = 2):
    """أقرب مستوى مقاومة (قمم متكررة) فوق السعر الحالي، إن وُجد. يُستخدم كهدف
    ربح تقديري ("نسبة الربح المحتملة") -- وليس فلتراً، البوت يبقى استراتيجية
    صعود بحتة في كل وحداته."""
    highs = df["High"].to_numpy()[-lookback:]
    close = float(df["Close"].iloc[-1])
    pivots = find_pivots(highs, order=pivot_order, highs=True)
    if len(pivots) < min_touches:
        return None

    prices = sorted(highs[i] for i in pivots)
    levels = _cluster_levels(prices, cluster_tol)

    candidates = [lv for lv, touches in levels if touches >= min_touches and lv > close]
    return min(candidates) if candidates else None


def check_falling_wedge_tier(df: pd.DataFrame, lookback: int = 120, pivot_order: int = 3,
                             min_bars: int = 20):
    """درجة الوتد الهابط: descending, converging trendlines through pivot
    highs/lows, with the upper line falling faster than the lower one.

    يرجع (tier, detail) حيث tier هي "complete" (نموذج مكتمل: تغطية زمنية
    كافية والسعر داخل الوتد بهامش ضيق)، "semi" (الخطوط والانحدار صحيحة لكن
    التغطية الزمنية أو موضع السعر أضعف من الشرط الصارم)، أو None (لا يوجد
    وتد هابط أصلاً)."""
    data = df.iloc[-lookback:]
    highs = data["High"].to_numpy()
    lows = data["Low"].to_numpy()
    close = float(data["Close"].iloc[-1])
    n = len(data)

    ph = find_pivots(highs, order=pivot_order, highs=True)
    pl = find_pivots(lows, order=pivot_order, highs=False)
    if len(ph) < 2 or len(pl) < 2:
        return None, "لا توجد قمم/قيعان كافية"

    # Use the most recent pivots (up to 4 of each) to define the wedge
    ph = ph[-4:]
    pl = pl[-4:]
    hs, hi = np.polyfit(ph, highs[ph], 1)  # slope, intercept of upper line
    ls, li = np.polyfit(pl, lows[pl], 1)   # slope, intercept of lower line

    both_falling = hs < 0 and ls < 0
    converging = hs < ls  # upper line falls faster than lower line
    if not (both_falling and converging):
        return None, "لا يوجد وتد هابط"

    # Convergence (apex) must lie ahead of the last bar, not inside the pattern
    apex = (li - hi) / (hs - ls)
    if apex <= n - 1:
        return None, "الخطوط تقاطعت"

    pattern_start = min(ph[0], pl[0])
    bars_covered = n - 1 - pattern_start
    upper_now = hs * (n - 1) + hi
    lower_now = ls * (n - 1) + li
    strict_margin = 0.005 * close
    loose_margin = 0.02 * close
    inside_strict = (lower_now - strict_margin) <= close <= (upper_now + strict_margin)
    inside_loose = (lower_now - loose_margin) <= close <= (upper_now + loose_margin)

    if bars_covered >= min_bars and inside_strict:
        return "complete", "وتد هابط مكتمل التكوين"
    if bars_covered >= min_bars * 0.6 and inside_loose:
        return "semi", "وتد هابط شبه مكتمل"
    return None, "لا يوجد وتد هابط واضح"


def check_falling_wedge(df: pd.DataFrame, lookback: int = 120, pivot_order: int = 3,
                        min_bars: int = 20):
    """توافقاً مع الاستخدام القديم: True فقط للوتد المكتمل بالكامل."""
    tier, detail = check_falling_wedge_tier(df, lookback, pivot_order, min_bars)
    return tier == "complete", detail
