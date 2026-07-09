"""وحدة عقود الأوبشن (مستقلة تماماً عن وحدتَي الأسهم والكريبتو).

تفحص OPTIONS_WATCHLIST (قائمة منفصلة عن قائمة وحدة الأسهم) بحثاً عن عقود
CALL تحقق كل الشروط دفعة واحدة: دلتا بين OPTIONS_DELTA_MIN/MAX، أيام حتى
الانتهاء بين OPTIONS_DTE_MIN/MAX، سيولة (حجم/عقود مفتوحة) كافية، تقلب ضمني
أقل من OPTIONS_IV_MAX، سبريد عرض/طلب أقل من OPTIONS_SPREAD_MAX، وسعر الطلب
ضمن OPTIONS_ASK_MIN..OPTIONS_ASK_MAX للسهم الواحد.

لكل عقد مؤهل: التكلفة، نقطة التعادل، واحتمالية الربح (Probability of
Profit) -- وهي "نسبة الربح المحتملة" البارزة في نهاية كل نتيجة.

يدعم أيضاً فحص سهم واحد فقط (/options TICKER) بمعزل عن بقية القائمة.
أي خطأ في جلب أو تقييم عقود سهم واحد لا يوقف بقية الفحص.
"""
import asyncio
import datetime as dt
import logging
import time

from . import config, data, options, pricing, probability
from .indicators import fmt_price

log = logging.getLogger(__name__)


def _passes_filters(c: dict) -> bool:
    try:
        delta = c["delta"]
        iv = c["iv"]
        spread_pct = c["spread_pct"]
        ask = c["ask"]
        if delta is None or iv is None or spread_pct is None:
            return False
        delta = abs(delta)
        return (config.OPTIONS_DELTA_MIN <= delta <= config.OPTIONS_DELTA_MAX
                and config.OPTIONS_DTE_MIN <= c["days"] <= config.OPTIONS_DTE_MAX
                and c["volume"] >= config.OPTIONS_VOLUME_MIN
                and c["openInterest"] >= config.OPTIONS_OI_MIN
                and iv < config.OPTIONS_IV_MAX
                and spread_pct < config.OPTIONS_SPREAD_MAX
                and config.OPTIONS_ASK_MIN <= ask <= config.OPTIONS_ASK_MAX)
    except (KeyError, TypeError):
        return False


def _enrich(symbol: str, spot: float, c: dict) -> dict | None:
    strike, premium, iv, days = c["strike"], c["premium"], c["iv"], c["days"]
    be = pricing.breakeven(strike, premium)
    pop = probability.probability_of_profit(spot, be, days, iv)
    if pop is None:
        return None
    return {
        "symbol": symbol, "spot": spot,
        "strike": strike, "expiry": c["expiry"], "days": days,
        "premium": premium, "estimated": c["estimated"],
        "delta": c["delta"], "iv": iv,
        "cost": round(premium * 100, 2),
        "breakeven": round(be, 2),
        "probability_of_profit": round(pop, 1),
    }


def _contracts_for_symbol(symbol: str, spot: float) -> list[dict]:
    """Qualifying CALL contracts for one symbol, or [] if none/no options.
    Raises options.OptionsFetchError / options.NoNearTermOptions same as
    options.gather_candidates."""
    if options._no_options.get(symbol, 0) > time.time() - options.NO_OPTIONS_TTL:
        return []
    today = dt.date.today()
    cutoff = today + dt.timedelta(weeks=config.OPTIONS_MAX_WEEKS)
    candidates = options.gather_candidates(symbol, spot, today, cutoff)
    if candidates is None:
        options._no_options[symbol] = time.time()
        return []
    qualified = [c for c in candidates["call"] if _passes_filters(c)]
    enriched = [r for c in qualified if (r := _enrich(symbol, spot, c)) is not None]
    enriched = [r for r in enriched if r["probability_of_profit"] >= config.OPTIONS_MIN_POP]
    enriched.sort(key=lambda c: -c["probability_of_profit"])
    return enriched


async def scan_symbol(symbol: str) -> tuple[float | None, list[dict], str | None]:
    """(spot, contracts, error) لسهم واحد فقط -- يُستخدم في /options TICKER."""
    symbol = symbol.upper()
    try:
        frames = await asyncio.to_thread(data.fetch_batch, [symbol], "1d", "5d")
    except Exception:
        log.exception("Spot price fetch failed for %s", symbol)
        return None, [], "تعذر جلب سعر السهم."
    df = frames.get(symbol)
    if df is None:
        return None, [], "رمز غير معروف أو لا توجد بيانات له."
    spot = float(df["Close"].iloc[-1])
    try:
        contracts = await asyncio.to_thread(_contracts_for_symbol, symbol, spot)
    except options.NoNearTermOptions:
        return spot, [], f"لا توجد عقود ضمن {config.OPTIONS_MAX_WEEKS} أسبوع القادمة."
    except options.OptionsFetchError:
        return spot, [], "تعذر جلب سلسلة العقود (فشل المزوّدان)."
    except Exception:
        log.exception("Options lookup failed for %s", symbol)
        return spot, [], "خطأ غير متوقع أثناء الفحص."
    return spot, contracts[:config.OPTIONS_TOP_N], None


async def scan(cancel_event: asyncio.Event | None = None) -> list[dict]:
    """يفحص كل أسهم OPTIONS_WATCHLIST، ويرجع أفضل OPTIONS_TOP_N عقد إجمالاً
    (أعلى احتمالية ربح أولاً) عبر كل القائمة."""
    found: list[dict] = []
    batches = data.make_batches(config.OPTIONS_WATCHLIST)
    for batch in batches:
        if cancel_event is not None and cancel_event.is_set():
            break
        try:
            frames = await asyncio.to_thread(data.fetch_batch, batch, "1d", "5d")
        except Exception:
            log.exception("Options watchlist spot-price batch failed (%s..)", batch[0])
            continue
        for symbol, df in frames.items():
            if cancel_event is not None and cancel_event.is_set():
                break
            try:
                spot = float(df["Close"].iloc[-1])
                contracts = await asyncio.to_thread(_contracts_for_symbol, symbol, spot)
            except (options.OptionsFetchError, options.NoNearTermOptions):
                continue
            except Exception:
                log.exception("Options evaluation failed for %s", symbol)
                continue
            found.extend(contracts)

    found.sort(key=lambda c: -c["probability_of_profit"])
    return found[:config.OPTIONS_TOP_N]


def _explain(row: dict) -> str:
    return (f"الشرح: عقد CALL يمنحك حق شراء سهم {row['symbol']} بسعر تنفيذ "
            f"{row['strike']:.2f}$ حتى {row['expiry']}. بناءً على التقلب الضمني الحالي "
            f"({row['iv'] * 100:.0f}%) والأيام المتبقية ({row['days']} يوم)، احتمالية أن "
            f"يكون السهم فوق نقطة التعادل ({fmt_price(row['breakeven'])}) عند الانتهاء "
            f"هي {row['probability_of_profit']:.0f}%.")


def format_result(row: dict) -> str:
    """جدول نصي (monospace) لكل عقد، مع احتمالية الربح (نسبة الربح
    المحتملة) بارزة، يليه شرح مختصر."""
    approx = "≈" if row.get("estimated") else ""
    rows = [
        ("السهم", f"{row['symbol']} ({fmt_price(row['spot'])})"),
        ("تنفيذ (Strike)", f"{row['strike']:.2f}$"),
        ("الانتهاء", f"{row['expiry']} ({row['days']} يوم)"),
        ("بريميوم", f"{approx}{row['premium']:.2f}$"),
        ("تكلفة العقد", f"{approx}{row['cost']:.0f}$"),
        ("نقطة التعادل", fmt_price(row['breakeven'])),
        ("دلتا", f"{row['delta']:.2f}" if row['delta'] is not None else "-"),
        ("تقلب ضمني (IV)", f"{row['iv'] * 100:.0f}%" if row['iv'] is not None else "-"),
        ("🎯 احتمالية الربح", f"{row['probability_of_profit']:.0f}%"),
    ]
    label_w = max(len(label) for label, _ in rows)
    table = "\n".join(f"{label.ljust(label_w)} : {value}" for label, value in rows)
    return f"```\n{table}\n```\n{_explain(row)}"
