"""وحدة /heavy: فحص عقود Call وPut على قائمة مختارة (HEAVY_TICKERS) من
أضخم الأسهم والصناديق الأكثر سيولة -- Mega caps، Large caps راسخة،
وETFs كبرى -- مستقلة تماماً عن /options العامة (قائمة مختلفة، وشروطها
الخاصة: مدى DTE أوسع بلا سقف أعلى، ونطاق strike أصرم).

شروط كل عقد (مجتمعة، بلا توسيع):
- DTE >= HEAVY_DTE_MIN (45 يوم)، بلا سقف أعلى -- يشمل أي LEAPS متوفر
  بالسلسلة الفعلية (2027، 2028، أو أبعد).
- Strike ضمن ±HEAVY_STRIKE_PCT (10%) من السعر الحالي.
- Ask <= HEAVY_PREMIUM_MAX (2.00$).
- Volume >= HEAVY_VOLUME_MIN و Open Interest >= HEAVY_OI_MIN.
- Spread < HEAVY_SPREAD_MAX (10%).

لتجنّب مئات الطلبات الشبكية لكل سهم عند سحب سلاسل بعيدة المدى (خصوصاً
الصناديق كثيفة العقود الأسبوعية مثل SPY/QQQ، حيث كل تاريخ استحقاق عبر
Yahoo يكلّف طلب شبكة مستقل)، يُفضَّل مزوّد CBOE أولاً هنا -- يرجع السلسلة
كاملة بطلب واحد فقط فلا حاجة لسقف عدد تواريخ استحقاق -- مع Yahoo كاحتياطي
بسقف كبير (HEAVY_MAX_EXPIRIES) لو تعطّل CBOE.

سهم بلا أي عقد يحقق الشروط مجتمعة يُتجاوز بصمت (لا رسالة خطأ لكل سهم).

scan() هي async generator: كل عقد يتحقق شروطه يُرسَل فوراً (live)، ويتوقف
الفحص تلقائياً بعد HEAVY_TOP_N نتيجة. القائمة تُخلَط عشوائياً في بداية كل
فحص لنفس سبب العدالة المستخدم في بقية الوحدات. الترتيب المطلوب هو "الأعلى
سيولة أولاً" (volume + openInterest) -- يُطبَّق محلياً داخل عقود السهم
الواحد فقط قبل إرسالها، لأن الترتيب الشامل عبر كل القائمة يتطلب مسحها
كاملة قبل الإرسال (نفس مبدأ /leaps مع IV).
"""
import asyncio
import datetime as dt
import logging
import random
import time
from collections.abc import AsyncIterator

from . import config, data, options
from .utils import fmt_price

log = logging.getLogger(__name__)

TYPE_TAG = {"call": "🟢 CALL (رهان صعود)", "put": "🔴 PUT (رهان هبوط)"}
CATEGORY_TAG = {"mega": "🏛️ MEGA", "large": "🏢 LARGE", "etf": "📦 ETF"}

# Fetch window ceiling -- generous enough that HEAVY_DTE_MIN/HEAVY_MAX_EXPIRIES
# (not this) are what actually bound the search; 5 years comfortably covers
# any LEAPS chain currently listed anywhere.
_FETCH_WINDOW_DAYS = 365 * 5


def _duration_tag(days: int) -> str:
    if days <= config.OPTIONS_DURATION_SHORT_MAX:
        return "🕐 قصير"
    if days <= config.OPTIONS_DURATION_MEDIUM_MAX:
        return "📅 متوسط"
    if days <= 730:
        return "🗓️ طويل (LEAPS)"
    return "🗓️🗓️ LEAPS بعيد"


def _passes_filters(c: dict, spot: float) -> bool:
    try:
        strike = c["strike"]
        ask = c["ask"]
        spread_pct = c["spread_pct"]
        if strike is None or ask is None or spread_pct is None or spot <= 0:
            return False
        if abs(strike - spot) / spot > config.HEAVY_STRIKE_PCT:
            return False
        return (c["days"] >= config.HEAVY_DTE_MIN
                and ask <= config.HEAVY_PREMIUM_MAX
                and c["volume"] >= config.HEAVY_VOLUME_MIN
                and c["openInterest"] >= config.HEAVY_OI_MIN
                and spread_pct < config.HEAVY_SPREAD_MAX)
    except (KeyError, TypeError):
        return False


def _row(symbol: str, spot: float, category: str, c: dict, is_call: bool) -> dict:
    return {
        "symbol": symbol, "spot": spot, "category": category,
        "side": "call" if is_call else "put",
        "strike": c["strike"], "expiry": c["expiry"], "days": c["days"],
        "premium": c["premium"], "estimated": c["estimated"],
        "bid": c["bid"], "ask": c["ask"], "spread_pct": c["spread_pct"],
        "volume": c["volume"], "openInterest": c["openInterest"],
        "cost": round(c["premium"] * 100, 2),
        "liquidity": c["volume"] + c["openInterest"],
    }


def _contracts_for_symbol(symbol: str, spot: float, category: str) -> tuple[list[dict], int]:
    """(عقود Call/Put مؤهلة لسهم واحد، عدد العقود المستبعدة لبيانات غير
    موثوقة). مرتبة محلياً بأعلى سيولة أولاً. Raises options.OptionsFetchError
    / options.NoNearTermOptions same as options.gather_candidates."""
    if options._no_options.get(symbol, 0) > time.time() - options.NO_OPTIONS_TTL:
        return [], 0
    today = dt.date.today()
    cutoff = today + dt.timedelta(days=_FETCH_WINDOW_DAYS)
    candidates, excluded = options.gather_candidates(
        symbol, spot, today, cutoff,
        max_expiries=config.HEAVY_MAX_EXPIRIES, min_days=config.HEAVY_DTE_MIN,
        providers=(options._cboe_candidates, options._yahoo_candidates))
    if candidates is None:
        options._no_options[symbol] = time.time()
        return [], excluded

    results = []
    for side in ("call", "put"):
        is_call = side == "call"
        for c in candidates.get(side, []):
            if _passes_filters(c, spot):
                results.append(_row(symbol, spot, category, c, is_call))

    results.sort(key=lambda r: -r["liquidity"])
    return results, excluded


async def scan(cancel_event: asyncio.Event | None = None,
               stats: dict | None = None) -> AsyncIterator[dict]:
    """يفحص HEAVY_TICKERS (بترتيب عشوائي) ويُرسل (yield) كل عقد مؤهل فور
    تحققه، حتى HEAVY_TOP_N عقد أو نهاية القائمة أو /stop أو انتهاء الجلسة.
    `stats["excluded_bad_data"]` يتجمّع بالمرجع."""
    if stats is None:
        stats = {}
    watchlist = list(config.HEAVY_TICKERS)
    random.shuffle(watchlist)
    sent = 0
    batches = data.make_batches(watchlist)
    for batch in batches:
        if cancel_event is not None and cancel_event.is_set():
            return
        if sent >= config.HEAVY_TOP_N:
            return
        try:
            frames = await asyncio.to_thread(data.fetch_batch, batch, "1d", "1mo")
        except Exception:
            log.exception("Heavy watchlist batch failed (%s..)", batch[0])
            continue
        for symbol, df in frames.items():
            if cancel_event is not None and cancel_event.is_set():
                return
            if sent >= config.HEAVY_TOP_N:
                return
            try:
                spot = float(df["Close"].iloc[-1])
                category = config.HEAVY_TAG.get(symbol, "large")
                contracts, excluded = await asyncio.to_thread(
                    _contracts_for_symbol, symbol, spot, category)
            except (options.OptionsFetchError, options.NoNearTermOptions):
                continue
            except Exception:
                log.exception("Heavy evaluation failed for %s", symbol)
                continue
            stats["excluded_bad_data"] = stats.get("excluded_bad_data", 0) + excluded
            for c in contracts:
                if sent >= config.HEAVY_TOP_N:
                    return
                yield c
                sent += 1


def format_result(row: dict) -> str:
    """جدول نصي (monospace) لكل عقد -- وسم الفئة (MEGA/LARGE/ETF) ونوع
    العقد والمدة بارزة بالعنوان، والسيولة (حجم/عقود مفتوحة) ضمن الجدول
    لأنها معيار الترتيب هنا بدل احتمالية الربح."""
    approx = "≈" if row.get("estimated") else ""
    header = (f"{CATEGORY_TAG[row['category']]} {TYPE_TAG[row['side']]} "
             f"*{row['symbol']}* — {_duration_tag(row['days'])}")
    rows = [
        ("السهم", f"{row['symbol']} ({fmt_price(row['spot'])})"),
        ("تنفيذ (Strike)", f"{row['strike']:.2f}$"),
        ("الانتهاء", f"{row['expiry']} ({row['days']} يوم)"),
        ("Bid / Ask", f"{approx}{row['bid']:.2f}$ / {approx}{row['ask']:.2f}$"),
        ("تكلفة العقد", f"{approx}{row['cost']:.0f}$"),
        ("الحجم", f"{row['volume']:,}"),
        ("العقود المفتوحة", f"{row['openInterest']:,}"),
        ("السبريد", f"{row['spread_pct'] * 100:.1f}%" if row['spread_pct'] is not None else "-"),
    ]
    label_w = max(len(label) for label, _ in rows)
    table = "\n".join(f"{label.ljust(label_w)} : {value}" for label, value in rows)
    return f"{header}\n```\n{table}\n```"
