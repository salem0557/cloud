"""وحدة عقود الأوبشن -- Call و Put معاً (مستقلة تماماً عن وحدتَي الأسهم
والكريبتو).

تفحص OPTIONS_WATCHLIST (قائمة منفصلة عن قائمة وحدة الأسهم، ~500 سهم من
الأنشط بسوق العقود) بحثاً عن عقود Call و/أو Put تحقق كل الشروط دفعة واحدة:
دلتا (بالقيمة المطلقة) بين OPTIONS_DELTA_MIN/MAX، أيام حتى الانتهاء بين
OPTIONS_DTE_MIN/MAX، سيولة (حجم/عقود مفتوحة) كافية، تقلب ضمني أقل من
OPTIONS_IV_MAX، سبريد عرض/طلب أقل من OPTIONS_SPREAD_MAX، وسعر الطلب ضمن
OPTIONS_ASK_MIN..OPTIONS_ASK_MAX للسهم الواحد.

لكل عقد مؤهل: احتمالية الربح (Probability of Profit، Black-Scholes عبر
scipy -- N(d2) للـCall وN(-d2) للـPut، انظر probability_module.py)، والقيمة
المتوقعة (EV) بناءً على الربح المتوقع عند أقرب مقاومة (Call) أو أقرب دعم
(Put) للسهم مقابل أقصى خسارة ممكنة (البريميوم المدفوع).

يدعم فحص القائمة كاملة (كول+بوت معاً أو كول فقط أو بوت فقط عبر sides)،
وفحص سهم واحد فقط (/options TICKER) بمعزل عن بقية القائمة. أي خطأ في جلب
أو تقييم عقود سهم واحد لا يوقف بقية الفحص.
"""
import asyncio
import datetime as dt
import logging
import time

from . import config, data, options, probability_module as pm
from .indicators import find_nearest_resistance, find_nearest_support_below
from .utils import fmt_price

log = logging.getLogger(__name__)

TYPE_TAG = {"call": "🟢 CALL (رهان صعود)", "put": "🔴 PUT (رهان هبوط)"}


def _duration_tag(days: int) -> str:
    if days <= config.OPTIONS_DURATION_SHORT_MAX:
        return "🕐 قصير - انتبه للوقت"
    if days <= config.OPTIONS_DURATION_MEDIUM_MAX:
        return "📅 متوسط - المنطقة المريحة"
    return "🗓️ طويل (LEAPS) - أغلى لكن أهدأ"


def _tier_label(pop: float) -> str:
    if pop >= config.OPTIONS_TIER_GOLD:
        return "🥇 ممتاز - نادر"
    if pop >= config.OPTIONS_TIER_SILVER:
        return "🥈 جيد جداً"
    return "🥉 مقبول"


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


def _enrich(symbol: str, spot: float, c: dict, is_call: bool,
           target: float | None) -> dict | None:
    strike, premium, iv, days = c["strike"], c["premium"], c["iv"], c["days"]
    be = pm.breakeven(strike, premium, is_call)
    pop = pm.probability_of_profit(spot, be, days, iv, is_call)
    if pop is None or pop < config.OPTIONS_MIN_POP:
        return None

    # متوسط الربح المحتمل = الربح الصافي المتوقع لو وصل السهم لأقرب مقاومة
    # (Call) أو أقرب دعم (Put)؛ +10%/-10% افتراضياً بلا مستوى واضح.
    if target is None:
        target = spot * 1.10 if is_call else spot * 0.90
    avg_profit = pm.expected_profit(target, strike, premium, days, iv, is_call)
    loss = pm.max_loss(premium)
    ev = pm.expected_value(pop, avg_profit, loss) if avg_profit is not None else None

    side = "call" if is_call else "put"
    return {
        "symbol": symbol, "spot": spot, "side": side,
        "strike": strike, "expiry": c["expiry"], "days": days,
        "premium": premium, "estimated": c["estimated"],
        "delta": c["delta"], "iv": iv,
        "cost": round(premium * 100, 2),
        "breakeven": round(be, 2),
        "probability_of_profit": round(pop, 1),
        "expected_value": round(ev, 2) if ev is not None else None,
    }


def _contracts_for_symbol(symbol: str, spot: float, df, sides: tuple[str, ...]) -> list[dict]:
    """عقود Call و/أو Put مؤهلة لسهم واحد، مرتبة بأعلى احتمالية ربح ثم أطول
    مدة عند التساوي. Raises options.OptionsFetchError /
    options.NoNearTermOptions same as options.gather_candidates."""
    if options._no_options.get(symbol, 0) > time.time() - options.NO_OPTIONS_TTL:
        return []
    today = dt.date.today()
    cutoff = today + dt.timedelta(weeks=config.OPTIONS_MAX_WEEKS)
    candidates = options.gather_candidates(symbol, spot, today, cutoff)
    if candidates is None:
        options._no_options[symbol] = time.time()
        return []

    resistance = support = None
    if df is not None:
        try:
            resistance = find_nearest_resistance(df, 250, 3, 0.01, 2)
            support = find_nearest_support_below(df, 250, 3, 0.01, 2)
        except Exception:
            log.exception("Resistance/support lookup failed for %s", symbol)

    results = []
    for side in sides:
        is_call = side == "call"
        target = resistance if is_call else support
        qualified = [c for c in candidates.get(side, []) if _passes_filters(c)]
        for c in qualified:
            enriched = _enrich(symbol, spot, c, is_call, target)
            if enriched is not None:
                results.append(enriched)

    results.sort(key=lambda r: (-r["probability_of_profit"], -r["days"]))
    return results


async def scan_symbol(symbol: str, sides: tuple[str, ...] = ("call", "put")
                      ) -> tuple[float | None, list[dict], str | None]:
    """(spot, contracts, error) لسهم واحد فقط -- يُستخدم في /options TICKER."""
    symbol = symbol.upper()
    try:
        frames = await asyncio.to_thread(data.fetch_batch, [symbol], "1d", "6mo")
    except Exception:
        log.exception("Spot price fetch failed for %s", symbol)
        return None, [], "تعذر جلب سعر السهم."
    df = frames.get(symbol)
    if df is None:
        return None, [], "رمز غير معروف أو لا توجد بيانات له."
    spot = float(df["Close"].iloc[-1])
    try:
        contracts = await asyncio.to_thread(_contracts_for_symbol, symbol, spot, df, sides)
    except options.NoNearTermOptions:
        return spot, [], f"لا توجد عقود ضمن {config.OPTIONS_MAX_WEEKS} أسبوع القادمة."
    except options.OptionsFetchError:
        return spot, [], "تعذر جلب سلسلة العقود (فشل المزوّدان)."
    except Exception:
        log.exception("Options lookup failed for %s", symbol)
        return spot, [], "خطأ غير متوقع أثناء الفحص."
    return spot, contracts[:config.OPTIONS_TOP_N], None


async def scan(cancel_event: asyncio.Event | None = None,
               sides: tuple[str, ...] = ("call", "put")) -> list[dict]:
    """يفحص كل أسهم OPTIONS_WATCHLIST، ويرجع أفضل OPTIONS_TOP_N عقد إجمالاً
    (أعلى احتمالية ربح أولاً، ثم أطول مدة عند التساوي)."""
    found: list[dict] = []
    batches = data.make_batches(config.OPTIONS_WATCHLIST)
    for batch in batches:
        if cancel_event is not None and cancel_event.is_set():
            break
        try:
            frames = await asyncio.to_thread(data.fetch_batch, batch, "1d", "6mo")
        except Exception:
            log.exception("Options watchlist batch failed (%s..)", batch[0])
            continue
        for symbol, df in frames.items():
            if cancel_event is not None and cancel_event.is_set():
                break
            try:
                spot = float(df["Close"].iloc[-1])
                contracts = await asyncio.to_thread(
                    _contracts_for_symbol, symbol, spot, df, sides)
            except (options.OptionsFetchError, options.NoNearTermOptions):
                continue
            except Exception:
                log.exception("Options evaluation failed for %s", symbol)
                continue
            found.extend(contracts)

    found.sort(key=lambda r: (-r["probability_of_profit"], -r["days"]))
    return found[:config.OPTIONS_TOP_N]


def format_result(row: dict) -> str:
    """جدول نصي (monospace) لكل عقد، مع نوع العقد ودرجة الاحتمالية بارزة
    قبله، والمدة والقيمة المتوقعة (EV) ضمن الجدول."""
    approx = "≈" if row.get("estimated") else ""
    header = f"{TYPE_TAG[row['side']]} *{row['symbol']}* — {_tier_label(row['probability_of_profit'])}"
    rows = [
        ("السهم", f"{row['symbol']} ({fmt_price(row['spot'])})"),
        ("تنفيذ (Strike)", f"{row['strike']:.2f}$"),
        ("الانتهاء", f"{row['expiry']} ({row['days']} يوم)"),
        ("المدة", _duration_tag(row['days'])),
        ("بريميوم", f"{approx}{row['premium']:.2f}$"),
        ("تكلفة العقد", f"{approx}{row['cost']:.0f}$"),
        ("نقطة التعادل", fmt_price(row['breakeven'])),
        ("دلتا", f"{row['delta']:.2f}" if row['delta'] is not None else "-"),
        ("تقلب ضمني (IV)", f"{row['iv'] * 100:.0f}%" if row['iv'] is not None else "-"),
        ("القيمة المتوقعة (EV)",
         f"{row['expected_value']:+.0f}$" if row['expected_value'] is not None else "-"),
        ("🎯 احتمالية الربح", f"{row['probability_of_profit']:.0f}%"),
    ]
    label_w = max(len(label) for label, _ in rows)
    table = "\n".join(f"{label.ljust(label_w)} : {value}" for label, value in rows)

    ev = row.get("expected_value")
    ev_note = ""
    if ev is not None:
        ev_note = f"\n{'📈 قيمة متوقعة إيجابية' if ev > 0 else '📉 قيمة متوقعة سلبية'}"
    return f"{header}\n```\n{table}\n```{ev_note}"
