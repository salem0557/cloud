"""وحدة الأسهم الأمريكية (مستقلة تماماً عن وحدتَي الأوبشن والكريبتو).

تفحص STOCKS_WATCHLIST بحثاً عن إشارات ارتداد صعودي: بولينجر السفلي، RSI
تشبع بيعي، منطقة دعم، ووتد هابط -- يتطلب تحقق STOCKS_FILTERS_REQUIRED من
أصل 4. تُرجع أفضل STOCKS_TOP_N نتيجة، مرتبة بعدد الفلاتر المتحققة ثم بأعلى
"نسبة ربح محتملة" (المسافة إلى أقرب مقاومة، أو +10% افتراضياً بلا مقاومة
واضحة).

أي خطأ في جلب أو تقييم سهم واحد لا يوقف بقية الفحص -- يُسجَّل ويُتجاوز.
"""
import asyncio
import functools
import logging

from . import config, data
from .indicators import (check_bollinger_lower, check_falling_wedge,
                         check_rsi_oversold, check_support, fmt_price,
                         find_nearest_resistance)

log = logging.getLogger(__name__)

FILTER_NAMES = {
    "bollinger": "بولينجر السفلي",
    "rsi": "RSI تشبع بيعي",
    "support": "منطقة دعم",
    "wedge": "وتد هابط",
}


def _run_filters(df):
    """Evaluate all 4 stock filters against df with this module's own
    thresholds; returns {key: (matched, detail)}."""
    return {
        "bollinger": check_bollinger_lower(
            df, config.STOCKS_BB_PERIOD, config.STOCKS_BB_STD, config.STOCKS_BB_TOLERANCE),
        "rsi": check_rsi_oversold(
            df, config.STOCKS_RSI_PERIOD, config.STOCKS_RSI_OVERSOLD),
        "support": check_support(
            df, config.STOCKS_SUPPORT_LOOKBACK, config.STOCKS_WEDGE_PIVOT_ORDER,
            config.STOCKS_SUPPORT_CLUSTER_TOL, config.STOCKS_SUPPORT_MIN_TOUCHES,
            config.STOCKS_SUPPORT_MARGIN, config.STOCKS_SUPPORT_BREAK_TOL),
        "wedge": check_falling_wedge(
            df, config.STOCKS_WEDGE_LOOKBACK, config.STOCKS_WEDGE_PIVOT_ORDER,
            config.STOCKS_WEDGE_MIN_BARS),
    }


def _evaluate(symbol: str, df) -> dict | None:
    if not data.passes_liquidity(df):
        return None
    results = _run_filters(df)
    matched = [k for k, (ok, _) in results.items() if ok]
    if len(matched) < config.STOCKS_FILTERS_REQUIRED:
        return None

    price = float(df["Close"].iloc[-1])
    resistance = find_nearest_resistance(
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
        "details": {k: d for k, (_, d) in results.items()},
        "profit_pct": profit_pct,
        "target_note": target_note,
    }


async def scan(cancel_event: asyncio.Event | None = None) -> list[dict]:
    """يفحص كل أسهم STOCKS_WATCHLIST دفعة دفعة، ويرجع أفضل STOCKS_TOP_N نتيجة."""
    found: list[dict] = []
    batches = data.make_batches(config.STOCKS_WATCHLIST)
    for batch in batches:
        if cancel_event is not None and cancel_event.is_set():
            break
        try:
            frames = await asyncio.to_thread(
                data.fetch_batch, batch, config.STOCKS_INTERVAL, config.STOCKS_PERIOD)
        except Exception:
            log.exception("Stocks batch download failed (%s..)", batch[0])
            continue
        for symbol, df in frames.items():
            try:
                row = _evaluate(symbol, df)
            except Exception:
                log.exception("Stocks evaluation failed for %s", symbol)
                continue
            if row is not None:
                found.append(row)

    found.sort(key=lambda r: (len(r["matched"]), r["profit_pct"]), reverse=True)
    return found[:config.STOCKS_TOP_N]


def format_result(row: dict) -> str:
    """كل نتيجة بسطرين، مع نسبة الربح المحتملة بارزة في نهاية السطر الثاني."""
    matched_names = "، ".join(FILTER_NAMES[k] for k in row["matched"])
    line1 = (f"*{row['symbol']}* — {fmt_price(row['price'])} — "
             f"{len(row['matched'])}/{row['total']} ({matched_names})")
    line2 = f"{row['target_note']} • 🎯 نسبة الربح المحتملة: {row['profit_pct']:+.1f}%"
    return f"{line1}\n{line2}"
