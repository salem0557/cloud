"""تكامل Massive.com (api.massive.com) -- مزوّد بيانات سوق مجاني بحد 5
طلبات/دقيقة على الطبقة المجانية (بيانات متأخرة)، يُستخدم لثلاث ميزات
تشترك بعميل واحد مقيَّد المعدّل (_rate_limited_get):

1) رصد إدراج عقود CALL جديدة + عقود رخيصة ترتد (watch_options_signals):
   مهمة خلفية دائمة (بلا أمر يدوي، تبدأ من bot.py's post_init) تمسح
   NEW_LISTING_WATCHLIST بالتمهل -- **طلب Massive واحد فقط لكل سهم كل
   دورة** (GET /v3/snapshot/options/{symbol}، سلسلة العقود كاملة بردّ
   واحد) يخدم غرضين معاً بدل مضاعفة الاستهلاك:
     - الوجود: سهم كان بلا عقود آخر فحص وصار عنده عقود الآن = إدراج
       جديد 🎉 -- عندها يُقيَّم العقد الفعلي بخط أنابيب البوت الحالي
       تماماً (yfinance/CBOE + probability_module عبر
       options_module._contracts_for_symbol، بنفس فلاتر /options
       العامة) بدل استهلاك المزيد من حصة Massive على التسعير.
     - الارتداد: من نفس الرد، أي عقد CALL سائل (volume/open_interest
       >= عتبات /options العادية) سعره الحالي القابل للشراء فعلاً
       (last_quote.ask) ضمن سقف OPTIONS_ASK_MAX (رخيص) وتغيّره اليومي
       (day.change_percent، من نفس الرد -- بلا طلب إضافي) إيجابي
       بوضوح (>= BOUNCE_MIN_DAY_CHANGE_PCT) يُعتبر "عقد كان رخيصاً
       وبدأ يرتد اليوم" 📈 -- يُحسَب POP له عبر probability_module بنفس
       المنطق (سعر السهم يُجلب من yfinance مجاناً، لا من Massive).
   فشل مؤقت على رمز واحد لا يوقف الحلقة عن بقية القائمة.

2) حالة السوق والعطلات (market_status_line): تُستدعى مرة واحدة عند بدء
   أي جلسة يدوية (/stocks، /options، /crypto) كسطر معلوماتي يُرسَل بعد
   رسالة "بدأ الفحص" مباشرة -- لا تنتظرها الجلسة ولا تمنعها، ولو فشل
   الطلب أو تأخر (يتشارك نفس قيد الـ5/دقيقة مع رصد الإدراج) تُتجاهل
   بصمت.

3) مرشّح short squeeze لـ/stocks (short_squeeze_line): تُستدعى من
   bot.py **فقط لسهم عدّى فعلاً فلاتر /stocks المجانية (yfinance)** --
   طلب Massive واحد لهذا السهم تحديداً (GET /stocks/v1/short-interest)،
   وليس مسحاً لقائمة /stocks كاملة (744 رمزاً أوسع بكثير من ميزانية
   5/دقيقة). سهم عدّى العتبة (days_to_cover >= SHORT_SQUEEZE_MIN_DAYS_TO_COVER)
   يحصل على رسالة متابعة "🔥 مرشّح short squeeze" بعد رسالته العادية،
   بلا انتظار أو حجب لبقية الجلسة.

كل الميزة تتعطل تلقائياً وبأمان لو MASSIVE_API_KEY غير مضبوط -- ليست
شرطاً لتشغيل بقية البوت.
"""
import asyncio
import datetime as dt
import logging
import time
from collections.abc import AsyncIterator

import requests

from . import config, data, options, options_module, probability_module as pm, signals_db

log = logging.getLogger(__name__)

_last_call_monotonic = 0.0
_rate_lock = asyncio.Lock()


async def _rate_limited_get(path: str, params: dict) -> dict | list | None:
    """طلب GET واحد على Massive، مقيَّد المعدّل عالمياً (كل مستدعٍ --
    الحلقة الخلفية أو سطر حالة السوق -- يتشارك نفس القيد، لأنهما يستهلكان
    من نفس حصة المفتاح الواحد). يرجع None عند أي فشل (شبكة، حالة غير
    200، JSON غير صالح) -- لا يوقف أي مستدعٍ، فقط "لا بيانات هذه المرة"."""
    if not config.MASSIVE_API_KEY:
        return None
    async with _rate_lock:
        global _last_call_monotonic
        min_interval = 60.0 / config.MASSIVE_RATE_LIMIT_PER_MINUTE
        wait = _last_call_monotonic + min_interval - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call_monotonic = time.monotonic()

    try:
        resp = await asyncio.to_thread(
            requests.get, f"{config.MASSIVE_BASE_URL}{path}",
            params={**params, "apiKey": config.MASSIVE_API_KEY}, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except Exception:
        log.exception("Massive API request failed: %s", path)
        return None


# ------------------------------------------------------ new-listing watch

async def _fetch_chain_snapshot(symbol: str) -> list[dict] | None:
    """None = الطلب فشل (يُعاد المحاولة بالدورة التالية بلا لمس أي حالة
    مخزَّنة). خلاف ذلك، قائمة كل عقود السهم الحالية (فارغة لو بلا عقود
    إطلاقاً) -- ردّ واحد يخدم كلاً من فحص الإدراج الجديد وكشف الارتداد
    معاً، طلب Massive واحد فقط لكل سهم لكل دورة."""
    payload = await _rate_limited_get(f"/v3/snapshot/options/{symbol}", {})
    if not isinstance(payload, dict):
        return None
    results = payload.get("results")
    return results if isinstance(results, list) else []


async def _check_new_listing(symbol: str, has_now: bool) -> bool:
    """True فقط لو تحقق انتقال صريح من "بلا عقود" إلى "عنده عقود" منذ
    آخر فحص مخزَّن -- أول فحص لأي سهم إطلاقاً (had_before is None) لا
    يُعتبر إدراجاً جديداً أبداً، وإلا لأنبّه على كل سهم عنده عقود أصلاً
    فور أول تشغيلة."""
    had_before = await asyncio.to_thread(signals_db.get_listing_state, symbol)
    await asyncio.to_thread(signals_db.set_listing_state, symbol, has_now)
    return had_before is False and has_now


async def _evaluate_new_listing(symbol: str) -> dict | None:
    """بعد رصد إدراج جديد، يجلب سعر السهم وعقوده المؤهلة الفعلية عبر خط
    أنابيب البوت الحالي (وليس Massive) -- None لو تعذّر جلب السعر أو لم
    يعدِّ أي عقد فلاتر /options العامة بعد (الإدراج موجود لكن لا شيء
    يستحق تنبيهاً حالياً)."""
    try:
        frames = await asyncio.to_thread(data.fetch_batch, [symbol], "1d", "1mo")
        df = frames.get(symbol)
        if df is None or df.empty:
            return None
        spot = float(df["Close"].iloc[-1])
        contracts, _excluded = await asyncio.to_thread(
            options_module._contracts_for_symbol, symbol, spot, None)
    except (options.OptionsFetchError, options.NoNearTermOptions):
        return None
    except Exception:
        log.exception("New-listing evaluation failed for %s", symbol)
        return None
    return contracts[0] if contracts else None


def _row_from_snapshot_contract(symbol: str, spot: float, contract: dict) -> dict | None:
    """يبني صفاً بنفس شكل صفوف options_module (نفس الحقول اللي يحتاجها
    format_result) من عقد واحد داخل رد Option Chain Snapshot -- None لو
    العقد PUT، أو حقل أساسي ناقص، أو غير سائل (volume/open_interest أقل
    من OPTIONS_VOLUME_MIN/OPTIONS_OI_MIN -- نفس عتبات /options العادي)،
    أو لا يعدّي شرطي "رخيص + يرتد اليوم" (سعر ask <= OPTIONS_ASK_MAX
    وتغيّر يومي >= BOUNCE_MIN_DAY_CHANGE_PCT)، أو لم يحسب POP >=
    OPTIONS_MIN_POP فعلياً. السعر يُقرأ من last_quote.ask (السعر القابل
    للشراء فعلاً الآن) لا من day.close (آخر تداول، قد يكون قديماً) --
    كلاهما من نفس رد Snapshot المجلوب أصلاً، بلا أي طلب إضافي."""
    details = contract.get("details") or {}
    if details.get("contract_type") != "call":
        return None
    strike = details.get("strike_price")
    expiry = details.get("expiration_date")
    day = contract.get("day") or {}
    change_pct = day.get("change_percent")
    volume = day.get("volume")
    open_interest = contract.get("open_interest")
    premium = (contract.get("last_quote") or {}).get("ask")
    iv = contract.get("implied_volatility")
    if (strike is None or expiry is None or premium is None or change_pct is None
            or iv is None or volume is None or open_interest is None):
        return None
    if volume < config.OPTIONS_VOLUME_MIN or open_interest < config.OPTIONS_OI_MIN:
        return None
    if premium <= 0 or premium > config.OPTIONS_ASK_MAX:
        return None
    if change_pct < config.BOUNCE_MIN_DAY_CHANGE_PCT:
        return None
    try:
        days = (dt.date.fromisoformat(expiry) - dt.date.today()).days
    except ValueError:
        return None
    if days <= 0:
        return None

    be = pm.breakeven(strike, premium, is_call=True)
    pop = pm.probability_of_profit(spot, be, days, iv, is_call=True)
    if pop is None or pop < config.OPTIONS_MIN_POP:
        return None
    avg_profit = pm.expected_profit(spot * 1.10, strike, premium, days, iv, is_call=True)
    ev = pm.expected_value(pop, avg_profit, pm.max_loss(premium)) if avg_profit is not None else None

    return {
        "symbol": symbol, "spot": spot, "side": "call",
        "strike": strike, "expiry": expiry, "days": days,
        "premium": premium, "estimated": False,
        "delta": (contract.get("greeks") or {}).get("delta"), "iv": iv,
        "cost": round(premium * 100, 2),
        "breakeven": round(be, 2),
        "probability_of_profit": round(pop, 1),
        "expected_value": round(ev, 2) if ev is not None else None,
        "day_change_pct": change_pct,
    }


async def _bounce_candidates(symbol: str, contracts: list[dict]) -> list[dict]:
    """أسهم بلا سعر حالي (فشل جلب yfinance) تُتجاوز بصمت لهذه الدورة --
    ليست شرط وجود، فقط تعذّر مؤقت."""
    try:
        frames = await asyncio.to_thread(data.fetch_batch, [symbol], "1d", "1mo")
        df = frames.get(symbol)
        if df is None or df.empty:
            return []
        spot = float(df["Close"].iloc[-1])
    except Exception:
        return []
    rows = [r for c in contracts if (r := _row_from_snapshot_contract(symbol, spot, c)) is not None]
    rows.sort(key=lambda r: -r["day_change_pct"])
    return rows


async def scan_once() -> AsyncIterator[dict]:
    """تمسح NEW_LISTING_WATCHLIST مرة واحدة فقط (وليست حلقة أبدية) --
    تُرسل (yield) كل عقد إدراج جديد أو عقد رخيص يرتد فور رصده، كل صف
    مُعلَّم بـ"alert_kind" ("new_listing" | "bounce") ليختار format_alert
    العنوان المناسب. فشل مؤقت على رمز واحد لا يوقف المسح عن بقية القائمة.
    الأساس المشترك بين الحلقة الخلفية الدائمة (watch_options_signals)
    وأي تشغيلة يدوية فورية (bot.py's /newoptions) -- بلا تكرار منطق."""
    for symbol in config.NEW_LISTING_WATCHLIST:
        try:
            contracts = await _fetch_chain_snapshot(symbol)
        except Exception:
            log.exception("Chain snapshot fetch failed for %s", symbol)
            continue
        if contracts is None:
            continue
        has_now = bool(contracts)

        try:
            is_new_listing = await _check_new_listing(symbol, has_now)
        except Exception:
            log.exception("New-listing check failed for %s", symbol)
            is_new_listing = False
        if is_new_listing:
            row = await _evaluate_new_listing(symbol)
            if row is not None:
                yield {**row, "alert_kind": "new_listing"}

        if not has_now:
            continue
        for row in await _bounce_candidates(symbol, contracts):
            yield {**row, "alert_kind": "bounce"}


async def watch_options_signals() -> AsyncIterator[dict]:
    """حلقة خلفية دائمة (لا تتوقف طالما البوت شغّال) تعيد scan_once باستمرار
    بترتيبها بالتمهل (فاصل زمني محكوم بقيد المعدّل العالمي، طلب Massive
    واحد فقط لكل سهم كل دورة). تنتهي فوراً بصمت (بعد تحذير واحد) لو
    MASSIVE_API_KEY غير مضبوط."""
    if not config.MASSIVE_API_KEY:
        log.warning("MASSIVE_API_KEY not set -- options signal watch disabled")
        return
    while True:
        async for row in scan_once():
            yield row


def format_alert(row: dict) -> str:
    if row.get("alert_kind") == "bounce":
        header = f"📈 عقد رخيص يرتد — *{row['symbol']}* (+{row['day_change_pct']:.0f}% اليوم)"
    else:
        header = f"🎉 عقود جديدة أُدرجت — *{row['symbol']}*"
    body = options_module.format_result(row)
    return f"{header}\n{body}"


# --------------------------------------------------------- market status

_STATUS_LABEL_AR = {
    "open": "🟢 السوق مفتوح الآن",
    "extended-hours": "🟡 تداول ما بعد/قبل الجلسة الرسمية",
    "closed": "🔴 السوق مغلق الآن",
}


async def market_status_line() -> str | None:
    """سطر معلوماتي واحد جاهز للإرسال، أو None لو تعذّر الجلب (لا مفتاح،
    فشل شبكة، رد غير متوقّع) -- المستدعي يتجاهله بصمت في هذه الحالة، لا
    يعرض خطأ للعضو. يجمع حالة السوق الحيّة (/v1/marketstatus/now) مع
    تقاطع تاريخ اليوم مع قائمة العطلات القادمة (/v1/marketstatus/upcoming)
    لو توفّرت."""
    status = await _rate_limited_get("/v1/marketstatus/now", {})
    if not isinstance(status, dict):
        return None
    market = status.get("market")
    label = _STATUS_LABEL_AR.get(market, f"⚪ حالة السوق: {market}" if market else None)
    if label is None:
        return None

    today = dt.date.today().isoformat()
    holidays = await _rate_limited_get("/v1/marketstatus/upcoming", {})
    if isinstance(holidays, list):
        today_holiday = next(
            (h for h in holidays if isinstance(h, dict) and h.get("date") == today), None)
        if today_holiday and today_holiday.get("name"):
            label += f" — 🎌 عطلة اليوم: {today_holiday['name']}"

    return label


# -------------------------------------------------- short squeeze (/stocks)

async def short_interest(symbol: str) -> dict | None:
    """أحدث صف short interest لسهم واحد (FINRA، كل أسبوعين) -- None لو
    تعذّر الجلب أو لا بيانات لهذا الرمز. حقول الرد: avg_daily_volume،
    days_to_cover، settlement_date، short_interest، ticker."""
    payload = await _rate_limited_get(
        "/stocks/v1/short-interest",
        {"ticker": symbol, "limit": 1, "sort": "settlement_date.desc"})
    if not isinstance(payload, dict):
        return None
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        return None
    return results[0]


async def short_squeeze_line(symbol: str) -> str | None:
    """سطر تنبيه جاهز للإرسال لو عدّى هذا السهم عتبة
    SHORT_SQUEEZE_MIN_DAYS_TO_COVER، وإلا None بصمت (لا يعني بالضرورة
    "لا يوجد short interest" -- قد يكون موجوداً لكن دون العتبة). يُستدعى
    فقط لسهم عدّى فعلاً فلاتر /stocks المجانية أصلاً -- انظر docstring
    الملف."""
    data = await short_interest(symbol)
    if data is None:
        return None
    days_to_cover = data.get("days_to_cover")
    if days_to_cover is None or days_to_cover < config.SHORT_SQUEEZE_MIN_DAYS_TO_COVER:
        return None
    shares = data.get("short_interest")
    settlement = data.get("settlement_date", "-")
    shares_line = f"الأسهم المباعة على المكشوف: {shares:,}" if shares is not None else ""
    parts = [
        f"🔥 *{symbol}* — مرشّح short squeeze محتمل (Massive.com)",
        f"أيام التغطية (days to cover): {days_to_cover:.1f}",
    ]
    if shares_line:
        parts.append(shares_line)
    parts.append(f"تاريخ التسوية: {settlement}")
    parts.append("⚠️ مؤشر إحصائي (بيانات FINRA كل أسبوعين) وليس ضماناً لحركة السعر.")
    return "\n".join(parts)
