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
            "coupon": parse_coupon(div.get_text(" ", strip=True), price),
        })
    return items


def parse_coupon(text: str, price: float) -> float | None:
    """'Save 10% with coupon' / 'Save SAR 50.00 with coupon' / 'وفر 10% مع القسيمة' → قيمة الخصم بالريال."""
    t = text.replace("\u00a0", " ")
    m = re.search(r"save\s+(\d{1,2})\s*%\s+(?:with|on)\s+(?:coupon|voucher)", t, re.I) or \
        re.search(r"(?:وفر|وفّر)\s*(\d{1,2})\s*%\s*(?:مع|ب)\s*(?:ال)?(?:كوبون|قسيمة)", t)
    if m:
        return round(price * int(m.group(1)) / 100, 2)
    m = re.search(r"save\s+(?:sar|ر\.?س\.?)\s*([\d,]+(?:\.\d+)?)\s+(?:with|on)\s+(?:coupon|voucher)", t, re.I)
    if m:
        off = float(m.group(1).replace(",", ""))
        return off if 0 < off < price else None
    return None


def _walk(o):
    if isinstance(o, dict):
        yield o
        for v in o.values():
            yield from _walk(v)
    elif isinstance(o, list):
        for v in o:
            yield from _walk(v)


def _first(v):
    return v[0] if isinstance(v, list) and v else v


def parse_almanea(page: str) -> list[dict]:
    """المنيع: المنتجات داخل __NEXT_DATA__ (نتائج Algolia) وكل منتج فيه prices_with_tax."""
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', page, re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return []
    items, seen = [], set()
    for d in _walk(data):
        p = d.get("prices_with_tax")
        if not isinstance(p, dict):
            continue
        pid = str(_first(d.get("sku")) or d.get("objectID") or d.get("id") or "")
        name = _first(d.get("name"))
        price = p.get("price") or p.get("discounted_price")
        if not pid or not isinstance(name, str) or not price or pid in seen:
            continue
        seen.add(pid)
        orig = p.get("original_price")
        url = _first(d.get("url")) or ""
        if url and not url.startswith("http"):
            url = "https://www.almanea.sa/" + url.lstrip("/")
        if not url and d.get("url_key"):
            url = f"https://www.almanea.sa/en/{_first(d['url_key'])}"
        items.append({
            "asin": pid, "title": re.sub(r"\s+", " ", name)[:200], "price": round(float(price), 2),
            "list_price": round(float(orig), 2) if orig and float(orig) > float(price) else None,
            "sponsored": False, "url": url or None,
        })
    if not items:
        print("almanea: ما لقيت منتجات في __NEXT_DATA__")
    return items


PARSERS = {"amazon": parse_results, "almanea": parse_almanea}


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
        # كلمات كاملة: "65" ما تطابق "165Hz"، و"tv" ما تطابق جزء من كلمة ثانية
        hits = [re.search(rf"(?<![\w]){re.escape(w)}(?![\w])", t) for w in rule["match"]]
        if not all(hits) or price >= below:
            continue
        # "Controller for Xbox Series X" / "Battery Pack (Xbox Series X)": الإكسسوار يجي قبل اسم الجهاز
        head = t[:max(h.start() for h in hits)]
        if _has_word(head, CONFIG.get("floor_accessory_words", [])):
            continue
        return below
    return None


def evaluate(item: dict, currency: str, history: list[float], trust_list: bool = True) -> list[str]:
    """يرجع أسباب التنبيه (فاضية = ما فيه شي).

    trust_list=False: المتجر يضخّم "السعر الأصلي"، فخصمه ما نصدقه إلا إذا السعر نزل فعلاً
    عن الأسعار اللي شافها البوت بنفسه (10%+ تحت المتوسط)."""
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
    # المجدد سعره "الأصلي" المشطوب هو سعر الجديد، فالخصم الكبير عليه طبيعي ومو فرصة
    real_drop = len(history) >= 3 and price <= statistics.median(history) * 0.9
    if item["list_price"] and not is_resale(item["title"]) and (trust_list or real_drop):
        disc = (1 - price / item["list_price"]) * 100
        if disc >= CONFIG["discount_alert_pct"]:
            reasons.append(f"🔥 خصم {disc:.0f}% عن السعر الأصلي ({item['list_price']:,.0f} {currency})")
    return reasons


# ---------- مقارنة نفس الموديل بين المتاجر ----------

def model_key(title: str) -> str | None:
    """مفتاح موحّد لنفس المنتج في أي متجر (آيفون/ماك/سامسونج/PS5/AirPods). None = ما نعرف نطابقه."""
    t = title.lower().replace("\u201d", '"').replace("''", '"')
    if is_resale(title):
        return None
    st = re.search(r"\b(\d{3,4})\s*gb\b|\b(\d)\s*tb\b", t)
    storage = (st.group(1) + "gb") if st and st.group(1) else (st.group(2) + "tb") if st else None
    m = re.search(r"iphone\s*(\d{2})\s*(pro max|pro|plus|air|e)?\b", t)
    if m and storage:
        return f"iphone{m.group(1)}-{(m.group(2) or 'base').replace(' ', '')}-{storage}"
    m = re.search(r"macbook\s*(air|pro)", t)
    chip = re.search(r"\bm(\d)\s*(pro|max)?\b", t)
    size = re.search(r"\b(13|14|15|16)(?:\.\d)?\s*(?:-?\s*inch|\"|in\b)", t)
    if m and chip and size and storage:
        return f"macbook{m.group(1)}-m{chip.group(1)}{chip.group(2) or ''}-{size.group(1)}-{storage}"
    m = re.search(r"galaxy\s*(s\d{2})\s*(ultra|plus|\+|fe|edge)?", t)
    if m and storage:
        return f"galaxy{m.group(1)}-{(m.group(2) or 'base').replace('+', 'plus')}-{storage}"
    if re.search(r"playstation\s*5|\bps5\b", t) and "console" in t:
        ed = "pro" if re.search(r"\bpro\b", t) else "digital" if "digital" in t else "disc"
        return f"ps5-{ed}"
    m = re.search(r"airpods\s*(pro\s*\d?|max|\d)", t)
    if m:
        return "airpods-" + m.group(1).replace(" ", "")
    return None


def cross_store_reason(item: dict, market_name: str, models: dict, now: float) -> str | None:
    key = model_key(item["title"])
    if not key:
        return None
    pct_min = CONFIG.get("cross_store_pct", 35)
    best = None
    for other, rec in models.get(key, {}).items():
        if other == market_name or now - rec["ts"] > 48 * 3600:
            continue
        cheaper = (1 - item["price"] / rec["price"]) * 100
        if cheaper >= pct_min and (not best or cheaper > best[0]):
            best = (cheaper, other, rec["price"])
    if not best:
        return None
    label = CONFIG["markets"].get(best[1], {}).get("label", best[1])
    return f"⚖️ أرخص {best[0]:.0f}% من نفس الموديل في {label} ({best[2]:,.0f})"


# ---------- قنوات تيليجرام العامة ----------

def parse_channel(page: str) -> list[tuple[int, str]]:
    """صفحة t.me/s/<قناة>: يرجع [(رقم المنشور, النص)]."""
    soup = BeautifulSoup(page, "html.parser")
    out = []
    for div in soup.select("div.tgme_widget_message[data-post]"):
        try:
            pid = int(div["data-post"].split("/")[-1])
        except ValueError:
            continue
        txt = div.select_one(".tgme_widget_message_text")
        if txt:
            out.append((pid, txt.get_text("\n", strip=True)))
    return out


def scan_channels(state: dict, session: requests.Session) -> int:
    chans = CONFIG.get("telegram_channels", [])
    if not chans:
        return 0
    seen: dict = state.setdefault("tg_seen", {})
    words = [w.lower() for w in CONFIG.get("telegram_keywords", [])]
    sent = 0
    for ch in chans:
        ch = ch.lstrip("@").strip()
        try:
            r = session.get(f"https://t.me/s/{ch}", timeout=20)
        except requests.RequestException:
            continue
        if r.status_code != 200:
            print("telegram channel", ch, r.status_code)
            continue
        posts = parse_channel(r.text)
        last = seen.get(ch)
        if posts:
            seen[ch] = max(p for p, _ in posts)
        if last is None:  # أول مرة: نحفظ مكاننا بس، ما نغرقك بالقديم
            print(f"قناة {ch}: بدأنا المتابعة ({len(posts)} منشور)")
            continue
        for pid, text in posts:
            if pid <= last:
                continue
            low = text.lower()
            if words and not any(w in low for w in words):
                continue
            msg = (f"📢 من قناة @{html.escape(ch)}\n{html.escape(text[:700])}\n"
                   f'🔗 <a href="https://t.me/{ch}/{pid}">افتح المنشور</a>')
            if send(msg):
                sent += 1
    return sent


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
    """إذا ما حطيت DEALS_CHAT_ID: ياخذ آخر محادثة أرسلت للبوت ويقولك رقمها."""
    try:
        r = requests.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=20).json()
    except Exception as e:
        print("getUpdates failed:", type(e).__name__)
        return None
    if not r.get("ok"):
        # مثلاً: webhook مفعّل على البوت، أو بوت ثاني يقرأ نفس الرسائل
        print("getUpdates error:", r.get("error_code"), r.get("description"))
        return None
    updates = r.get("result", [])
    print(f"getUpdates: {len(updates)} رسالة")
    for upd in reversed(updates):
        msg = upd.get("message") or upd.get("my_chat_member") or upd.get("channel_post") or {}
        chat = msg.get("chat") or {}
        if chat.get("id"):
            cid = str(chat["id"])
            print(f"لقيت رقم المحادثة ({chat.get('type')}) — حطه في السر DEALS_CHAT_ID")
            requests.post(f"https://api.telegram.org/bot{token}/sendMessage", timeout=20, json={
                "chat_id": cid,
                "text": f"رقم محادثتك: {cid}\nحطه في GitHub Secrets باسم DEALS_CHAT_ID"})
            return cid
    return None


def format_alert(item: dict, market: dict, reasons: list[str]) -> str:
    cur = market["currency"]
    flag = "🇸🇦" if market["host"].endswith(".sa") else "🇺🇸" if market["host"].endswith(".com") else "🌍"
    flag = f"{flag} {market.get('label', 'أمازون')}"
    url = item.get("url") or f"https://{market['host']}/dp/{item['asin']}"
    tag = "♻️ <b>مجدد / Resale</b>\n" if is_resale(item["title"]) else ""
    lines = [f"{tag}{flag} <b>{html.escape(item['title'][:150])}</b>",
             f"💰 السعر: <b>{item['price']:,.2f} {cur}</b>"]
    if item.get("coupon"):
        lines.append(f"🎟️ كوبون -{item['coupon']:,.0f} → <b>{item['price'] - item['coupon']:,.2f} {cur}</b> بعد الكوبون "
                     "(فعّله من صفحة المنتج)")
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
    if m.get("search"):
        return m["search"].format(q=q)
    extra = "&" + src["extra"] if src.get("extra") else ""
    return f"https://{m['host']}/s?k={q}&{m['params']}{extra}"


def pick_batch(state: dict, now: float) -> list[dict]:
    """لكل متجر: عدد صفحات وفترة خاصة فيه (أمازون أقل وأبطأ عشان ما تحظرنا)، وبالتناوب على كلمات البحث."""
    cursors: dict = state.setdefault("cursors", {})
    last: dict = state.setdefault("last_scan", {})
    per_market: list[list[dict]] = []
    for name, m in CONFIG["markets"].items():
        srcs = [x for x in CONFIG["searches"] if x["market"] == name]
        n = min(m.get("pages_per_run", CONFIG["pages_per_run"]), len(srcs))
        if not n or now - last.get(name, 0) < m.get("every_min", 0) * 60 - 30:
            continue
        c = cursors.get(name, 0) % len(srcs)
        per_market.append([srcs[(c + i) % len(srcs)] for i in range(n)])
        cursors[name] = (c + n) % len(srcs)
        last[name] = now
    # نخلط المتاجر: صفحة من هذا وصفحة من ذاك
    batch = []
    while any(per_market):
        for lst in per_market:
            if lst:
                batch.append(lst.pop(0))
    return batch


# ---------- تجربة طرق أمازون (نقيس أي طريقة يقل فيها الحظر) ----------

EXPERIMENT = {
    # الاسم: (عدد الصفحات, (أقل, أكثر) ثواني قبل كل صفحة, شكل الرابط)
    "base":      (2, (3, 7), "search"),
    "one_page":  (1, (3, 7), "search"),
    "slow_gap":  (2, (20, 30), "search"),
    "en_path":   (2, (3, 7), "en_path"),
    "bestsellers": (1, (3, 7), "bestsellers"),
}


def apply_experiment(state: dict, batch: list[dict]) -> str | None:
    """يعدّل صفحات أمازون السعودية في هالتشغيلة حسب الطريقة اللي دورها (بالتناوب)."""
    if not CONFIG.get("amazon_experiment"):
        return None
    exp = state.setdefault("exp", {"i": 0, "stats": {}})
    names = list(EXPERIMENT)
    name = names[exp["i"] % len(names)]
    exp["i"] += 1
    pages, gap, form = EXPERIMENT[name]
    sa = [x for x in batch if x["market"] == "sa"]
    rest = [x for x in batch if x["market"] != "sa"]
    chosen = []
    for src in sa[:pages]:
        src = dict(src, _gap=gap, _exp=name)
        if form == "en_path":
            src["_url"] = f"https://www.amazon.sa/-/en/s?k={requests.utils.quote(src['q'])}"
        elif form == "bestsellers":
            src["_url"] = "https://www.amazon.sa/gp/bestsellers/electronics"
            src["_measure_only"] = True
        chosen.append(src)
    batch[:] = chosen + rest  # أمازون أول (قبل ما نطلب أي شي ثاني)
    return name


def record_experiment(state: dict, name: str | None, ok: int, blocked: int) -> None:
    if not name:
        return
    st = state["exp"]["stats"].setdefault(name, {"runs": 0, "ok": 0, "blocked": 0})
    st["runs"] += 1
    st["ok"] += ok
    st["blocked"] += blocked
    summary = " | ".join(f"{k}: {v['ok']}✓/{v['blocked']}✗ ({v['runs']} مرات)"
                         for k, v in state["exp"]["stats"].items())
    print(f"EXP this_run={name} ok={ok} blocked={blocked} || {summary}")


def run() -> int:
    state = load_state()
    prices: dict = state.setdefault("prices", {})     # "sa:ASIN" -> [سعر, ...]
    alerted: dict = state.setdefault("alerted", {})   # "sa:ASIN" -> {"price":, "ts":}
    stats: dict = state.setdefault("stats", {"day": "", "pages": 0, "blocked": 0, "items": 0, "alerts": 0})
    batch = pick_batch(state, time.time())
    models: dict = state.setdefault("models", {})     # موديل -> {متجر: {"price":, "ts":}}

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if stats["day"] != today:
        if stats["day"]:
            send(f"✅ صياد الأسعار شغال\nأمس: {stats['pages']} صفحة، {stats['items']} منتج، "
                 f"{stats['alerts']} تنبيه، {stats['blocked']} حظر مؤقت")
        stats.update(day=today, pages=0, blocked=0, items=0, alerts=0)

    # أول تشغيل بعد إضافة الـ token: رسالة ترحيب + رقم المحادثة (حتى لو ما فيه عروض)
    if os.environ.get("DEALS_BOT_TOKEN") and not state.get("welcomed"):
        if send("👋 صياد أخطاء الأسعار شغال!\nبيفحص إلكترونيات أمازون السعودية والمنيع (جديد + مجدد) "
                "ويرسل لك هنا أي سعر غلط أو تخفيض مجنون."):
            state["welcomed"] = True

    now = time.time()
    session = requests.Session()
    blocked_hosts: set[str] = set()
    exp_name = apply_experiment(state, batch)
    exp_ok = exp_blocked = 0
    for i, src in enumerate(batch):
        if i:
            time.sleep(random.uniform(*src.get("_gap", (3, 7))))
        market = CONFIG["markets"][src["market"]]
        if market["host"] in blocked_hosts:  # أمازون رفضتنا: نوقف الطلب عليها لين التشغيلة الجاية
            continue
        page = fetch(session, src.get("_url") or search_url(src))
        stats["pages"] += 1
        if page is None or is_blocked(page):
            stats["blocked"] += 1
            blocked_hosts.add(market["host"])
            exp_blocked += bool(src.get("_exp"))
            print("blocked/failed:", src["q"], src.get("_exp", ""))
            continue
        if src.get("_measure_only"):  # صفحة الأكثر مبيعاً: نقيس الوصول بس
            good = len(page) > 50000
            exp_ok += good
            exp_blocked += not good
            print(f"{src['_exp']}: len={len(page)} {'ok' if good else 'short page'}")
            continue
        items = PARSERS[market.get("parser", "amazon")](page)
        if not items:  # صفحة بدون منتجات = أمازون رجعت صفحة مختصرة (حظر ناعم)
            stats["blocked"] += 1
            blocked_hosts.add(market["host"])
            exp_blocked += bool(src.get("_exp"))
            print(f"no results (soft block?) '{src['q']}' len={len(page)} {src.get('_exp', '')}")
            continue
        exp_ok += bool(src.get("_exp"))
        stats["items"] += len(items)
        print(f"{src['market']} '{src['q']}': {len(items)} منتج")
        for it in items:
            key = f"{src['market']}:{it['asin']}"
            hist = prices.get(key, [])
            # إذا فيه كوبون نقيّم بالسعر بعده (عرض قوي + كوبون = صيدة)
            ev = dict(it, price=round(it["price"] - it["coupon"], 2)) if it.get("coupon") else it
            reasons = evaluate(ev, market["currency"], hist, market.get("trust_list_price", True))
            if not excluded(it["title"]):
                cross = cross_store_reason(ev, src["market"], models, now)
                if cross:
                    reasons.append(cross)
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
                mk = model_key(it["title"]) if not excluded(it["title"]) else None
                if mk:  # أرخص سعر لهالموديل في هالمتجر (آخر 48 ساعة)
                    rec = models.setdefault(mk, {}).get(src["market"])
                    if not rec or now - rec["ts"] > 48 * 3600 or it["price"] < rec["price"]:
                        models[mk][src["market"]] = {"price": it["price"], "ts": now}

    record_experiment(state, exp_name, exp_ok, exp_blocked)
    try:
        stats["alerts"] += scan_channels(state, session)
    except Exception as e:  # القنوات إضافة: ما نخليها توقف الصيد
        print("telegram channels error:", type(e).__name__, e)

    # نظافة: نحذف تنبيهات أقدم من أسبوع
    for k in [k for k, v in alerted.items() if now - v["ts"] > 7 * 86400]:
        del alerted[k]
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(run())
