"""صياد أخطاء الأسعار والتخفيضات المجنونة في أمازون (إلكترونيات).

يشتغل مرة واحدة ويطلع (GitHub Actions يشغله كل 5 دقائق).
كل تشغيلة تفحص عدد من صفحات البحث بالتناوب، وتنبه على تيليجرام إذا:
  1. السعر أقل من حد معروف للمنتج (مثلاً MacBook Pro أقل من 3000 ريال) = خطأ تسعير محتمل.
  2. السعر نزل بقوة عن متوسط سعره اللي سجله البوت سابقاً.
  3. الخصم عن السعر الأصلي المشطوب كبير جداً (70%+).

المتغيرات: DEALS_BOT_TOKEN, DEALS_CHAT_ID. اختياري: DEALS_STATE (مسار ملف الحالة), DEALS_DRY=1.
"""
from __future__ import annotations

import html
import json
import os
import random
import re
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

HERE = Path(__file__).resolve().parent
CONFIG = json.loads((HERE / "config.json").read_text(encoding="utf-8"))
STATE_PATH = Path(os.environ.get("DEALS_STATE", HERE / "deals_state.json"))
HISTORY_KEEP = 40  # كم سعر نحفظ لكل منتج

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.5 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:131.0) Gecko/20100101 Firefox/131.0",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0.0.0 Safari/537.36",
]
BLOCK_MARKERS = ("validateCaptcha", "/errors/validateCaptcha", "api-services-support@amazon.com",
                 "Enter the characters you see below")


# ---------- تحليل الصفحة ----------

def parse_price(text: str | None) -> float | None:
    if not text:
        return None
    m = re.search(r"(\d[\d,]*(?:\.\d+)?)", text.replace(" ", " "))
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", ""))
    except ValueError:
        return None


def parse_results(page: str) -> list[dict]:
    soup = BeautifulSoup(page, "html.parser")
    items = []
    for div in soup.select('div[data-component-type="s-search-result"][data-asin]'):
        asin = div.get("data-asin", "").strip()
        if not asin:
            continue
        if div.select_one(".puis-sponsored-label-text, .s-sponsored-label-text"):
            sponsored = True
        else:
            sponsored = False
        h2 = div.select_one("h2")
        title = ""
        if h2:
            title = (h2.get("aria-label") or h2.get_text(" ", strip=True)).strip()
        # العنوان أحياناً في سطر منفصل عن اسم الماركة
        alt = div.select_one('[data-cy="title-recipe"]')
        if alt:
            t2 = alt.get_text(" ", strip=True)
            if len(t2) > len(title):
                title = t2
        price = None
        for sp in div.select("span.a-price"):
            if "a-text-price" in (sp.get("class") or []):
                continue
            off = sp.select_one("span.a-offscreen")
            price = parse_price(off.get_text() if off else sp.get_text())
            if price:
                break
        list_price = None
        lp = div.select_one("span.a-price.a-text-price span.a-offscreen")
        if lp:
            list_price = parse_price(lp.get_text())
        if not price or not title:
            continue
        items.append({
            "asin": asin,
            "title": re.sub(r"\s+", " ", title)[:200],
            "price": price,
            "list_price": list_price if list_price and list_price > price else None,
            "sponsored": sponsored,
        })
    return items


def is_blocked(page: str) -> bool:
    return any(m in page for m in BLOCK_MARKERS)


# ---------- قواعد الكشف ----------

def _has_word(t: str, words: list[str]) -> bool:
    return any(re.search(rf"(?<!\w){re.escape(w)}(?!\w)", t) for w in words)


def is_resale(title: str) -> bool:
    return _has_word(title.lower(), CONFIG["resale"]["words"])


def excluded(title: str) -> bool:
    t = title.lower()
    # مجدد بحالة سيئة/مقبولة: ما نبيه
    if is_resale(title) and _has_word(t, CONFIG["resale"]["bad_condition"]):
        return True
    # الإكسسوارات تبدأ عادة بـ "Case for MacBook..." والمنتج الحقيقي يذكر "Case" متأخر
    # (مثل Apple Watch ... Aluminium Case with Sport Band)، فنفحص أول العنوان بس.
    return any(w in t[:45] for w in CONFIG["exclude_words"])


def floor_hit(title: str, price: float, currency: str) -> float | None:
    t = title.lower()
    factor = CONFIG["resale"]["floor_factor"] if is_resale(title) else 1.0
    for rule in CONFIG["floors"].get(currency, []):
        below = rule["below"] * factor
        if all(w in t for w in rule["match"]) and price < below:
            return below
    return None


def evaluate(item: dict, currency: str, history: list[float]) -> list[str]:
    """يرجع أسباب التنبيه (فاضية = ما فيه شي)."""
    price = item["price"]
    if price < CONFIG["min_price"].get(currency, 0) or excluded(item["title"]):
        return []
    reasons = []
    floor = floor_hit(item["title"], price, currency)
    if floor:
        reasons.append(f"🚨 أقل من الحد الطبيعي ({floor:,.0f} {currency}) — احتمال خطأ تسعير")
    if len(history) >= 3:
        med = statistics.median(history)
        drop = (1 - price / med) * 100 if med else 0
        if drop >= CONFIG["history_drop_pct"]:
            reasons.append(f"📉 نزل {drop:.0f}% عن متوسط سعره ({med:,.0f} {currency})")
    if item["list_price"]:
        disc = (1 - price / item["list_price"]) * 100
        if disc >= CONFIG["discount_alert_pct"]:
            reasons.append(f"🔥 خصم {disc:.0f}% عن السعر الأصلي ({item['list_price']:,.0f} {currency})")
    return reasons


# ---------- الحالة ----------

def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


# ---------- تيليجرام ----------

def send(text: str) -> bool:
    token = os.environ.get("DEALS_BOT_TOKEN")
    chat = os.environ.get("DEALS_CHAT_ID")
    if os.environ.get("DEALS_DRY") == "1" or not token:
        print("[DRY]", text.replace("\n", " | "))
        return True
    if not chat:
        chat = discover_chat(token)
        if not chat:
            print("DEALS_CHAT_ID ناقص: أرسل أي رسالة للبوت في تيليجرام وشغّل مرة ثانية.")
            return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=20, json={
            "chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False})
        if r.status_code != 200:
            print("telegram error", r.status_code, r.text[:200])
        return r.status_code == 200
    except requests.RequestException as e:
        print("telegram error", e)
        return False


def discover_chat(token: str) -> str | None:
    """إذا ما حطيت DEALS_CHAT_ID: ياخذ آخر محادثة خاصة أرسلت للبوت ويقولك رقمها."""
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=20).json()
    except Exception:
        return None
    for upd in reversed(r.get("result", [])):
        chat = (upd.get("message") or {}).get("chat") or {}
        if chat.get("type") == "private":
            cid = str(chat["id"])
            print(f"لقيت رقم محادثتك: {cid} — حطه في السر DEALS_CHAT_ID")
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=20, json={
                "chat_id": cid,
                "text": f"رقم محادثتك: {cid}\nحطه في GitHub Secrets باسم DEALS_CHAT_ID"})
            return cid
    return None


def format_alert(item: dict, market: dict, reasons: list[str]) -> str:
    cur = market["currency"]
    flag = "🇸🇦" if market["host"].endswith(".sa") else "🇺🇸" if market["host"].endswith(".com") else "🌍"
    url = f"https://{market['host']}/dp/{item['asin']}"
    tag = "♻️ <b>مجدد / Resale</b>\n" if is_resale(item["title"]) else ""
    lines = [f"{tag}{flag} <b>{html.escape(item['title'][:150])}</b>",
             f"💰 السعر: <b>{item['price']:,.2f} {cur}</b>"]
    lines += reasons
    lines.append(f'🔗 <a href="{url}">افتح المنتج واشترِ بسرعة</a>')
    return "\n".join(lines)


# ---------- التشغيل ----------

def fetch(session: requests.Session, url: str, retry: bool = True) -> str | None:
    try:
        r = session.get(url, timeout=25, headers={
            "User-Agent": random.choice(USER_AGENTS),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        })
    except requests.RequestException as e:
        print("fetch error", url, e)
        return None
    if r.status_code in (503, 429) and retry:  # أمازون أحياناً ترفض مؤقتاً، نحاول مرة ثانية
        time.sleep(random.uniform(8, 15))
        return fetch(session, url, retry=False)
    if r.status_code != 200:
        print("status", r.status_code, url)
        return None
    return r.text


def search_url(src: dict) -> str:
    m = CONFIG["markets"][src["market"]]
    q = requests.utils.quote(src["q"])
    extra = "&" + src["extra"] if src.get("extra") else ""
    return f"https://{m['host']}/s?k={q}&{m['params']}{extra}"


def run() -> int:
    state = load_state()
    prices: dict = state.setdefault("prices", {})     # "sa:ASIN" -> [سعر, ...]
    alerted: dict = state.setdefault("alerted", {})   # "sa:ASIN" -> {"price":, "ts":}
    stats: dict = state.setdefault("stats", {"day": "", "pages": 0, "blocked": 0, "items": 0, "alerts": 0})
    searches = CONFIG["searches"]
    cursor = state.get("cursor", 0) % len(searches)
    n = min(CONFIG["pages_per_run"], len(searches))
    batch = [searches[(cursor + i) % len(searches)] for i in range(n)]
    state["cursor"] = (cursor + n) % len(searches)

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if stats["day"] != today:
        if stats["day"]:
            send(f"✅ صياد الأسعار شغال\nأمس: {stats['pages']} صفحة، {stats['items']} منتج، "
                 f"{stats['alerts']} تنبيه، {stats['blocked']} حظر مؤقت من أمازون")
        stats.update(day=today, pages=0, blocked=0, items=0, alerts=0)

    now = time.time()
    session = requests.Session()
    for i, src in enumerate(batch):
        if i:
            time.sleep(random.uniform(3, 7))
        market = CONFIG["markets"][src["market"]]
        page = fetch(session, search_url(src))
        stats["pages"] += 1
        if page is None or is_blocked(page):
            stats["blocked"] += 1
            print("blocked/failed:", src["q"])
            continue
        items = parse_results(page)
        stats["items"] += len(items)
        print(f"{src['market']} '{src['q']}': {len(items)} منتج")
        for it in items:
            key = f"{src['market']}:{it['asin']}"
            hist = prices.get(key, [])
            reasons = evaluate(it, market["currency"], hist)
            if reasons:
                prev = alerted.get(key)
                fresh = (not prev or now - prev["ts"] > CONFIG["realert_hours"] * 3600
                         or it["price"] < prev["price"] * 0.95)
                if fresh and send(format_alert(it, market, reasons)):
                    alerted[key] = {"price": it["price"], "ts": now}
                    stats["alerts"] += 1
            # لا نخلي سعر الخطأ يخرب المتوسط
            if not reasons:
                hist = (hist + [it["price"]])[-HISTORY_KEEP:]
                prices[key] = hist

    # نظافة: نحذف تنبيهات أقدم من أسبوع
    for k in [k for k, v in alerted.items() if now - v["ts"] > 7 * 86400]:
        del alerted[k]
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(run())
