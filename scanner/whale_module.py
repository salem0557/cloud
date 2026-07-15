"""كاشف النشاط الشاذ (whale): يرصد بصمات صفقات CALL ضخمة محتملة من نسبة
حجم اليوم إلى العقود المفتوحة (Vol/OI) عبر قائمة WHALE_TICKERS (نفس
HEAVY_TICKERS)، من نفس مصدري yfinance/CBOE الحاليين -- بلا أي اشتراك
بيانات مدفوع.

CALL فقط، مطابقة لبقية البوت الذي لا يدعم PUT إطلاقاً (انظر options.py) --
"نشاط شاذ" هنا يعني رهاناً صعودياً محتملاً دائماً، وليس مقارنة مع الجهة
المقابلة. الاتجاه استنتاجي دائماً وليس مؤكداً (قد يكون تحوطاً لا رهاناً) --
انظر DISCLAIMER أدناه، يُلحق بكل تنبيه.

لا يوجد أمر /whales يدوي عمداً -- هذه وظيفة خلفية بحتة (bot.py's
job_queue.run_repeating، بنفس نمط _position_monitor_job الحالي بالضبط)،
تعمل تلقائياً طالما البوت شغّال وتدفع تنبيهاً مباشراً لكل عضو معتمد حالياً
فور رصد شيء جديد، بدل انتظار أحد يكتب أمراً.
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

DISCLAIMER = "⚠️ الاتجاه استنتاجي - قد يكون تحوطًا لا رهانًا"
TIER_LABEL = {
    "notable": "🟡 ملحوظ",
    "unusual": "🟠 شاذ - رهان جديد مرجّح",
    "whale": "🐋 حوت شبه مؤكد",
}


def _classify(ratio: float) -> str | None:
    """التصنيف تراكمي (نسبة 12 تصنَّف "حوت"، أعلى تصنيف تحققه فقط) --
    "ملحوظ" مصنَّف هنا لاكتماله لكنه لا يُنبَّه عليه أبداً فعلياً، انظر
    _contracts_for_symbol (يشترط >= WHALE_RATIO_UNUSUAL)."""
    if ratio > config.WHALE_RATIO_WHALE:
        return "whale"
    if ratio > config.WHALE_RATIO_UNUSUAL:
        return "unusual"
    if ratio > config.WHALE_RATIO_NOTABLE:
        return "notable"
    return None


def _row(symbol: str, spot: float, c: dict, ratio: float, tier: str, flow_usd: float) -> dict:
    strike = c["strike"]
    return {
        "symbol": symbol, "spot": spot, "side": "call",
        "strike": strike, "expiry": c["expiry"], "days": c["days"],
        "premium": c["premium"], "estimated": c["estimated"],
        "volume": c["volume"], "openInterest": c["openInterest"],
        "iv": c["iv"], "delta": c["delta"],
        "ratio": round(ratio, 1), "tier": tier,
        "flow_usd": round(flow_usd, 0),
        "moneyness": "ITM" if strike < spot else "OTM",
        "pct_from_spot": round(abs(strike - spot) / spot * 100, 1),
    }


def _contracts_for_symbol(symbol: str, spot: float) -> list[dict]:
    """(عقود CALL شاذة لسهم واحد) -- تجتمع فيها كل فلاتر تنقية الإشارة معاً
    (ratio، حجم، قيمة تدفق، DTE). عقد يعدّي التصنيف لكن ما يجتمع مع بقية
    الفلاتر يُستبعد بصمت. مرتبة بأعلى نسبة Vol/OI أولاً. تستخدم نفس
    options.gather_candidates الخام (بلا فلاتر IV/دلتا/POP -- هذي وحدة
    كشف حجم شاذ، وليست ترتيب "أفضل عقد")، بنفس أسلوب heavy_module (CBOE
    أولاً لسلسلة كاملة بطلب واحد)."""
    if options._no_options.get(symbol, 0) > time.time() - options.NO_OPTIONS_TTL:
        return []
    today = dt.date.today()
    cutoff = today + dt.timedelta(days=config.WHALE_DTE_MAX)
    try:
        candidates, _excluded = options.gather_candidates(
            symbol, spot, today, cutoff,
            max_expiries=config.HEAVY_MAX_EXPIRIES, min_days=config.WHALE_DTE_MIN,
            providers=(options._cboe_candidates, options._yahoo_candidates))
    except (options.OptionsFetchError, options.NoNearTermOptions):
        return []
    if candidates is None:
        options._no_options[symbol] = time.time()
        return []

    results = []
    for c in candidates.get("call", []):
        oi = c["openInterest"]
        if oi < config.WHALE_MIN_OI:
            continue
        ratio = c["volume"] / oi
        tier = _classify(ratio)
        if tier not in ("unusual", "whale"):
            continue
        if c["volume"] < config.WHALE_MIN_VOLUME:
            continue
        flow_usd = c["volume"] * c["premium"] * 100
        if flow_usd < config.WHALE_MIN_FLOW_USD:
            continue
        results.append(_row(symbol, spot, c, ratio, tier, flow_usd))
    results.sort(key=lambda r: -r["ratio"])
    return results


async def scan() -> AsyncIterator[dict]:
    """async generator تمسح WHALE_TICKERS كاملة (ترتيب عشوائي) وتُرسل
    (yield) كل عقد شاذ مؤهل فوراً. لا يوجد cancel_event/مهلة جلسة هنا
    عمداً -- هذي ليست جلسة يدوية يشغّلها عضو، بل تشغيلة واحدة يستدعيها
    bot.py's job الدوري وتنتهي من تلقاء نفسها."""
    watchlist = list(config.WHALE_TICKERS)
    random.shuffle(watchlist)
    for batch in data.make_batches(watchlist):
        try:
            frames = await asyncio.to_thread(data.fetch_batch, batch, "1d", "1mo")
        except Exception:
            log.exception("Whale watchlist batch failed (%s..)", batch[0])
            continue
        for symbol, df in frames.items():
            if df is None or df.empty:
                continue
            try:
                spot = float(df["Close"].iloc[-1])
                contracts = await asyncio.to_thread(_contracts_for_symbol, symbol, spot)
            except (options.OptionsFetchError, options.NoNearTermOptions):
                continue
            except Exception:
                log.exception("Whale evaluation failed for %s", symbol)
                continue
            for c in contracts:
                yield c


def format_alert(row: dict) -> str:
    approx = "≈" if row.get("estimated") else ""
    header = f"🐋 نشاط شاذ *{row['symbol']}* — {TIER_LABEL[row['tier']]}"
    rows = [
        ("السهم", f"{row['symbol']} ({fmt_price(row['spot'])})"),
        ("النوع", "🟢 CALL (رهان صعود محتمل)"),
        ("تنفيذ (Strike)", f"{row['strike']:.2f}$"),
        ("الموقع", f"{row['moneyness']} ({row['pct_from_spot']:.1f}% عن السعر)"),
        ("الانتهاء", f"{row['expiry']} ({row['days']} يوم)"),
        ("نسبة Vol/OI", f"{row['ratio']:.1f}x"),
        ("الحجم اليوم", f"{row['volume']:,}"),
        ("العقود المفتوحة", f"{row['openInterest']:,}"),
        ("قيمة التدفق التقريبية", f"≈{row['flow_usd']:,.0f}$"),
        ("السعر (Premium)", f"{approx}{row['premium']:.2f}$"),
        ("تقلب ضمني (IV)", f"{row['iv'] * 100:.0f}%" if row.get('iv') is not None else "-"),
    ]
    label_w = max(len(label) for label, _ in rows)
    table = "\n".join(f"{label.ljust(label_w)} : {value}" for label, value in rows)
    return f"{header}\n```\n{table}\n```\n{DISCLAIMER}"
