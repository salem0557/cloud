"""وحدة /heavy: فحص عقود CALL فقط (بلا PUT) على قائمة مختارة (HEAVY_TICKERS)
من أضخم الأسهم والصناديق الأكثر سيولة -- Mega caps، Large caps راسخة،
وETFs كبرى. تشكّل هذه الوحدة أحد ثلاثة "أنواع" يجمعها أمر /options الموحّد
واحداً (انظر options_module.scan_all) -- قائمتها (HEAVY_TICKERS) وشروطها
الخاصة (مدى DTE أوسع بلا سقف أعلى، نطاق strike أصرم، سقف بريميوم) تبقى
مستقلة، لكنها تشارك حدود دلتا/تقلب ضمني (OPTIONS_DELTA_MIN/OPTIONS_IV_MAX)
مع النوعين الآخرين (العادي وLEAPS).

شروط كل عقد (مجتمعة، بلا توسيع):
- DTE >= HEAVY_DTE_MIN (45 يوم)، بلا سقف أعلى -- يشمل أي LEAPS متوفر
  بالسلسلة الفعلية (2027، 2028، أو أبعد).
- Strike ضمن ±HEAVY_STRIKE_PCT (10%) من السعر الحالي.
- Ask <= HEAVY_PREMIUM_MAX (3.00$).
- Volume >= HEAVY_VOLUME_MIN و Open Interest >= HEAVY_OI_MIN.
- Spread < HEAVY_SPREAD_MAX (10%).
- تقلب ضمني (IV) < OPTIONS_IV_MAX ودلتا >= OPTIONS_DELTA_MIN (الحدود
  الموحّدة المشتركة مع النوعين الآخرين).
- احتمالية الربح (POP، Black-Scholes) >= OPTIONS_MIN_POP -- نفس حد
  القبول المستخدم بالنوع العادي، حتى يحمل كل عقد HEAVY تصنيف 🥇/🥈/🥉
  موحّداً معه (انظر probability_module.tier_label).

لتجنّب مئات الطلبات الشبكية لكل سهم عند سحب سلاسل بعيدة المدى (خصوصاً
الصناديق كثيفة العقود الأسبوعية مثل SPY/QQQ، حيث كل تاريخ استحقاق عبر
Yahoo يكلّف طلب شبكة مستقل)، يُفضَّل مزوّد CBOE أولاً هنا -- يرجع السلسلة
كاملة بطلب واحد فقط فلا حاجة لسقف عدد تواريخ استحقاق -- مع Yahoo كاحتياطي
بسقف كبير (HEAVY_MAX_EXPIRIES) لو تعطّل CBOE.

سهم بلا أي عقد يحقق الشروط مجتمعة يُتجاوز بصمت (لا رسالة خطأ لكل سهم).

scan() هي async generator: كل عقد يتحقق شروطه يُرسَل فوراً (live)، بلا سقف
على عدد النتائج -- يمسح HEAVY_TICKERS كاملة حتى نهاية القائمة أو /stop أو
انتهاء الجلسة. القائمة تُخلَط عشوائياً في بداية كل فحص لتفادي أي ترتيب
ثابت بالإرسال. الترتيب المطلوب هو "الأعلى سيولة أولاً" (volume +
openInterest) -- يُطبَّق محلياً داخل عقود السهم الواحد فقط قبل إرسالها،
لأن الترتيب الشامل عبر كل القائمة يتطلب مسحها كاملة قبل الإرسال (نفس مبدأ
/leaps مع IV).
"""
import asyncio
import datetime as dt
import logging
import random
import time
from collections.abc import AsyncIterator

from . import config, data, options, probability_module as pm
from .utils import fmt_price

log = logging.getLogger(__name__)

TYPE_TAG = "🟢 CALL (رهان صعود)"
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
        iv = c["iv"]
        delta = c["delta"]
        if (strike is None or ask is None or spread_pct is None
                or iv is None or delta is None or spot <= 0):
            return False
        if abs(strike - spot) / spot > config.HEAVY_STRIKE_PCT:
            return False
        return (c["days"] >= config.HEAVY_DTE_MIN
                and ask <= config.HEAVY_PREMIUM_MAX
                and c["volume"] >= config.HEAVY_VOLUME_MIN
                and c["openInterest"] >= config.HEAVY_OI_MIN
                and spread_pct < config.HEAVY_SPREAD_MAX
                and iv < config.OPTIONS_IV_MAX
                and abs(delta) >= config.OPTIONS_DELTA_MIN)
    except (KeyError, TypeError):
        return False


def _row(symbol: str, spot: float, category: str, c: dict, pop: float, be: float) -> dict:
    return {
        "symbol": symbol, "spot": spot, "category": category,
        "side": "call",
        "strike": c["strike"], "expiry": c["expiry"], "days": c["days"],
        "premium": c["premium"], "estimated": c["estimated"],
        "bid": c["bid"], "ask": c["ask"], "spread_pct": c["spread_pct"],
        "volume": c["volume"], "openInterest": c["openInterest"],
        "iv": c["iv"], "delta": c["delta"],
        "cost": round(c["premium"] * 100, 2),
        "liquidity": c["volume"] + c["openInterest"],
        "breakeven": round(be, 2),
        "probability_of_profit": round(pop, 1),
    }


def _contracts_for_symbol(symbol: str, spot: float, category: str) -> tuple[list[dict], int]:
    """(عقود CALL مؤهلة لسهم واحد، عدد العقود المستبعدة لبيانات غير
    موثوقة). مرتبة محلياً بأعلى سيولة أولاً. عقد يعدّي الفلاتر الهيكلية
    (_passes_filters) لكن احتمالية ربحه أقل من OPTIONS_MIN_POP يُستبعد هنا
    أيضاً -- نفس حد القبول المستخدم بالنوع العادي، لتوحيد تصنيف 🥇/🥈/🥉
    عبر الأنواع الثلاثة. Raises options.OptionsFetchError /
    options.NoNearTermOptions same as options.gather_candidates."""
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
    for c in candidates.get("call", []):
        if not _passes_filters(c, spot):
            continue
        be = pm.breakeven(c["strike"], c["premium"], is_call=True)
        pop = pm.probability_of_profit(spot, be, c["days"], c["iv"], is_call=True)
        if pop is None or pop < config.OPTIONS_MIN_POP:
            continue
        results.append(_row(symbol, spot, category, c, pop, be))
    results.sort(key=lambda r: -r["liquidity"])
    return results, excluded


async def scan(cancel_event: asyncio.Event | None = None,
               stats: dict | None = None) -> AsyncIterator[dict]:
    """يفحص كامل HEAVY_TICKERS (بترتيب عشوائي، بلا خروج مبكر) ويُرسل (yield)
    كل عقد مؤهل فور تحققه، حتى نهاية القائمة أو /stop أو انتهاء الجلسة.
    `stats["excluded_bad_data"]` يتجمّع بالمرجع."""
    if stats is None:
        stats = {}
    watchlist = list(config.HEAVY_TICKERS)
    random.shuffle(watchlist)
    batches = data.make_batches(watchlist)
    for batch in batches:
        if cancel_event is not None and cancel_event.is_set():
            return
        try:
            frames = await asyncio.to_thread(data.fetch_batch, batch, "1d", "1mo")
        except Exception:
            log.exception("Heavy watchlist batch failed (%s..)", batch[0])
            continue
        for symbol, df in frames.items():
            if cancel_event is not None and cancel_event.is_set():
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
                yield c


def format_result(row: dict) -> str:
    """جدول نصي (monospace) لكل عقد -- وسم الفئة (MEGA/LARGE/ETF)، نوع
    العقد، وتصنيف احتمالية الربح (🥇/🥈/🥉، موحّد مع النوعين الآخرين) بارزة
    بالعنوان، والسيولة (حجم/عقود مفتوحة) ضمن الجدول لأنها معيار الترتيب
    هنا (وليست الاحتمالية، رغم أنها الآن شرط قبول أيضاً)."""
    approx = "≈" if row.get("estimated") else ""
    header = (f"{CATEGORY_TAG[row['category']]} {TYPE_TAG} "
             f"*{row['symbol']}* — {pm.tier_label(row['probability_of_profit'])}")
    rows = [
        ("السهم", f"{row['symbol']} ({fmt_price(row['spot'])})"),
        ("تنفيذ (Strike)", f"{row['strike']:.2f}$"),
        ("الانتهاء", f"{row['expiry']} ({row['days']} يوم)"),
        ("المدة", _duration_tag(row['days'])),
        ("Bid / Ask", f"{approx}{row['bid']:.2f}$ / {approx}{row['ask']:.2f}$"),
        ("تكلفة العقد", f"{approx}{row['cost']:.0f}$"),
        ("نقطة التعادل", fmt_price(row['breakeven'])),
        ("دلتا", f"{row['delta']:.2f}" if row.get('delta') is not None else "-"),
        ("تقلب ضمني (IV)", f"{row['iv'] * 100:.0f}%" if row.get('iv') is not None else "-"),
        ("الحجم", f"{row['volume']:,}"),
        ("العقود المفتوحة", f"{row['openInterest']:,}"),
        ("السبريد", f"{row['spread_pct'] * 100:.1f}%" if row['spread_pct'] is not None else "-"),
        ("🎯 احتمالية الربح", f"{row['probability_of_profit']:.0f}%"),
    ]
    label_w = max(len(label) for label, _ in rows)
    table = "\n".join(f"{label.ljust(label_w)} : {value}" for label, value in rows)
    return f"{header}\n```\n{table}\n```"
