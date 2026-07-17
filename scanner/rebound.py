"""تحليل "القاع والارتداد" لأمر /bottomcalls: هل السهم نازل الآن وجالس
في منطقة قاع، وهل اعتاد تاريخياً أن يرتد صعوداً من مثل هذه المنطقة؟

المنهجية (بيانات يومية لسنتين):
1. شرط الحاضر — السهم "نازل وفي قاع": مغلق تحت متوسط 50 يوم أو عائد آخر
   20 جلسة سالب، **و** في "منطقة قاع" (RSI يومي تحت DIP_RSI أو الإغلاق ضمن
   DIP_NEAR_LOW_PCT فوق قاع 52 أسبوعاً).
2. شرط التاريخ — "اعتاد الارتداد": نرصد كل المرات السابقة التي دخل فيها
   السهم منطقة قاع مشابهة، ونقيس أعلى ارتفاع خلال REBOUND_HORIZON_DAYS
   جلسة تالية. يتأهل السهم فقط إذا تكرر ذلك REBOUND_MIN_EPISODES مرة على
   الأقل وارتد ‏≥ REBOUND_MIN_GAIN في نسبة REBOUND_MIN_RATE من المرات.

هذا إحصاء وصفي لسلوك السهم الماضي — لا يضمن تكرار الارتداد مستقبلاً،
لذلك تُعرض الأرقام نفسها للمشترك ليحكم بنفسه.
"""
import logging
from dataclasses import dataclass

import pandas as pd
import yfinance as yf

from . import config
from .indicators import rsi

log = logging.getLogger(__name__)

REQUIRED_COLS = ["Open", "High", "Low", "Close", "Volume"]

# سنة تداول ~252 جلسة؛ أقل من ذلك لا يكفي لرصد "قاع 52 أسبوع" ولا لجمع
# عينة تاريخية ذات معنى.
MIN_HISTORY_BARS = 252


def fetch_daily_batch(symbols: list[str]) -> dict[str, pd.DataFrame]:
    """شموع يومية بعمق REBOUND_HISTORY_PERIOD لدفعة رموز (نفس نمط
    data.fetch_batch لكن على الفريم اليومي المطلوب للتحليل التاريخي)."""
    raw = yf.download(
        tickers=symbols,
        interval="1d",
        period=config.REBOUND_HISTORY_PERIOD,
        group_by="ticker",
        auto_adjust=True,
        threads=min(len(symbols), config.DOWNLOAD_THREADS),
        progress=False,
    )
    out: dict[str, pd.DataFrame] = {}
    if raw is None or raw.empty:
        return out

    frames: dict[str, pd.DataFrame] = {}
    if isinstance(raw.columns, pd.MultiIndex):
        top = raw.columns.get_level_values(0)
        for sym in symbols:
            if sym in top:
                frames[sym] = raw[sym]
    else:
        frames = {symbols[0]: raw}

    for sym, df in frames.items():
        try:
            df = df[REQUIRED_COLS].dropna(subset=["Close"])
        except KeyError:
            continue
        if not df.empty:
            out[sym] = df
    return out


@dataclass
class ReboundStats:
    """نتيجة تحليل سهم واحد اجتاز الشرطين (نازل الآن + معتاد على الارتداد)."""
    symbol: str
    price: float
    change_20d: float      # عائد آخر 20 جلسة (سالب = نازل)
    rsi14: float           # RSI اليومي الحالي
    above_52w_low: float   # كم يبعد السعر فوق قاع 52 أسبوعاً (نسبة)
    off_52w_high: float    # كم يبعد السعر تحت قمة 52 أسبوعاً (نسبة)
    episodes: int          # مرات دخول منطقة قاع مشابهة تاريخياً
    rebounds: int          # كم مرة منها ارتد >= REBOUND_MIN_GAIN
    avg_gain: float        # متوسط أعلى ارتداد عبر كل المرات

    @property
    def rebound_rate(self) -> float:
        return self.rebounds / self.episodes if self.episodes else 0.0

    @property
    def score(self) -> float:
        """للترتيب فقط: نسبة نجاح الارتداد مرجحة بمتوسط حجمه."""
        return self.rebound_rate * max(self.avg_gain, 0.0)


def analyze(symbol: str, df: pd.DataFrame) -> ReboundStats | None:
    """يرجع ReboundStats إذا اجتاز السهم شرطي الحاضر والتاريخ معاً، وإلا None."""
    if df is None or len(df) < MIN_HISTORY_BARS:
        return None

    close = df["Close"].astype(float)
    price = float(close.iloc[-1])
    if price <= 0:
        return None

    rsi_series = rsi(close, config.RSI_PERIOD)
    rsi_now = float(rsi_series.iloc[-1])
    if pd.isna(rsi_now):
        return None

    roll_low = close.rolling(252, min_periods=60).min()
    roll_high = close.rolling(252, min_periods=60).max()
    near_low = close <= roll_low * (1 + config.DIP_NEAR_LOW_PCT)
    dip = (rsi_series < config.DIP_RSI) | near_low

    # شرط الحاضر: نازل (تحت متوسط 50 يوم أو عائد 20 جلسة سالب) وفي منطقة قاع
    sma50 = float(close.rolling(50).mean().iloc[-1])
    change_20d = price / float(close.iloc[-21]) - 1
    declining = change_20d < 0 or price < sma50
    if not (declining and bool(dip.iloc[-1])):
        return None

    # شرط التاريخ: مرات مشابهة سابقة وما حدث بعدها. تُستبعد آخر
    # REBOUND_HORIZON_DAYS جلسة لأن نافذتها الأمامية لم تكتمل بعد،
    # ويُفصل بين المرات بـ REBOUND_EPISODE_GAP جلسة حتى لا يُحسب
    # القاع الواحد الممتد أسبوعين عشر "مرات".
    horizon = config.REBOUND_HORIZON_DAYS
    values = close.to_numpy()
    dip_flags = dip.to_numpy()
    n = len(values)

    episodes = rebounds = 0
    total_gain = 0.0
    next_allowed = 0
    for i in range(60, n - horizon):
        if not dip_flags[i] or i < next_allowed:
            continue
        gain = float(values[i + 1:i + 1 + horizon].max()) / float(values[i]) - 1
        episodes += 1
        total_gain += gain
        if gain >= config.REBOUND_MIN_GAIN:
            rebounds += 1
        next_allowed = i + config.REBOUND_EPISODE_GAP

    if episodes < config.REBOUND_MIN_EPISODES:
        return None
    if rebounds / episodes < config.REBOUND_MIN_RATE:
        return None

    low_52w = float(roll_low.iloc[-1])
    high_52w = float(roll_high.iloc[-1])
    return ReboundStats(
        symbol=symbol,
        price=price,
        change_20d=change_20d,
        rsi14=rsi_now,
        above_52w_low=price / low_52w - 1 if low_52w > 0 else 0.0,
        off_52w_high=1 - price / high_52w if high_52w > 0 else 0.0,
        episodes=episodes,
        rebounds=rebounds,
        avg_gain=total_gain / episodes,
    )
