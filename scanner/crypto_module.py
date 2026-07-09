"""وحدة العملات الرقمية (مستقلة تماماً عن وحدتَي الأسهم والأوبشن).

تفحص CRYPTO_WATCHLIST (أعلى ~60 عملة بالقيمة السوقية عبر بيانات Binance
العامة، بدون مفاتيح API) على فريم 4 ساعات، بحثاً عن 3 شروط: بولينجر
السفلي، RSI تشبع بيعي، ومنطقة دعم -- يتطلب تحقق CRYPTO_FILTERS_REQUIRED من
أصل 3 (بلا وتد هابط، فقط للأسهم)، وأن تحقق احتمالية ربح (نفس نموذج وحدة
الأسهم اللوغاريتمي، بتقلب تاريخي محسوب من شموع 4 الساعات) لا تقل عن
CRYPTO_MIN_POP.

أي خطأ في جلب أو تقييم عملة واحدة لا يوقف بقية الفحص.
"""
import asyncio
import logging

from . import chart, config, crypto_data, probability
from .indicators import (check_bollinger_lower, check_rsi_oversold,
                         check_support, fmt_price, find_nearest_resistance,
                         find_nearest_support)

# 4h candles, 24/7 market: 6 bars/day * 365 days/year
BARS_PER_YEAR = 6 * 365

log = logging.getLogger(__name__)

FILTER_NAMES = {
    "bollinger": "بولينجر السفلي",
    "rsi": "RSI تشبع بيعي",
    "support": "منطقة دعم",
}


def _run_filters(df):
    return {
        "bollinger": check_bollinger_lower(
            df, config.CRYPTO_BB_PERIOD, config.CRYPTO_BB_STD, config.CRYPTO_BB_TOLERANCE),
        "rsi": check_rsi_oversold(
            df, config.CRYPTO_RSI_PERIOD, config.CRYPTO_RSI_OVERSOLD),
        "support": check_support(
            df, config.CRYPTO_SUPPORT_LOOKBACK, 3,
            config.CRYPTO_SUPPORT_CLUSTER_TOL, config.CRYPTO_SUPPORT_MIN_TOUCHES,
            config.CRYPTO_SUPPORT_MARGIN, config.CRYPTO_SUPPORT_BREAK_TOL),
    }


def _explain(matched: list[str], details: dict) -> str:
    parts = [details[k] for k in matched if details.get(k)]
    return "الشرح: " + "، ".join(parts) if parts else ""


def _evaluate(symbol: str, df) -> dict | None:
    results = _run_filters(df)
    matched = [k for k, (ok, _) in results.items() if ok]
    if len(matched) < config.CRYPTO_FILTERS_REQUIRED:
        return None

    price = float(df["Close"].iloc[-1])
    details = {k: d for k, (_, d) in results.items()}
    resistance = find_nearest_resistance(
        df, config.CRYPTO_SUPPORT_LOOKBACK, 3,
        config.CRYPTO_SUPPORT_CLUSTER_TOL, config.CRYPTO_SUPPORT_MIN_TOUCHES)
    if resistance is not None:
        profit_pct = (resistance - price) / price * 100
        target_note = f"مقاومة {fmt_price(resistance)}"
    else:
        profit_pct = 10.0
        target_note = "افتراضي +10%"
    target_price = resistance if resistance is not None else price * 1.10

    vol = probability.realized_volatility(df["Close"].to_numpy(), bars_per_year=BARS_PER_YEAR)
    pop = probability.probability_of_profit(
        price, target_price, config.CRYPTO_PROFIT_HORIZON_DAYS, vol)
    if pop is None or pop < config.CRYPTO_MIN_POP:
        return None

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
        "probability_of_profit": round(pop, 1),
        "chart_png": None,
        "volume_24h_usdt": None,
    }


def _fmt_volume(v: float) -> str:
    if v >= 1e9:
        return f"{v / 1e9:.2f}B$"
    if v >= 1e6:
        return f"{v / 1e6:.1f}M$"
    if v >= 1e3:
        return f"{v / 1e3:.1f}K$"
    return f"{v:.0f}$"


async def scan(cancel_event: asyncio.Event | None = None) -> list[dict]:
    """يفحص كل عملات CRYPTO_WATCHLIST، ويرجع أفضل CRYPTO_TOP_N نتيجة مع رسم
    بياني لكل واحدة منها."""
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
            row = _evaluate(symbol, df)
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
        if df is None or cancel_event is not None and cancel_event.is_set():
            continue
        try:
            support = find_nearest_support(
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
    """نص كل نتيجة: سطران، تليهما الشرح -- يُستخدم كنص أو كتعليق (caption)
    على صورة الرسم البياني."""
    matched_names = "، ".join(FILTER_NAMES[k] for k in row["matched"])
    line1 = (f"*{row['symbol']}* — {fmt_price(row['price'])} — "
             f"{len(row['matched'])}/{row['total']} ({matched_names})")
    line2 = (f"الهدف: {row['target_note']} ({row['profit_pct']:+.1f}%) • "
             f"🎯 احتمالية الربح: {row['probability_of_profit']:.0f}%")
    text = f"{line1}\n{line2}"
    vol = row.get("volume_24h_usdt")
    if vol is not None:
        text += f"\n💧 السيولة (حجم تداول 24 ساعة): {_fmt_volume(vol)}"
    if row.get("explanation"):
        text += f"\n{row['explanation']}"
    return text
