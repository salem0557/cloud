"""تكامل Massive.com (api.massive.com) -- مزوّد بيانات سوق مجاني بحد 5
طلبات/دقيقة على الطبقة المجانية (بيانات متأخرة)، يُستخدم لميزتين
مستقلتين تماماً تشتركان بعميل واحد مقيَّد المعدّل (_rate_limited_get):

1) رصد إدراج عقود CALL جديدة (watch_new_listings): مهمة خلفية دائمة
   (بلا أمر يدوي، تبدأ من bot.py's post_init وتعمل طالما البوت شغّال)
   تمسح NEW_LISTING_WATCHLIST بالتمهل -- طلب واحد فقط لكل سهم كل دورة
   (GET /v3/reference/options/contracts?underlying_ticker=X&limit=1) فقط
   للتحقق "هل عند هذا السهم أي عقد مُدرَج الآن؟"، بلا أي بيانات تسعير من
   Massive إطلاقاً. سهم كان بلا عقود آخر فحص وصار عنده عقود الآن = إدراج
   جديد -- عندها تُقيَّم عقوده الفعلية بخط أنابيب البوت الحالي تماماً
   (yfinance/CBOE + probability_module عبر options_module._contracts_for_symbol،
   بنفس فلاتر /options العامة: OPTIONS_ASK_MAX 200$/عقد وOPTIONS_MIN_POP
   45% فأعلى) بدل استهلاك المزيد من حصة Massive الضئيلة على التسعير.

2) حالة السوق والعطلات (market_status_line): تُستدعى مرة واحدة عند بدء
   أي جلسة يدوية (/stocks، /options، /crypto) كسطر معلوماتي يُرسَل بعد
   رسالة "بدأ الفحص" مباشرة -- لا تنتظرها الجلسة ولا تمنعها، ولو فشل
   الطلب أو تأخر (يتشارك نفس قيد الـ5/دقيقة مع رصد الإدراج) تُتجاهل
   بصمت.

كل الميزة تتعطل تلقائياً وبأمان لو MASSIVE_API_KEY غير مضبوط -- ليست
شرطاً لتشغيل بقية البوت.
"""
import asyncio
import datetime as dt
import logging
import time
from collections.abc import AsyncIterator

import requests

from . import config, data, options, options_module, signals_db

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

async def has_options_contracts(symbol: str) -> bool | None:
    """None = الطلب فشل (يُعاد المحاولة بالدورة التالية بلا لمس الحالة
    المخزَّنة). خلاف ذلك: هل عند هذا السهم عقد مُدرَج واحد على الأقل
    الآن -- limit=1 يكفي، لا حاجة لأكثر من عقد واحد للتحقق من الوجود."""
    payload = await _rate_limited_get(
        "/v3/reference/options/contracts", {"underlying_ticker": symbol, "limit": 1})
    if not isinstance(payload, dict):
        return None
    return bool(payload.get("results"))


async def _check_new_listing(symbol: str) -> bool:
    """True فقط لو تحقق انتقال صريح من "بلا عقود" إلى "عنده عقود" منذ
    آخر فحص مخزَّن -- أول فحص لأي سهم إطلاقاً (had_before is None) لا
    يُعتبر إدراجاً جديداً أبداً، وإلا لأنبّه على كل سهم عنده عقود أصلاً
    فور أول تشغيلة."""
    has_now = await has_options_contracts(symbol)
    if has_now is None:
        return False
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


async def watch_new_listings() -> AsyncIterator[dict]:
    """حلقة خلفية دائمة (لا تتوقف طالما البوت شغّال) تمسح
    NEW_LISTING_WATCHLIST بترتيبها بالتمهل (فاصل زمني محكوم بقيد المعدّل
    العالمي)، وتُرسل (yield) كل عقد إدراج جديد مؤهل فور رصده. فشل مؤقت
    على رمز واحد (شبكة، بيانات غير موثوقة) لا يوقف الحلقة عن بقية
    القائمة -- تتجاوزه وتكمل. تنتهي فوراً بصمت (بعد تحذير واحد) لو
    MASSIVE_API_KEY غير مضبوط."""
    if not config.MASSIVE_API_KEY:
        log.warning("MASSIVE_API_KEY not set -- new-listing watch disabled")
        return
    while True:
        for symbol in config.NEW_LISTING_WATCHLIST:
            try:
                is_new = await _check_new_listing(symbol)
            except Exception:
                log.exception("New-listing check failed for %s", symbol)
                continue
            if not is_new:
                continue
            row = await _evaluate_new_listing(symbol)
            if row is not None:
                yield row


def format_new_listing_alert(row: dict) -> str:
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
