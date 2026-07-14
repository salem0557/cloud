"""بيانات وحدة السوق العام للوحة الويب (`scanner/webapp.py`) -- حالة السوق
(تفاؤل/حذر/خوف) وأخبار السوق، عبر بيانات yfinance العامة نفسها المستخدمة
في بقية البوت (لا مفتاح API إضافي، لا تكلفة).

مستقلة تماماً عن آلية الفحص (stocks/options/crypto) -- لا تُسجَّل إشاراتها
بـ signals.db ولا تمر بجلسات /stop، فقط دالتان تُستدعيان عند تحميل صفحة
لوحة الويب. نتائجهما مخزَّنة مؤقتاً (TTL بسيط بالذاكرة، DASHBOARD_CACHE_SECONDS)
حتى لا يُحمَّل yfinance بطلب جديد كل مرة يحدّث فيها أحد المتصفح.
"""
import asyncio
import logging
import time

import yfinance as yf

from . import config, data

log = logging.getLogger(__name__)

_cache: dict[str, tuple[float, object]] = {}


def _cached(key: str):
    hit = _cache.get(key)
    if hit is not None and time.time() - hit[0] < config.DASHBOARD_CACHE_SECONDS:
        return hit[1]
    return None


def _store(key: str, value):
    _cache[key] = (time.time(), value)
    return value


# VIX bands (typical convention: <15 quiet, 15-20 normal, 20-28 tense,
# 28+ elevated fear) combined with SPY vs its own 50-day SMA to produce one
# composite mood label -- pure threshold logic, no model, no external call.
def _classify_mood(spy_above_sma: bool | None, vix: float | None) -> tuple[str, str]:
    """(mood_label, vix_band_label). None for either input means that half
    of the picture is missing (not "bearish"/"unknown pretending to be
    known") -- callers with both None should treat the read as
    unavailable rather than trust this function's guess."""
    if vix is None:
        band = "غير متاح"
        if spy_above_sma is None:
            return "غير متاح", band
        return ("متوازن (بلا مؤشر VIX)" if spy_above_sma else "حذر (بلا مؤشر VIX)"), band
    if vix >= 28:
        return "خوف مرتفع", "مرتفع جداً"
    if vix >= 20:
        return ("حذر" if spy_above_sma else "حذر متزايد"), "مرتفع"
    if vix >= 15:
        return ("متفائل بحذر" if spy_above_sma else "متوازن"), "طبيعي"
    return ("متفائل" if spy_above_sma else "متوازن (تقلب منخفض)"), "هادئ"


async def market_status() -> dict:
    """حالة السوق العام الآن: اتجاه SPY (فوق/تحت المتوسط الحركي 50 يوماً)
    ومستوى مؤشر الخوف VIX، مدمجَين بقاعدة بسيطة إلى تصنيف واحد (متفائل/
    متوازن/حذر/خوف مرتفع) مع فقرة شرح قصيرة. None لأي رقم تعذّر جلبه بدل
    رمي خطأ -- خلل في مؤشر السوق العام لا يجب أن يمنع عرض بقية الصفحة."""
    cached = _cached("market_status")
    if cached is not None:
        return cached

    result = {
        "spy_price": None, "spy_sma50": None, "spy_above_sma": None,
        "spy_change_pct": None, "vix": None, "vix_band": "غير متاح",
        "mood": "غير متاح", "note": "تعذّر جلب بيانات السوق العام حالياً.",
        "as_of": time.time(),
    }
    try:
        frames = await asyncio.to_thread(data.fetch_batch, ["SPY", "^VIX"], "1d", "6mo")
        spy = frames.get("SPY")
        vix_df = frames.get("^VIX")

        spy_above_sma = None
        if spy is not None and len(spy) >= 50:
            sma50 = float(spy["Close"].tail(50).mean())
            price = float(spy["Close"].iloc[-1])
            prev = float(spy["Close"].iloc[-2]) if len(spy) >= 2 else price
            spy_above_sma = price > sma50
            result.update({
                "spy_price": round(price, 2),
                "spy_sma50": round(sma50, 2),
                "spy_above_sma": spy_above_sma,
                "spy_change_pct": round((price - prev) / prev * 100, 2) if prev else None,
            })

        vix_price = None
        if vix_df is not None and len(vix_df):
            vix_price = float(vix_df["Close"].iloc[-1])
            result["vix"] = round(vix_price, 1)

        mood, band = _classify_mood(spy_above_sma, vix_price)
        result["mood"] = mood
        result["vix_band"] = band
        result["note"] = _mood_note(result, mood)
    except Exception:
        log.exception("Market status fetch failed")

    return _store("market_status", result)


def _mood_note(m: dict, mood: str) -> str:
    parts = []
    if m["spy_price"] is not None:
        trend_txt = "أعلى من" if m["spy_above_sma"] else "أقل من"
        chg = m["spy_change_pct"]
        chg_txt = f"، تغيّر اليوم {chg:+.2f}%" if chg is not None else ""
        parts.append(f"مؤشر SPY ({m['spy_price']:.2f}$) {trend_txt} متوسطه الحركي 50 يوماً "
                     f"({m['spy_sma50']:.2f}$){chg_txt}.")
    if m["vix"] is not None:
        parts.append(f"مؤشر الخوف VIX عند {m['vix']:.1f} ({m['vix_band']}).")
    if not parts:
        return "تعذّر جلب بيانات السوق العام حالياً."
    return " ".join(parts) + f" الخلاصة: السوق يبدو **{mood}** حالياً."


async def fetch_news(extra_symbols: list[str] | None = None, limit: int = 24) -> list[dict]:
    """يجمع آخر أخبار yfinance لقائمة رموز (DASHBOARD_NEWS_SYMBOLS +
    الرموز الممرَّرة من كتالوج اليوم)، يزيل التكرار بالرابط، ويرتب الأحدث
    أولاً. عنصر واحد فاشل (رمز بلا أخبار، أو خطأ شبكة) لا يوقف البقية."""
    symbols = list(dict.fromkeys(config.DASHBOARD_NEWS_SYMBOLS + (extra_symbols or [])))
    cache_key = "news:" + ",".join(sorted(symbols))
    cached = _cached(cache_key)
    if cached is not None:
        return cached

    items: list[dict] = []
    seen_links: set[str] = set()

    def _fetch_one(symbol: str) -> list[dict]:
        try:
            return yf.Ticker(symbol).news or []
        except Exception:
            log.exception("News fetch failed for %s", symbol)
            return []

    for symbol in symbols:
        raw = await asyncio.to_thread(_fetch_one, symbol)
        for entry in raw:
            content = entry.get("content", entry)  # yfinance news schema has shifted before
            link = (content.get("canonicalUrl") or {}).get("url") or content.get("link") or entry.get("link")
            title = content.get("title") or entry.get("title")
            if not link or not title or link in seen_links:
                continue
            seen_links.add(link)
            publisher = (content.get("provider") or {}).get("displayName") \
                or entry.get("publisher") or ""
            pub_ts = entry.get("providerPublishTime")
            if not pub_ts:
                pub_date = content.get("pubDate")
                pub_ts = _parse_iso_ts(pub_date) if pub_date else None
            items.append({
                "symbol": symbol, "title": title, "publisher": publisher,
                "link": link, "published_ts": pub_ts,
            })

    items.sort(key=lambda x: x["published_ts"] or 0, reverse=True)
    return _store(cache_key, items[:limit])


def _parse_iso_ts(iso: str) -> float | None:
    import datetime as dt
    try:
        return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()
    except Exception:
        return None
