"""وحدة الأسهم الأمريكية (مستقلة تماماً عن وحدتَي الأوبشن والكريبتو).

تفحص STOCKS_WATCHLIST (S&P 500 + Nasdaq تقريباً، من config.py) بحثاً عن
إشارات ارتداد صعودي: بولينجر السفلي، RSI تشبع بيعي، منطقة دعم، ووتد هابط
(مكتمل أو شبه مكتمل) -- يتطلب تحقق STOCKS_FILTERS_REQUIRED من أصل 4.

الاحتمالية هنا **نظام نقاط مرجّح** (heuristic score من 0 إلى
STOCKS_SCORE_CAP)، وليست احتمالية إحصائية رياضية كما في وحدة الأوبشن (لا
يوجد سوق خيارات على السهم نفسه لتلك الحسابات هنا) -- انظر _score أدناه
للصيغة الكاملة.

scan() هي async generator: كل سهم يتحقق شروطه يُرسَل فوراً (live) بدل
الانتظار حتى نهاية الفحص الكامل، ويتوقف الفحص تلقائياً بعد إيجاد
STOCKS_TOP_N نتيجة (خروج مبكر يوفر وقتاً أيضاً). قائمة المراقبة تُخلَط
عشوائياً في بداية كل فحص حتى لا تنحاز النتائج دائماً لنفس الأسهم الأولى
أبجدياً في STOCKS_WATCHLIST -- الترتيب هنا "أول ما يتحقق الشرط"، وليس
"الأفضل من بين كل السوق" (ذاك يتطلب مسح كل السوق قبل الإرسال).

أي خطأ في جلب أو تقييم سهم واحد لا يوقف بقية الفحص -- يُسجَّل ويُتجاوز.
"""
import asyncio
import logging
import math
import random
from collections.abc import AsyncIterator

from . import chart, config, data
from . import indicators
from .utils import fmt_price

log = logging.getLogger(__name__)

FILTER_NAMES = {
    "bollinger": "بولينجر السفلي",
    "rsi": "RSI تشبع بيعي",
    "support": "منطقة دعم",
    "wedge": "وتد هابط",
}


async def _spy_trend_multiplier() -> float:
    """SPY فوق متوسطه الحركي 50 يوماً -> STOCKS_TREND_UP_MULT، وإلا
    STOCKS_TREND_DOWN_MULT. 1.0 (محايد) لو تعذر جلب بيانات SPY -- خطأ في
    مؤشر الاتجاه العام لا يجب أن يوقف تقييم الأسهم نفسها."""
    try:
        frames = await asyncio.to_thread(data.fetch_batch, ["SPY"], "1d", "6mo")
        df = frames.get("SPY")
        if df is None or len(df) < config.STOCKS_TREND_SMA_PERIOD:
            return 1.0
        sma = df["Close"].tail(config.STOCKS_TREND_SMA_PERIOD).mean()
        price = float(df["Close"].iloc[-1])
        return config.STOCKS_TREND_UP_MULT if price > sma else config.STOCKS_TREND_DOWN_MULT
    except Exception:
        log.exception("SPY trend fetch failed; using neutral multiplier")
        return 1.0


def _score(df) -> tuple[list[str], float, dict] | None:
    """(matched_filters, raw_points_before_trend, details) أو None لو لم
    تتحقق STOCKS_FILTERS_REQUIRED فلاتر."""
    close = float(df["Close"].iloc[-1])

    rsi_series = indicators.rsi(df["Close"], config.STOCKS_RSI_PERIOD)
    rsi_val = rsi_series.iloc[-1]
    rsi_ok = not math.isnan(rsi_val)
    rsi_matched = rsi_ok and rsi_val < config.STOCKS_RSI_OVERSOLD

    bb_matched, bb_detail = indicators.check_bollinger_lower(
        df, config.STOCKS_BB_PERIOD, config.STOCKS_BB_STD, config.STOCKS_BB_TOLERANCE)
    lower, _, _ = indicators.bollinger(df["Close"], config.STOCKS_BB_PERIOD, config.STOCKS_BB_STD)
    last_lower = lower.iloc[-1]
    bb_gap = (close - last_lower) / last_lower if not math.isnan(last_lower) else None

    support_info = indicators.find_nearest_support_info(
        df, config.STOCKS_SUPPORT_LOOKBACK, config.STOCKS_WEDGE_PIVOT_ORDER,
        config.STOCKS_SUPPORT_CLUSTER_TOL, config.STOCKS_SUPPORT_MIN_TOUCHES,
        config.STOCKS_SUPPORT_MARGIN, config.STOCKS_SUPPORT_BREAK_TOL)
    support_matched = support_info is not None

    wedge_tier, wedge_detail = indicators.check_falling_wedge_tier(
        df, config.STOCKS_WEDGE_LOOKBACK, config.STOCKS_WEDGE_PIVOT_ORDER,
        config.STOCKS_WEDGE_MIN_BARS)
    wedge_matched = wedge_tier is not None

    matched_map = {"bollinger": bb_matched, "rsi": rsi_matched,
                   "support": support_matched, "wedge": wedge_matched}
    matched = [k for k, ok in matched_map.items() if ok]
    if len(matched) < config.STOCKS_FILTERS_REQUIRED:
        return None

    points = 0.0
    if rsi_ok:
        if rsi_val < 30:
            points += config.STOCKS_SCORE_RSI_STRONG
        elif rsi_val < 35:
            points += config.STOCKS_SCORE_RSI_WEAK
    if bb_gap is not None:
        if bb_gap <= 0.01:
            points += config.STOCKS_SCORE_BB_STRONG
        elif bb_gap <= 0.02:
            points += config.STOCKS_SCORE_BB_WEAK
    if support_info is not None:
        _, touches = support_info
        points += (config.STOCKS_SCORE_SUPPORT_STRONG if touches >= 3
                  else config.STOCKS_SCORE_SUPPORT_WEAK)
    if wedge_tier == "complete":
        points += config.STOCKS_SCORE_WEDGE_COMPLETE
    elif wedge_tier == "semi":
        points += config.STOCKS_SCORE_WEDGE_SEMI

    details = {
        "bollinger": bb_detail,
        "rsi": f"RSI={rsi_val:.1f}" if rsi_ok else "بيانات غير كافية",
        "support": f"دعم عند {fmt_price(support_info[0])} (اختُبر {support_info[1]} مرات)"
                   if support_info else "بعيد عن الدعم",
        "wedge": wedge_detail,
    }
    return matched, points, details


def _explain(matched: list[str], details: dict) -> str:
    parts = [details[k] for k in matched if details.get(k)]
    return "الشرح: " + "، ".join(parts) if parts else ""


def _evaluate(symbol: str, df, trend_mult: float) -> dict | None:
    if df["Volume"].tail(20).mean() < config.MIN_AVG_VOLUME:
        return None
    scored = _score(df)
    if scored is None:
        return None
    matched, points, details = scored

    probability = min(points * trend_mult, config.STOCKS_SCORE_CAP)
    if probability < config.STOCKS_MIN_POP:
        return None

    price = float(df["Close"].iloc[-1])
    resistance = indicators.find_nearest_resistance(
        df, config.STOCKS_SUPPORT_LOOKBACK, config.STOCKS_WEDGE_PIVOT_ORDER,
        config.STOCKS_SUPPORT_CLUSTER_TOL, config.STOCKS_SUPPORT_MIN_TOUCHES)
    if resistance is not None:
        profit_pct = (resistance - price) / price * 100
        target_note = f"مقاومة {fmt_price(resistance)}"
    else:
        profit_pct = 10.0
        target_note = "افتراضي +10%"

    return {
        "symbol": symbol,
        "price": price,
        "matched": matched,
        "total": len(FILTER_NAMES),
        "details": details,
        "explanation": _explain(matched, details),
        "profit_pct": profit_pct,
        "target_note": target_note,
        "resistance": resistance,
        "probability_of_profit": round(probability, 1),
        "chart_png": None,
    }


def _attach_chart(row: dict, df) -> None:
    try:
        support = indicators.find_nearest_support(
            df, config.STOCKS_SUPPORT_LOOKBACK, config.STOCKS_WEDGE_PIVOT_ORDER,
            config.STOCKS_SUPPORT_CLUSTER_TOL, config.STOCKS_SUPPORT_MIN_TOUCHES,
            config.STOCKS_SUPPORT_MARGIN, config.STOCKS_SUPPORT_BREAK_TOL)
        row["chart_png"] = chart.render_chart(
            row["symbol"], df, config.STOCKS_BB_PERIOD, config.STOCKS_BB_STD,
            support=support, resistance=row["resistance"])
    except Exception:
        log.exception("Chart attach failed for %s", row["symbol"])


async def scan(cancel_event: asyncio.Event | None = None,
               stats: dict | None = None) -> AsyncIterator[dict]:
    """يفحص أسهم STOCKS_WATCHLIST (بترتيب عشوائي) ويُرسل (yield) كل نتيجة
    فور تحققها، حتى STOCKS_TOP_N نتيجة أو نهاية القائمة أو /stop أو انتهاء
    الجلسة. `stats` غير مستخدم هنا (موجود فقط لتوحيد التوقيع مع
    options_module.scan)."""
    try:
        trend_mult = await _spy_trend_multiplier()
    except Exception:
        log.exception("Unexpected error computing SPY trend; using neutral multiplier")
        trend_mult = 1.0

    watchlist = list(config.STOCKS_WATCHLIST)
    random.shuffle(watchlist)
    sent = 0
    batches = data.make_batches(watchlist)
    for batch in batches:
        if cancel_event is not None and cancel_event.is_set():
            return
        if sent >= config.STOCKS_TOP_N:
            return
        try:
            frames = await asyncio.to_thread(
                data.fetch_batch, batch, config.STOCKS_INTERVAL, config.STOCKS_PERIOD)
        except Exception:
            log.exception("Stocks batch download failed (%s..)", batch[0])
            continue
        for symbol, df in frames.items():
            if cancel_event is not None and cancel_event.is_set():
                return
            if sent >= config.STOCKS_TOP_N:
                return
            try:
                row = _evaluate(symbol, df, trend_mult)
            except Exception:
                log.exception("Stocks evaluation failed for %s", symbol)
                continue
            if row is None:
                continue
            _attach_chart(row, df)
            yield row
            sent += 1


def format_result(row: dict) -> str:
    """نص كل نتيجة: سطران (السهم/السعر/الفلاتر، ثم الهدف والاحتمالية)،
    تليهما الشرح -- يُستخدم كنص أو كتعليق (caption) على صورة الرسم البياني."""
    matched_names = "، ".join(FILTER_NAMES[k] for k in row["matched"])
    line1 = (f"*{row['symbol']}* — {fmt_price(row['price'])} — "
             f"{len(row['matched'])}/{row['total']} ({matched_names})")
    line2 = (f"الهدف: {row['target_note']} ({row['profit_pct']:+.1f}%) • "
             f"🎯 احتمالية الربح: {row['probability_of_profit']:.0f}%")
    text = f"{line1}\n{line2}"
    if row.get("explanation"):
        text += f"\n{row['explanation']}"
    return text
