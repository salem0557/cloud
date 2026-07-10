"""وحدة العملات الرقمية (مستقلة تماماً عن وحدتَي الأسهم والأوبشن).

تفحص CRYPTO_WATCHLIST (أعلى ~100 عملة بالقيمة السوقية عبر بيانات Binance
العامة، بدون مفاتيح API) على فريم 4 ساعات، بحثاً عن 3 شروط: بولينجر
السفلي، RSI تشبع بيعي، ومنطقة دعم -- يتطلب تحقق CRYPTO_FILTERS_REQUIRED من
أصل 3 (بلا وتد هابط، فقط للأسهم).

الاحتمالية هنا **نظام نقاط مرجّح** (heuristic score من 0 إلى
CRYPTO_SCORE_CAP)، بنفس منطق وحدة الأسهم، مع نقطة إضافية لحجم التداول
الشرائي المتزايد آخر 12 ساعة، ومعدَّلة باتجاه BTC العام (متوسطه الحركي 50
يوماً) -- انظر _score أدناه للصيغة الكاملة.

أي خطأ في جلب أو تقييم عملة واحدة لا يوقف بقية الفحص.
"""
import asyncio
import logging
import math

from . import chart, config, crypto_data
from . import indicators
from .utils import fmt_price, fmt_volume

log = logging.getLogger(__name__)

FILTER_NAMES = {
    "bollinger": "بولينجر السفلي",
    "rsi": "RSI تشبع بيعي",
    "support": "منطقة دعم",
}

# 4h candles, 24/7 market: 6 bars/day
BARS_PER_DAY = 6


async def _btc_trend_multiplier() -> float:
    """BTC فوق متوسطه الحركي 50 يوماً (شموع يومية، ليست 4 ساعات، حتى يطابق
    "50 يوم" حرفياً) -> CRYPTO_TREND_UP_MULT، وإلا CRYPTO_TREND_DOWN_MULT.
    1.0 (محايد) لو تعذر جلب بيانات BTC."""
    try:
        df = await asyncio.to_thread(
            crypto_data.fetch_ohlcv, "BTC/USDT", "1d", config.CRYPTO_TREND_SMA_PERIOD + 5)
        if df is None or len(df) < config.CRYPTO_TREND_SMA_PERIOD:
            return 1.0
        sma = df["Close"].tail(config.CRYPTO_TREND_SMA_PERIOD).mean()
        price = float(df["Close"].iloc[-1])
        return config.CRYPTO_TREND_UP_MULT if price > sma else config.CRYPTO_TREND_DOWN_MULT
    except Exception:
        log.exception("BTC trend fetch failed; using neutral multiplier")
        return 1.0


def _volume_increasing_12h(df) -> bool:
    """متوسط حجم آخر 3 شموع (12 ساعة) أعلى من متوسط الشموع الثلاث قبلها."""
    vol = df["Volume"]
    if len(vol) < 6:
        return False
    recent = vol.tail(3).mean()
    prior = vol.tail(6).head(3).mean()
    return bool(recent > prior)


def _score(df) -> tuple[list[str], float, dict] | None:
    close = float(df["Close"].iloc[-1])

    rsi_series = indicators.rsi(df["Close"], config.CRYPTO_RSI_PERIOD)
    rsi_val = rsi_series.iloc[-1]
    rsi_ok = not math.isnan(rsi_val)
    rsi_matched = rsi_ok and rsi_val < config.CRYPTO_RSI_OVERSOLD

    bb_matched, bb_detail = indicators.check_bollinger_lower(
        df, config.CRYPTO_BB_PERIOD, config.CRYPTO_BB_STD, config.CRYPTO_BB_TOLERANCE)
    lower, _, _ = indicators.bollinger(df["Close"], config.CRYPTO_BB_PERIOD, config.CRYPTO_BB_STD)
    last_lower = lower.iloc[-1]
    bb_gap = (close - last_lower) / last_lower if not math.isnan(last_lower) else None

    support_info = indicators.find_nearest_support_info(
        df, config.CRYPTO_SUPPORT_LOOKBACK, 3,
        config.CRYPTO_SUPPORT_CLUSTER_TOL, config.CRYPTO_SUPPORT_MIN_TOUCHES,
        config.CRYPTO_SUPPORT_MARGIN, config.CRYPTO_SUPPORT_BREAK_TOL)
    support_matched = support_info is not None

    matched_map = {"bollinger": bb_matched, "rsi": rsi_matched, "support": support_matched}
    matched = [k for k, ok in matched_map.items() if ok]
    if len(matched) < config.CRYPTO_FILTERS_REQUIRED:
        return None

    points = 0.0
    if rsi_ok:
        if rsi_val < 30:
            points += config.CRYPTO_SCORE_RSI_STRONG
        elif rsi_val < 35:
            points += config.CRYPTO_SCORE_RSI_WEAK
    if bb_gap is not None:
        if bb_gap <= 0.01:
            points += config.CRYPTO_SCORE_BB_STRONG
        elif bb_gap <= 0.02:
            points += config.CRYPTO_SCORE_BB_WEAK
    if support_info is not None:
        _, touches = support_info
        points += (config.CRYPTO_SCORE_SUPPORT_STRONG if touches >= 3
                  else config.CRYPTO_SCORE_SUPPORT_WEAK)
    volume_up = _volume_increasing_12h(df)
    if volume_up:
        points += config.CRYPTO_SCORE_VOLUME_INCREASE

    details = {
        "bollinger": bb_detail,
        "rsi": f"RSI={rsi_val:.1f}" if rsi_ok else "بيانات غير كافية",
        "support": f"دعم عند {fmt_price(support_info[0])} (اختُبر {support_info[1]} مرات)"
                   if support_info else "بعيد عن الدعم",
    }
    if volume_up:
        details["volume"] = "حجم تداول متزايد آخر 12 ساعة"
    return matched, points, details


def _explain(matched: list[str], details: dict) -> str:
    parts = [details[k] for k in matched if details.get(k)]
    if details.get("volume"):
        parts.append(details["volume"])
    return "الشرح: " + "، ".join(parts) if parts else ""


def _evaluate(symbol: str, df, trend_mult: float) -> dict | None:
    scored = _score(df)
    if scored is None:
        return None
    matched, points, details = scored

    probability = min(points * trend_mult, config.CRYPTO_SCORE_CAP)
    if probability < config.CRYPTO_MIN_POP:
        return None

    price = float(df["Close"].iloc[-1])
    resistance = indicators.find_nearest_resistance(
        df, config.CRYPTO_SUPPORT_LOOKBACK, 3,
        config.CRYPTO_SUPPORT_CLUSTER_TOL, config.CRYPTO_SUPPORT_MIN_TOUCHES)
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
        "volume_24h_usdt": None,
    }


async def scan(cancel_event: asyncio.Event | None = None) -> list[dict]:
    """يفحص كل عملات CRYPTO_WATCHLIST، ويرجع أفضل CRYPTO_TOP_N نتيجة مع رسم
    بياني لكل واحدة منها."""
    try:
        trend_mult = await _btc_trend_multiplier()
    except Exception:
        log.exception("Unexpected error computing BTC trend; using neutral multiplier")
        trend_mult = 1.0

    found: list[dict] = []
    for symbol in config.CRYPTO_WATCHLIST:
        if cancel_event is not None and cancel_event.is_set():
            break
        try:
            df = await asyncio.to_thread(
                crypto_data.fetch_ohlcv, symbol, config.CRYPTO_TIMEFRAME,
                config.CRYPTO_CANDLE_LIMIT)
        except Exception:
            log.exception("Crypto fetch failed for %s", symbol)
            continue
        if df is None or len(df) < config.CRYPTO_BB_PERIOD + 1:
            continue
        try:
            row = _evaluate(symbol, df, trend_mult)
        except Exception:
            log.exception("Crypto evaluation failed for %s", symbol)
            continue
        if row is not None:
            row["_df"] = df  # kept only long enough to render the chart below
            found.append(row)

    found.sort(key=lambda r: (len(r["matched"]), r["probability_of_profit"]), reverse=True)
    top = found[:config.CRYPTO_TOP_N]
    for row in top:
        df = row.pop("_df", None)
        if df is None or (cancel_event is not None and cancel_event.is_set()):
            continue
        try:
            support = indicators.find_nearest_support(
                df, config.CRYPTO_SUPPORT_LOOKBACK, 3,
                config.CRYPTO_SUPPORT_CLUSTER_TOL, config.CRYPTO_SUPPORT_MIN_TOUCHES,
                config.CRYPTO_SUPPORT_MARGIN, config.CRYPTO_SUPPORT_BREAK_TOL)
            row["chart_png"] = chart.render_chart(
                row["symbol"], df, config.CRYPTO_BB_PERIOD, config.CRYPTO_BB_STD,
                support=support, resistance=row["resistance"])
        except Exception:
            log.exception("Chart attach failed for %s", row["symbol"])
        try:
            row["volume_24h_usdt"] = await asyncio.to_thread(
                crypto_data.fetch_24h_quote_volume, row["symbol"])
        except Exception:
            log.exception("24h volume fetch failed for %s", row["symbol"])
    for row in found:
        row.pop("_df", None)
    return top


def format_result(row: dict) -> str:
    """نص كل نتيجة: سطران، تليهما السيولة (إن وُجدت) والشرح -- يُستخدم
    كنص أو كتعليق (caption) على صورة الرسم البياني."""
    matched_names = "، ".join(FILTER_NAMES[k] for k in row["matched"])
    line1 = (f"*{row['symbol']}* — {fmt_price(row['price'])} — "
             f"{len(row['matched'])}/{row['total']} ({matched_names})")
    line2 = (f"الهدف: {row['target_note']} ({row['profit_pct']:+.1f}%) • "
             f"🎯 احتمالية الربح: {row['probability_of_profit']:.0f}%")
    text = f"{line1}\n{line2}"
    vol = row.get("volume_24h_usdt")
    if vol is not None:
        text += f"\n💧 السيولة (حجم تداول 24 ساعة): {fmt_volume(vol)}"
    if row.get("explanation"):
        text += f"\n{row['explanation']}"
    return text
