"""الإشارة الذهبية (Confluence): بعد كل جلسة /stocks، لكل سهم أُرسل وحقق
عتبة GOLDEN_STOCKS_FILTERS_REQUIRED (3 من 4 افتراضياً -- أصرم من عتبة
/stocks العامة STOCKS_FILTERS_REQUIRED=2)، يُفحص فحص أوبشن مصغر (سهم واحد
فقط) بنفس فلاتر /options العامة تماماً (options_module._contracts_for_symbol).
لو وُجد عقد CALL مؤهل، تُرسَل رسالة "⭐ إشارة ذهبية" واحدة تجمع تفاصيل السهم
والعقد معاً، وتُسجَّل بـsignals.db تحت قسم مستقل "golden" حتى يتتبع /stats
أداءها بمعزل عن /stocks و/options العاديين. لو رصد whale_module (وظيفة
خلفية مستقلة، انظره) كول شاذاً على نفس السهم خلال GOLDEN_WHALE_LOOKBACK_DAYS
الماضية، يُستبدل العنوان بـ"⭐⭐⭐ تعافي بدعم حوت" الأقوى.

مستقل تماماً عن جلسة /options العامة: لا يمس OPTIONS_WATCHLIST ولا يبدأ
جلسة/مهلة منفصلة -- مجرد فحص إضافي يُشغَّل بعد جلسة /stocks نفسها (على كل
سهم حقق عتبة الذهبية، مهما كان عددهم -- /stocks بلا خروج مبكر الآن)، من
نفس bot.py's _run_watchlist_session.
"""
import asyncio
import logging

from . import config, options_module, signals_db as db, stocks_module

log = logging.getLogger(__name__)


def stock_qualifies_for_golden(stock_row: dict) -> bool:
    return len(stock_row.get("matched", [])) >= config.GOLDEN_STOCKS_FILTERS_REQUIRED


async def check_confluence(stock_row: dict) -> dict | None:
    """(golden_row | None) -- the best qualifying CALL contract (highest
    POP, options_module's own ranking) for this stock's own ticker,
    enriched with the stock's matched-filter context for formatting. None
    if no contract passes every /options filter for this symbol right
    now. A fetch/evaluation failure is this stock's problem alone -- it
    never aborts the rest of the golden pass (bot.py loops over several
    stocks)."""
    symbol = stock_row["symbol"]
    spot = stock_row["price"]
    try:
        contracts, _excluded = await asyncio.to_thread(
            options_module._contracts_for_symbol, symbol, spot, None)
    except Exception:
        log.exception("Golden confluence check failed for %s", symbol)
        return None
    if not contracts:
        return None

    golden = dict(contracts[0])
    golden["stock_matched"] = stock_row.get("matched", [])
    golden["stock_explanation"] = stock_row.get("explanation", "")
    golden["stock_probability"] = stock_row.get("probability_of_profit")
    golden["whale_backed"] = await asyncio.to_thread(
        db.recent_whale_call, symbol, config.GOLDEN_WHALE_LOOKBACK_DAYS)
    return golden


def format_golden_result(row: dict) -> str:
    """السهم أولاً (الفلاتر المتحققة) ثم جدول العقد الكامل بنفس شكل
    options_module.format_result -- رسالة واحدة، وليس رسالتين منفصلتين،
    لأن الفكرة كلها هي التقاء الإشارتين معاً. سهم ذهبي عليه أيضاً كول شاذ
    رصده whale_module خلال GOLDEN_WHALE_LOOKBACK_DAYS الماضية يحصل على
    عنوان مطوّر (⭐⭐⭐) بدل العادي (⭐) -- انظر check_confluence's
    whale_backed."""
    stock_filters_ar = "، ".join(
        stocks_module.FILTER_NAMES.get(f, f) for f in row.get("stock_matched", []))
    stock_line = f"📈 السهم: {stock_filters_ar}"
    if row.get("stock_explanation"):
        stock_line += f" — {row['stock_explanation']}"
    if row.get("whale_backed"):
        header = f"⭐⭐⭐ *تعافي بدعم حوت* — {row['symbol']}"
    else:
        header = f"⭐ *إشارة ذهبية* — {row['symbol']}"
    option_table = options_module.format_result(row)
    return f"{header}\n{stock_line}\n\n{option_table}"
