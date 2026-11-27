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
        # الإعلانات: أمازون تحط "Sponsored ... Let us know" قبل العنوان — نشيلها ونعلّم المنتج كإعلان
        if re.match(r"\s*sponsored\b", title, re.I):
            sponsored = True
            title = re.sub(r"^\s*(?:sponsored\s*)+(?:you.re seeing this ad.*?let us know\s*)?", "", title,
                           flags=re.I | re.S)
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


AR_MAP = [  # أسماء عربية شائعة في المتاجر الصغيرة → الإنجليزي اللي تفهمه القواعد
    (r"آيفون|ايفون|أيفون", "iphone"), (r"ماك\s*بوك", "macbook"), (r"ايباد|آيباد|أيباد", "ipad"),
    (r"اير\s*بودز|ايربودز", "airpods"), (r"برو\s*ماكس", "pro max"), (r"\bبرو\b", "pro"), (r"\bاير\b", "air"),
    (r"\bماكس\b", "max"), (r"بلس", "plus"), (r"جالكسي|جالاكسي|قالكسي|galaxy", "galaxy"), (r"سامسونج", "samsung"),
    (r"بلايستيشن|بلاي\s*ستيشن", "playstation"), (r"سوني", "sony"), (r"اكس\s*بوكس", "xbox"), (r"جهاز", "console"),
    (r"(\d+)\s*(?:جيجا|قيقا|جيقا|gb)", r"\1gb"), (r"(\d+)\s*(?:تيرا|تيرابايت)", r"\1tb"),
    (r"مكيف\s*سبليت|سبليت", "split air conditioner"), (r"مكيف\s*شباك|شباك", "window air conditioner"),
    (r"شاشة|شاشه|تلفزيون", "tv"), (r"بوصة|انش", "inch"), (r"لابتوب", "laptop"), (r"ساعة\s*ابل|ساعه\s*ابل|ساعة\s*آبل", "apple watch"),
    (r"الترا", "ultra"), (r"مستخدم", "used"),
    # كروت الشاشة: "كرت 3060"، "كرت شاشه 4070 سوبر"، "ار تي اكس 3080"، "rtx3070ti"
    (r"(?:ار\s*تي\s*اكس|rtx)\s*(\d{4})", r"rtx \1"),
    (r"(?:كرت|كارت)\s*(?:شاشه|شاشة|tv)?\s*(?:rtx\s*)?((?:20|30|40|50)[5-9]0)\b", r"rtx \1"),
    (r"(?:كرت|كارت)\s*(?:شاشه|شاشة|tv)?\s*(?:rx\s*)?((?:6|7|9)[0-9]00)\b", r"rx \1"),
    (r"(?:جي\s*تي\s*اكس|gtx)\s*(\d{3,4})", r"gtx \1"), (r"سوبر", "super"), (r"\bتي\s*اي\b", "ti"),
]


def _ar_fold(w: str) -> str:
    """توحيد الكتابة العربية: الهمزات، التاء المربوطة، الألف المقصورة، التطويل والتشكيل."""
    w = re.sub(r"[\u064B-\u0652\u0640]", "", w)
    return w.translate(str.maketrans("أإآٱةى", "ااااهي"))


def norm_title(title: str) -> str:
    """lower + ترجمة الكلمات العربية الشائعة عشان القواعد (الحدود، المطابقة) تشتغل على العناوين العربية."""
    t = title.lower()
    if re.search(r"[\u0600-\u06FF]", t):
        t = _ar_fold(t)  # إيفون/أيفون → ايفون، شاشة → شاشه ... قبل الترجمة
        for pat, rep in AR_MAP:
            t = re.sub(pat, rep, t)
    return t


def parse_mahally(page: str) -> list[dict]:
    """محلي: المتصفح يرجّع JSON [[href, نص البطاقة]]. النص: الاسم المتجر التقييم (عدد) ... [قبل -N% ] السعر."""
    try:
        cards = json.loads(page)
    except ValueError:
        return []
    items, seen = [], set()
    for href, txt in cards:
        href = href.split("?")[0]
        if href in seen or "/products/" not in href:
            continue
        seen.add(href)
        txt = re.sub(r"\s+", " ", txt).strip()
        m = re.search(r"([\d,]+(?:\.\d+)?)\s*-(\d{1,2})%\s*([\d,]+(?:\.\d+)?)\s*$", txt) or \
            re.search(r"()()([\d,]+(?:\.\d+)?)\s*$", txt)
        if not m:
            continue
        price = float(m.group(3).replace(",", ""))
        old = float(m.group(1).replace(",", "")) if m.group(1) else None
        name = re.split(r"\s\d\.\d{2}\s\(\d+\)", txt)[0][:200]  # قبل التقييم (فيه اسم المتجر بالآخر)
        if price <= 0 or not name:
            continue
        pid = href.strip("/").split("/")[-1]
        items.append({"asin": pid, "title": name, "price": price,
                      "list_price": old if old and old > price else None, "sponsored": False,
                      "url": "https://mahally.com" + href})
    return items


def parse_opensooq(page: str) -> list[dict]:
    """السوق المفتوح: الإعلانات في JSON-LD (Product: name, url, offers.price). أغلبها مستعمل من أفراد."""
    items, seen = [], set()
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        for d in _walk(data):
            if d.get("@type") != "Product" or not d.get("url"):
                continue
            offer = d.get("offers") or {}
            try:
                price = float(str(offer.get("price", "0")).replace(",", ""))
            except ValueError:
                continue
            name = re.sub(r"\s+", " ", str(d.get("name", ""))).strip()
            pid = d["url"].rstrip("/").split("/")[-1]
            if price <= 0 or not name or pid in seen:
                continue
            seen.add(pid)
            if not is_resale(name):
                name += " - مستعمل"  # إعلانات أفراد: نعاملها كمستعمل (حدود وتنبيهات المستعمل)
            items.append({"asin": pid, "title": name[:200], "price": price, "list_price": None,
                          "sponsored": False, "url": d["url"]})
    return items


PARSERS = {"amazon": parse_results, "almanea": parse_almanea, "mahally": parse_mahally, "opensooq": parse_opensooq}


def is_blocked(page: str) -> bool:
    return any(m in page for m in BLOCK_MARKERS)


# ---------- قواعد الكشف ----------

def _has_word(t: str, words: list[str]) -> bool:
    t = _ar_fold(t)  # "تعليقة" في القائمة تطابق "تعليقه" بعد التوحيد

    def pat(w: str) -> str:
        w = re.escape(_ar_fold(w))
        # العربي يجي ملصوق بالبادئات: "وسماعة"، "بالكفر"، "للايفون"
        pre = r"(?:وال|بال|فال|كال|لل|ال|و|ف|ب|ل|ك)?" if re.search(r"[\u0600-\u06FF]", w) else ""
        return rf"(?<!\w){pre}{w}(?!\w)"
    return any(re.search(pat(w), t) for w in words)


def is_resale(title: str) -> bool:
    title = norm_title(title)
    return _has_word(title.lower(), CONFIG["resale"]["words"])


def excluded(title: str) -> bool:
    t = _ar_fold(title.lower())  # "شبية" = "شبيه"، "إصلاح" = "اصلاح" ...
    # تجار يبيعون تقليد باسم الأصلي (اسم المتجر يجي بآخر عنوان محلي)
    if _has_word(t, CONFIG.get("blocked_sellers", [])):
        return True
    # مجدد بحالة سيئة/مقبولة: ما نبيه
    if is_resale(title) and _has_word(t, CONFIG["resale"]["bad_condition"]):
        return True
    # تقليد/شبيه/متوافق مع: مو الجهاز الأصلي (وين ما جات بالعنوان)
    if any(_ar_fold(w) in t for w in CONFIG.get("exclude_knockoffs", [])):
        return True
    # خدمات صيانة بالعربي (محلي مليان مراكز صيانة): "خدمة فحص"، "تغيير بطارية"، "استبدال شاشة"...
    if any(_ar_fold(w) in t for w in CONFIG.get("exclude_services_anywhere", [])):
        return True
    # خدمات (تركيب/صيانة/ضمان) مو منتجات — بس إذا في أول العنوان، لأن أجهزة كثيرة تكتب
    # "... 2 Years Warranty" في آخر عنوانها وما نبي نفوّتها
    if _has_word(t[:50], CONFIG.get("exclude_services", [])):
        return True
    # الإكسسوارات تبدأ عادة بـ "Case for MacBook..." والمنتج الحقيقي يذكر "Case" متأخر
    # (مثل Apple Watch ... Aluminium Case with Sport Band)، فنفحص أول العنوان بس.
    return any(_ar_fold(w) in t[:45] for w in CONFIG["exclude_words"])


def floor_hit(title: str, price: float, currency: str) -> float | None:
    t = norm_title(title)
    factor = CONFIG["resale"]["floor_factor"] if is_resale(title) else 1.0
    for rule in CONFIG["floors"].get(currency, []):
        below = rule["below"] * factor
        if rule.get("require_re") and not re.search(rule["require_re"], t):
            continue  # مثلاً ماك بوك لازم يكون فيه معالج M (الأجهزة القديمة سعرها طبيعي يكون أقل)
        # كلمات كاملة: "65" ما تطابق "165Hz"، و"tv" ما تطابق جزء من كلمة ثانية
        hits = [re.search(rf"(?<![\w]){re.escape(w)}(?![\w])", t) for w in rule["match"]]
        if not all(hits) or price >= below:
            continue
        # قطع وملحقات واضحة وين ما جات ("Portable Air Conditioner Window Kit")
        if _has_word(t, CONFIG.get("floor_accessory_anywhere", [])):
            continue
        # منتج من نوع ثاني يذكر الجهاز ("Gaming Monitor ... for PS5 & Xbox Series X")
        if _has_word(t, rule.get("not_if", [])):
            continue
        # "Controller for Xbox Series X" / "Battery Pack (Xbox Series X)": الإكسسوار يجي قبل اسم الجهاز
        head = t[:max(h.start() for h in hits)]
        if _has_word(head, CONFIG.get("floor_accessory_words", [])):
            continue
        return below
    return None


def starts_with_brand(title: str) -> bool:
    """أول كلمة حقيقية بالعنوان (بعد كلمات الدعاية مثل عرض/خصم/🔥) ماركة أو جهاز — يسامح خطأ حرف واحد
    (ايفوون، سامسنج). يعني المنتج نفسه، مو "واقي/ريموت/موجه هواء لـ..."."""
    import difflib
    brands = [_ar_fold(b) for b in CONFIG.get("brand_first_words", [])]
    fillers = {_ar_fold(f) for f in CONFIG.get("brand_fillers", [])}
    words = [w for w in re.split(r"[^\w\u0600-\u06FF]+", _ar_fold(norm_title(title))) if w]
    for w in words[:3]:
        if w in fillers:
            continue
        if w in brands:
            return True
        if len(w) >= 4 and difflib.get_close_matches(w, [b for b in brands if len(b) >= 4], n=1, cutoff=0.8):
            return True
        return False
    return False


def evaluate(item: dict, currency: str, history: list[float], trust_list: bool = True,
             brand_first: bool = False) -> list[str]:
    """يرجع أسباب التنبيه (فاضية = ما فيه شي).

    trust_list=False: المتجر يضخّم "السعر الأصلي"، فخصمه ما نصدقه إلا إذا السعر نزل فعلاً
    عن الأسعار اللي شافها البوت بنفسه (10%+ تحت المتوسط)."""
    price = item["price"]
    if price < CONFIG["min_price"].get(currency, 0) or excluded(item["title"]):
        return []
    reasons = []
    floor = floor_hit(item["title"], price, currency)
    if floor and item.get("sponsored"):
        floor = None  # الإعلانات الممولة ما تكون أخطاء تسعير (وغالباً إكسسوارات)
    if floor and brand_first and price < floor * CONFIG.get("too_cheap_ratio", 0.2):
        floor = None  # متاجر صغيرة: أرخص من 20% من سعر الجهاز = تقليد ينسخ اسم الأصلي، مو خطأ تسعير
    if floor and brand_first and not starts_with_brand(item["title"]):
        # متاجر صغيرة: العنوان ما يبدأ بماركة. الإكسسوارات عادة أرخص بكثير من الجهاز، فلو السعر
        # قريب من سعر الجهاز (≥ 40% من الحد) نرسله صامت للتأكد بدل ما يضيع.
        if price >= floor * CONFIG.get("brand_gate_min_ratio", 0.4):
            reasons.append(f"⚠️ تأكد من العنوان — سعر منخفض ({floor:,.0f} {currency} حد الجهاز) والعنوان ما يبدأ بماركة")
        floor = None
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

def model_key(title: str, allow_resale: bool = False) -> str | None:
    """مفتاح موحّد لنفس المنتج في أي متجر (آيفون/ماك/سامسونج/PS5/AirPods). None = ما نعرف نطابقه."""
    t = norm_title(title).replace("\u201d", '"').replace("''", '"')
    t = re.sub(r"\b(used|renewed|refurbished|مستعمل|مجدد)\b", " ", t) if allow_resale else t
    if is_resale(title) and not allow_resale:
        return None
    # كلمة إكسسوار قبل اسم الجهاز ("Face Plates ... for PS5"، "Case for iPhone") = مو الجهاز نفسه.
    # بعد اسم الجهاز عادي: "AirPods Pro 2 with Charging Case"، "PS5 Console with DualSense Controller"
    dev = re.search(r"iphone|macbook|galaxy|playstation|\bps5\b|airpods", t)
    head = t[:dev.start()] if dev else t
    if _has_word(head, CONFIG.get("floor_accessory_words", []) + CONFIG.get("model_accessory_words", [])):
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


def used_fair_reason(title: str, price: float) -> str | None:
    """مستعمل (إعلانات أفراد): نقارن بسعر المستعمل المعتاد للقطعة (جدول used_fair_prices)."""
    t = norm_title(title)
    best = None
    for key, fair in CONFIG.get("used_fair_prices", {}).items():
        if re.search(rf"(?<!\w){re.escape(key)}(?!\w)", t) and (not best or len(key) > len(best[0])):
            best = (key, fair)  # الأطول أدق: "rtx 4070 super" قبل "rtx 4070"
    if not best:
        return None
    cheaper = (1 - price / best[1]) * 100
    if CONFIG.get("used_fair_pct", 30) <= cheaper <= 75:  # أكثر من 75% = غالباً قطعة ثانية/غلط في الإعلان
        return f"♻️ مستعمل أرخص {cheaper:.0f}% من سعره المعتاد ({best[1]:,.0f} {best[0].upper()})"
    return None


def market_deal_reasons(item: dict, price: float, market_name: str, models: dict, now: float) -> list[str]:
    """عروض مؤكدة بسعر السوق (نفس الموديل الجديد في كل المتاجر، آخر 48 ساعة):
    ♻️ مستعمل/مجدد أرخص من الجديد بنسبة كبيرة، و🔥 خصم المتجر + أرخص من متجر ثاني."""
    key = model_key(item["title"], allow_resale=True)
    if not key:
        return []
    recs = {m: r for m, r in models.get(key, {}).items() if now - r["ts"] <= 48 * 3600}
    if not recs:
        return []
    label = lambda m: CONFIG["markets"].get(m, {}).get("label", m)
    out = []
    if is_resale(item["title"]):
        m, r = min(recs.items(), key=lambda kv: kv[1]["price"])
        cheaper = (1 - price / r["price"]) * 100
        if CONFIG.get("used_deal_pct", 35) <= cheaper <= 80:  # أكثر من 80% = غالباً منتج ثاني/تقليد
            out.append(f"♻️ مستعمل/مجدد أرخص {cheaper:.0f}% من الجديد ({r['price']:,.0f} في {label(m)})")
    elif item.get("list_price"):
        disc = (1 - price / item["list_price"]) * 100
        others = {m: r for m, r in recs.items() if m != market_name}
        if disc >= CONFIG.get("verified_discount_pct", 25) and others:
            m, r = min(others.items(), key=lambda kv: kv[1]["price"])
            cheaper = (1 - price / r["price"]) * 100
            if 10 <= cheaper <= 75:
                out.append(f"🔥 خصم {disc:.0f}% وأرخص {cheaper:.0f}% من {label(m)} ({r['price']:,.0f}) — عرض مؤكد")
    return out


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
        if cheaper > CONFIG.get("cross_store_max_pct", 75):
            continue  # فرق خيالي = غالباً منتج ثاني (إكسسوار) مو نفس الموديل؛ الأخطاء الحقيقية تمسكها الحدود
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
            for br in txt.find_all("br"):
                br.replace_with("\n")
            text = txt.get_text("")  # المسافات الحقيقية موجودة في النص؛ الفاصل يكسر "ال"+"جسم"
            text = "\n".join(re.sub(r"[ \t]+", " ", ln).strip() for ln in text.split("\n"))
            out.append((pid, re.sub(r"\n{3,}", "\n\n", text).strip()))
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
            # نشيل الروابط (فيها حروف عشوائية مثل ...PC...) ونطابق كلمات كاملة بس
            low = re.sub(r"https?://\S+|\b[\w.-]+\.(?:com|net|sa|gl|ly|me)/\S*", " ", text.lower())
            # العربي: جزء من الكلمة (الشاشة، المكيفات). الإنجليزي: كلمة كاملة (tv ما تطابق داخل كلمة ثانية)
            arabic = [w for w in words if re.search(r"[\u0600-\u06FF]", w)]
            latin = [w for w in words if w not in arabic]
            hit = not words or any(w in low for w in arabic) or _has_word(low, latin)
            # تريندول (روابط ty.gl): أي قسم، بشرط عرض قوي — خصم كبير أو "ببلاش"
            if not hit and CONFIG.get("trendyol_any_category") and re.search(r"ty\.gl/|trendyol", text, re.I):
                pcts = [int(x) for x in re.findall(r"(\d{2})\s*[%٪]", text)]
                strong = CONFIG.get("trendyol_min_pct", 60)
                # السعر قبل الخصم: "بدل 58 ريال" أو "(بدل 58)"؛ لو ما انكتب نحسبه من السعر والنسبة
                olds = [float(x) for x in re.findall(r"بدل\s*([\d,.]+)", text.replace(",", ""))]
                min_old = CONFIG.get("trendyol_min_old_price", 150)
                worth = not olds or max(olds) >= min_old
                hit = (any(p >= strong for p in pcts) and worth) or \
                    any(w in text for w in ("خطأ تسعير", "غلط تسعير"))
            if not hit:
                continue
            # نفس العرض منشور في أكثر من قناة (أو مكرر بنفس القناة): بصمة النص بدون روابط/رموز
            fp = re.sub(r"\W+", "", _ar_fold(re.sub(r"https?://\S+", "", text.lower())))[:160]
            fps: dict = state.setdefault("tg_fp", {})
            if fp in fps and time.time() - fps[fp] < 3 * 86400:
                continue
            fps[fp] = time.time()
            tag = "🛍️ تريندول — " if re.search(r"ty\.gl/|trendyol", text, re.I) else ""
            msg = (f"📢 {tag}من قناة @{html.escape(ch)}\n{html.escape(text[:700])}\n"
                   f'🔗 <a href="https://t.me/{ch}/{pid}">افتح المنشور</a>')
            if send(msg, silent=True):  # منشورات القنوات: إشعار صامت
                sent += 1
    return sent


# ---------- نشرات العروض (D4D) ----------

def parse_flyers(page: str) -> list[dict]:
    """D4D: النشرات في JSON-LD كـ SaleEvent (اسم المتجر، الوصف، الرابط، التواريخ)."""
    out = []
    for m in re.finditer(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S):
        try:
            data = json.loads(m.group(1))
        except ValueError:
            continue
        for d in _walk(data):
            if d.get("@type") == "SaleEvent" and d.get("url"):
                out.append({"store": str(d.get("name", "")), "desc": str(d.get("description", "")),
                            "url": d["url"], "end": str(d.get("endDate", ""))[:10]})
    return out


def scan_flyers(state: dict, session: requests.Session) -> int:
    cfg = CONFIG.get("flyers", {})
    if not cfg.get("url"):
        return 0
    try:
        r = session.get(cfg["url"], timeout=25, headers={"User-Agent": USER_AGENTS[0]})
    except requests.RequestException:
        return 0
    if r.status_code != 200:
        print("flyers", r.status_code)
        return 0
    seen: dict = state.setdefault("flyers_seen", {})
    first_time = not seen
    stores = [_ar_fold(x.lower()) for x in cfg.get("stores", [])]
    sent = 0
    for f in parse_flyers(r.text):
        if f["url"] in seen:
            continue
        seen[f["url"]] = time.time()
        name = _ar_fold(f["store"].lower())
        if first_time or not any(s in name for s in stores):
            continue  # أول مرة: نحفظ الموجود بس
        msg = (f"🗞️ نشرة عروض جديدة: <b>{html.escape(f['store'])}</b>\n{html.escape(f['desc'][:200])}"
               + (f"\n⏳ لين {f['end']}" if f["end"] else "") + f'\n🔗 <a href="{f["url"]}">افتح النشرة</a>')
        if send(msg, silent=True):
            sent += 1
    print(f"flyers: {len(seen)} معروفة، {sent} جديدة انرسلت")
    for k in [k for k, v in seen.items() if time.time() - v > 30 * 86400]:
        del seen[k]
    return sent


# ---------- App Store: الاشتراكات (السعودية مقابل تركيا) والتطبيقات المدفوعة ----------

def _num(txt: str) -> float | None:
    """'₺3.649,99' / 'SAR 7.99' / '1,099.00' → رقم. الفاصل الأخير هو الكسور."""
    t = re.sub(r"[^\d.,]", "", txt or "")
    if not t:
        return None
    if "," in t and "." in t:
        dec = "," if t.rfind(",") > t.rfind(".") else "."
        t = t.replace("." if dec == "," else ",", "").replace(dec, ".")
    elif "," in t:
        t = t.replace(",", ".") if len(t.split(",")[-1]) == 2 else t.replace(",", "")
    elif t.count(".") > 1 or (t.count(".") == 1 and len(t.split(".")[-1]) == 3):
        t = t.replace(".", "")
    try:
        return float(t)
    except ValueError:
        return None


def parse_iap(page: str) -> dict[str, float]:
    """صفحة التطبيق في App Store → {اسم الاشتراك: أقل سعر}. من Annotation 'In-App Purchases' (textPair)."""
    m = re.search(r'id="serialized-server-data"[^>]*>(.*?)</script>', page, re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(1))
    except ValueError:
        return {}
    out: dict[str, float] = {}
    for d in _walk(data):
        if d.get("$kind") == "Annotation" and "purchase" in str(d.get("title", "")).lower():
            for it in d.get("items") or []:
                if it.get("$kind") == "textPair":
                    name, price = str(it.get("leadingText", "")).strip(), _num(it.get("trailingText", ""))
                    if name and price:
                        out[name] = min(price, out.get(name, price))
    return out


def app_page(session: requests.Session, app_id: str, cc: str) -> str | None:
    try:
        r = session.get(f"https://apps.apple.com/{cc}/app/id{app_id}", timeout=25,
                        headers={"User-Agent": USER_AGENTS[0], "Accept-Language": "en-US,en;q=0.9"})
    except requests.RequestException:
        return None
    return r.content.decode("utf-8", "replace") if r.status_code == 200 else None


def fx_rate(state: dict, session: requests.Session, cur: str = "TRY") -> float:
    """سعر صرف العملة مقابل الريال (open.er-api.com المجاني)، مخزّن 12 ساعة؛ لو فشل نستخدم config."""
    fx = state.setdefault("fx", {})
    c = fx.get(cur)
    if c and time.time() - c["ts"] < 12 * 3600:
        return c["rate"]
    try:
        r = session.get(f"https://open.er-api.com/v6/latest/{cur}", timeout=20).json()
        rate = float(r["rates"]["SAR"])
        fx[cur] = {"rate": rate, "ts": time.time()}
        return rate
    except Exception:
        return CONFIG.get("fx_fallback", {}).get(cur, 0.09)


def find_app(session: requests.Session, term: str) -> dict | None:
    try:
        r = session.get("https://itunes.apple.com/search", timeout=20,
                        params={"term": term, "country": "sa", "entity": "software", "limit": 1}).json()
    except Exception:
        return None
    res = r.get("results") or []
    return res[0] if res else None


def compare_subs(state: dict, session: requests.Session, app_id: str) -> list[dict]:
    """اشتراكات التطبيق: سعرها في السعودية وتركيا (بالريال) ونسبة الفرق."""
    sa, tr = parse_iap(app_page(session, app_id, "sa") or ""), parse_iap(app_page(session, app_id, "tr") or "")
    rate = fx_rate(state, session, "TRY")
    rows = []
    for name in sorted(set(sa) | set(tr), key=lambda n: sa.get(n, 1e9)):
        s_, t_ = sa.get(name), tr.get(name)
        t_sar = round(t_ * rate, 2) if t_ else None
        cheaper = round((1 - t_sar / s_) * 100) if s_ and t_sar else None
        rows.append({"name": name, "sa": s_, "tr": t_, "tr_sar": t_sar, "cheaper": cheaper})
    return rows


def format_subs(app_name: str, rows: list[dict]) -> str:
    if not rows:
        return f"📱 <b>{html.escape(app_name)}</b>: ما لقيت اشتراكات ظاهرة في صفحته."
    lines = [f"📱 اشتراكات <b>{html.escape(app_name)}</b> (🇸🇦 مقابل 🇹🇷 بالريال):"]
    for r in rows[:12]:
        sa = f"{r['sa']:,.2f}" if r["sa"] else "—"
        tr = f"{r['tr_sar']:,.2f}" if r["tr_sar"] else "—"
        tag = f" ⬇️ {r['cheaper']}%" if r["cheaper"] and r["cheaper"] > 0 else ""
        lines.append(f"• {html.escape(r['name'])}: 🇸🇦 {sa} | 🇹🇷 {tr}{tag}")
    lines.append("⚠️ الشراء من المتجر التركي يحتاج حساب تركي برصيد تركي، وأبل ممكن توقف الحسابات المخالفة.")
    return "\n".join(lines)


def scan_subscriptions(state: dict, session: requests.Session) -> int:
    """كل few ساعات: اشتراكات التطبيقات المتابَعة. اشتراك نزل سعره (عرض بلاك فرايدي مثلاً) = تنبيه."""
    cfg = CONFIG.get("app_subs", {})
    last = state.get("subs_last", 0)
    if not cfg.get("apps") or time.time() - last < cfg.get("every_hours", 6) * 3600:
        return 0
    state["subs_last"] = time.time()
    seen: dict = state.setdefault("subs_prices", {})
    sent = 0
    for name, app_id in cfg["apps"].items():
        for cc in ("sa", "tr"):
            cur = parse_iap(app_page(session, app_id, cc) or "")
            for item, price in cur.items():
                key = f"{app_id}:{cc}:{item}"
                prev = seen.get(key)
                seen[key] = min(price, prev) if prev else price
                if prev and price <= prev * (1 - cfg.get("drop_pct", 15) / 100):
                    unit = "ر.س" if cc == "sa" else "₺"
                    msg = (f"📱🔥 عرض اشتراك: <b>{html.escape(name)}</b> — {html.escape(item)}\n"
                           f"{'🇸🇦' if cc == 'sa' else '🇹🇷'} {price:,.2f} {unit} بدل {prev:,.2f} "
                           f"(أرخص {(1 - price / prev) * 100:.0f}%)\n"
                           f'🔗 <a href="https://apps.apple.com/{cc}/app/id{app_id}">صفحة التطبيق</a>')
                    if send(msg, silent=True):
                        sent += 1
            time.sleep(1)
    return sent


def scan_paid_apps(state: dict, session: requests.Session) -> int:
    """أعلى التطبيقات المدفوعة في المتجر السعودي: صار مجاني أو نزل 50%+ = تنبيه."""
    cfg = CONFIG.get("paid_apps", {})
    if not cfg.get("enabled") or time.time() - state.get("paid_last", 0) < cfg.get("every_hours", 3) * 3600:
        return 0
    state["paid_last"] = time.time()
    try:
        feed = session.get(f"https://rss.applemarketingtools.com/api/v2/sa/apps/top-paid/{cfg.get('top', 100)}/apps.json",
                           timeout=20).json()
        ids = [x["id"] for x in feed["feed"]["results"]]
        look = session.get("https://itunes.apple.com/lookup", timeout=25,
                           params={"id": ",".join(ids), "country": "sa"}).json()
    except Exception as e:
        print("paid apps error:", type(e).__name__)
        return 0
    seen: dict = state.setdefault("paid_prices", {})
    sent = 0
    for a in look.get("results", []):
        aid, price = str(a.get("trackId")), float(a.get("price") or 0)
        prev = seen.get(aid)
        seen[aid] = price
        if prev and prev > 0 and (price == 0 or price <= prev * 0.5):
            what = "صار <b>مجاني</b> 🆓" if price == 0 else f"نزل لـ {price:,.2f} ر.س بدل {prev:,.2f}"
            if send(f"📱 {html.escape(a.get('trackName', ''))} {what}\n"
                    f'🔗 <a href="{a.get("trackViewUrl", "")}">افتحه في App Store</a>', silent=True):
                sent += 1
    return sent


# ---------- أكواد الخصم (مواقع الكوبونات السعودية) ----------

_NOT_CODES = {"KSA", "SAR", "USD", "AED", "VIP", "APP", "FAQ", "PDF", "NEW", "OFF", "FREE", "CODE", "COUPON", "AMAZON",
              "NOON", "EXTRA", "SALE", "BEST", "HTML", "HTTP", "HTTPS", "JSON", "UTC", "GMT", "TOP", "LED", "USB"}


def extract_codes(page: str) -> dict[str, str]:
    """يطلع أكواد الخصم من صفحة موقع كوبونات: {الكود: وصف قصير}. يتعرف على الكود اللي جنب كلمة code/كود."""
    text = html.unescape(re.sub(r"<[^>]+>", " ", page))
    text = re.sub(r"\s+", " ", text)
    found: dict[str, str] = {}
    pats = [r"\bCode\s+([A-Z0-9]{3,15})\b",                       # جدول الموفر: Code | NEW20
            r"(?:coupon|code|كود|كوبون)\s*[:(\[\"']*\s*\(?([A-Z][A-Z0-9]{2,14})\)?",
            r"\(([A-Z][A-Z0-9]{2,14})\)"]                        # الكوبون: (NEW20)
    for pat in pats:
        for m in re.finditer(pat, text, re.I if "coupon" in pat else 0):
            code = m.group(1).upper()
            if code in _NOT_CODES or code.isdigit() or not re.search(r"[A-Z]", code) or re.fullmatch(r"20\d\d", code):
                continue
            if not re.search(r"\d", code) and len(code) < 4:
                continue
            if code not in found:
                ctx = text[max(0, m.start() - 60): m.end() + 90]
                pct = re.search(r"(\d{1,2})\s*%", ctx)
                cap = re.search(r"(?:up to|max(?:imum)? discount|حتى)\s*(?:SAR\s*)?(\d{2,4})", ctx, re.I)
                found[code] = (f"{pct.group(1)}%" if pct else "") + (f" حتى {cap.group(1)} ر.س" if cap else "")
    return found


def collect_codes(store: str, session: requests.Session) -> list[dict]:
    """يجمع أكواد متجر من كل مواقع الكوبونات ويرتبها حسب كم موقع ذكر الكود."""
    agg: dict[str, dict] = {}
    for url in CONFIG.get("coupon_sites", {}).get(store, []):
        try:
            r = session.get(url, timeout=25, headers={"User-Agent": USER_AGENTS[0]})
        except requests.RequestException:
            continue
        if r.status_code != 200 or is_blocked(r.text):
            continue
        site = url.split("/")[2].replace("www.", "")
        for code, desc in extract_codes(r.text).items():
            a = agg.setdefault(code, {"code": code, "desc": "", "sites": set()})
            a["sites"].add(site)
            if len(desc) > len(a["desc"]):
                a["desc"] = desc
    out = sorted(agg.values(), key=lambda a: (-len(a["sites"]), a["code"]))
    return [dict(a, sites=sorted(a["sites"])) for a in out]


def cached_codes(state: dict, store: str, session: requests.Session) -> list[dict]:
    c = state.setdefault("coupons", {}).get(store)
    if not c or time.time() - c["ts"] > CONFIG.get("coupon_refresh_hours", 6) * 3600:
        c = {"ts": time.time(), "codes": collect_codes(store, session)}
        state["coupons"][store] = c
    return c["codes"]


def format_codes(store: str, codes: list[dict]) -> str:
    label = CONFIG.get("coupon_labels", {}).get(store, store)
    if not codes:
        return f"🎟️ ما لقيت أكواد لـ {html.escape(label)} الحين."
    lines = [f"🎟️ أكواد خصم <b>{html.escape(label)}</b> (مرتبة حسب كم موقع ذكرها):"]
    for a in codes[:8]:
        lines.append(f"• <code>{a['code']}</code> {html.escape(a['desc'])} — {len(a['sites'])} موقع")
    lines.append("⚠️ ما جربتها عند الدفع؛ بعضها لأول طلب أو لبطاقات بنوك معينة.")
    return "\n".join(lines)


# ---------- أوامر تيليجرام: /find ----------

FIND_MARKETS = ["sa", "almanea", "mahally"]


def find_matches(query: str, items: list[dict]) -> list[dict]:
    """نتائج تطابق المنتج المطلوب: كل كلمات البحث موجودة، ومو إكسسوار/خدمة/تقليد."""
    q = [w for w in re.split(r"\s+", _ar_fold(norm_title(query))) if w]
    out = []
    for it in items:
        if excluded(it["title"]):
            continue
        t = _ar_fold(norm_title(it["title"]))
        pos = [t.find(w) for w in q]
        if not q or any(p < 0 for p in pos):
            continue
        head = t[:min(pos)]  # "Case for iPhone 16" = الكفر قبل اسم المنتج
        if _has_word(head, CONFIG.get("floor_accessory_words", []) + CONFIG.get("model_accessory_words", [])):
            continue
        if _has_word(t, CONFIG.get("floor_accessory_anywhere", [])):
            continue
        out.append(it)
    return out


def find_product(query: str, session: requests.Session) -> list[tuple[str, dict]]:
    """يدوّر على المنتج في كل المتاجر الشغالة ويرجع النتائج المطابقة مرتبة من الأرخص."""
    results = []
    for name in FIND_MARKETS:
        m = CONFIG["markets"].get(name)
        if not m:
            continue
        url = search_url({"market": name, "q": query})
        page = fetch_browser(url) if m.get("browser") else fetch(session, url)
        if not page or is_blocked(page):
            print(f"/find {name}: ما قدرنا نفتح الصفحة")
            continue
        items = find_matches(query, PARSERS[m.get("parser", "amazon")](page))
        results += [(name, it) for it in items]
        time.sleep(random.uniform(*m.get("gap", (2, 4))))
    results.sort(key=lambda x: x[1]["price"] - (x[1].get("coupon") or 0))
    return results


def format_find(query: str, results: list[tuple[str, dict]]) -> str:
    if not results:
        return (f"🔍 <b>{html.escape(query)}</b>\nما لقيت نتائج مطابقة الحين (أو المتاجر رفضت الطلب). "
                "جرّب اسم أدق، مثلاً: /find airpods pro 2")
    lines = [f"🔍 أرخص أسعار <b>{html.escape(query)}</b>:"]
    for i, (name, it) in enumerate(results[:CONFIG.get("find_top", 5)], 1):
        m = CONFIG["markets"][name]
        url = it.get("url") or f"https://{m['host']}/dp/{it['asin']}"
        price = it["price"] - (it.get("coupon") or 0)
        extra = f" (بعد كوبون {it['coupon']:,.0f})" if it.get("coupon") else ""
        tag = " ♻️" if is_resale(it["title"]) else ""
        lines.append(f"{i}. <b>{price:,.0f} ر.س</b>{extra} — {m.get('label', name)}{tag}\n"
                     f'   <a href="{url}">{html.escape(it["title"][:80])}</a>')
    stores = sorted({CONFIG["markets"][n].get("label", n) for n, _ in results})
    lines.append(f"📊 {len(results)} نتيجة من: {'، '.join(stores)}")
    return "\n".join(lines)


def handle_commands(state: dict, session: requests.Session) -> int:
    """يقرأ رسايلك للبوت (getUpdates) وينفّذ الأوامر. يرد على صاحب البوت بس (DEALS_CHAT_ID)."""
    token, chat = os.environ.get("DEALS_BOT_TOKEN"), os.environ.get("DEALS_CHAT_ID")
    if not token or not chat:
        return 0
    try:
        r = session.get(f"https://api.telegram.org/bot{token}/getUpdates", timeout=20,
                        params={"offset": state.get("tg_offset", 0) + 1, "timeout": 0}).json()
    except Exception as e:
        print("getUpdates failed:", type(e).__name__)
        return 0
    done = 0
    for upd in r.get("result", []):
        state["tg_offset"] = max(state.get("tg_offset", 0), upd.get("update_id", 0))
        msg = upd.get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != str(chat):
            continue  # أحد غيرك كتب للبوت: نتجاهله
        text = (msg.get("text") or "").strip()
        m = re.match(r"^(?:/find|/بحث|بحث|ابحث|دور)\s+(.+)$", text, re.I | re.S)
        if m:
            query = m.group(1).strip()[:60]
            send(f"⏳ أدوّر على <b>{html.escape(query)}</b> في كل المتاجر...", silent=True)
            send(format_find(query, find_product(query, session)))
            done += 1
        elif re.match(r"^(?:/codes|/اكواد|اكواد|كود|كوبون)(?:\s+(\S+))?$", text, re.I):
            arg = (re.match(r"^\S+\s*(.*)$", text).group(1) or "amazon").strip().lower()
            store = next((k for k, v in CONFIG.get("coupon_aliases", {}).items() if arg in v), arg)
            send(format_codes(store, cached_codes(state, store, session)))
            done += 1
        elif re.match(r"^(?:/app|/تطبيق|تطبيق|اشتراك)\s+(.+)$", text, re.I | re.S):
            term = re.match(r"^\S+\s+(.+)$", text, re.S).group(1).strip()[:60]
            app = find_app(session, term)
            if not app:
                send(f"📱 ما لقيت تطبيق باسم {html.escape(term)}")
            else:
                send(format_subs(app["trackName"], compare_subs(state, session, str(app["trackId"]))))
            done += 1
        elif text.lower() in ("/start", "/help", "مساعدة"):
            send("الأوامر:\n/find اسم المنتج — أرخص سعر له في أمازون والمنيع ومحلي\n"
                 "مثال: /find ps5 slim  أو  ابحث ايفون 16 برو 256\n"
                 "/codes amazon — أكواد الخصم (amazon, noon, extra, jarir, namshi)\n"
                 "/app google one — أسعار اشتراكات التطبيق 🇸🇦 مقابل 🇹🇷", silent=True)
    return done


# ---------- الحالة ----------

def load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


# ---------- تيليجرام ----------

def send(text: str, silent: bool = False) -> bool:
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
            "chat_id": chat, "text": text, "parse_mode": "HTML", "disable_web_page_preview": False,
            "disable_notification": silent})
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


def format_alert(item: dict, market: dict, reasons: list[str], season: dict | None = None) -> str:
    cur = market["currency"]
    flag = market.get("flag") or ("🇸🇦" if market["host"].endswith(".sa") else "🇺🇸" if market["host"].endswith(".com") else "🌍")
    flag = f"{flag} {market.get('label', 'أمازون')}"
    url = item.get("url") or f"https://{market['host']}/dp/{item['asin']}"
    tag = "♻️ <b>مجدد / Resale</b>\n" if is_resale(item["title"]) else ""
    lines = [f"{tag}{flag} <b>{html.escape(item['title'][:150])}</b>",
             f"💰 السعر: <b>{item['price']:,.2f} {cur}</b>"]
    if item.get("coupon"):
        lines.append(f"🎟️ كوبون -{item['coupon']:,.0f} → <b>{item['price'] - item['coupon']:,.2f} {cur}</b> بعد الكوبون "
                     "(فعّله من صفحة المنتج)")
    lines += reasons
    if season:
        lines.append(f"🗓️ موسم {season['name']}")
    lines.append(f'🔗 <a href="{url}">افتح المنتج واشترِ بسرعة</a>')
    return "\n".join(lines)


# ---------- التشغيل ----------

_browser = {}


def fetch_browser(url: str) -> str | None:
    """متصفح Chromium عادي (بدون تخفّي) للمتاجر اللي تحمّل منتجاتها بالجافاسكربت (محلي).
    يرجّع JSON [[رابط المنتج, نص البطاقة]]."""
    try:
        if "page" not in _browser:
            from playwright.sync_api import sync_playwright
            _browser["pw"] = sync_playwright().start()
            _browser["b"] = _browser["pw"].chromium.launch()
            _browser["page"] = _browser["b"].new_page(locale="ar-SA")
        page = _browser["page"]
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(7000)
        if re.search(r"Attention Required|Just a moment|التحقق البشري", page.title()):
            return None
        cards = page.eval_on_selector_all("a[href*='/products/']", """els => els.map(a => {
            let n = a; for (let i = 0; i < 4 && n.parentElement; i++) { n = n.parentElement; if (n.innerText.length > 30) break; }
            return [a.getAttribute('href'), n.innerText];
        })""")
        return json.dumps(cards, ensure_ascii=False)
    except Exception as e:
        print("browser error", type(e).__name__, str(e)[:200])
        return None


def close_browser() -> None:
    try:
        if "b" in _browser:
            _browser["b"].close()
            _browser["pw"].stop()
    except Exception:
        pass
    _browser.clear()


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
    parts = [f"k={q}"] + ([m["params"]] if m.get("params") else []) + ([src["extra"]] if src.get("extra") else [])
    return f"https://{m['host']}{m.get('path_prefix', '')}/s?" + "&".join(parts)


def active_season(now: float) -> dict | None:
    """الموسم الحالي من config (from/to: "MM-DD" كل سنة، أو "YYYY-MM-DD" لتاريخ محدد مثل رمضان)."""
    d = datetime.fromtimestamp(now, timezone.utc)
    for se in CONFIG.get("seasons", []):
        f, t = se["from"], se["to"]
        cur = d.strftime("%Y-%m-%d") if len(f) == 10 else d.strftime("%m-%d")
        if f <= cur <= t:
            return se
    return None


def pick_batch(state: dict, now: float) -> list[dict]:
    """لكل متجر: عدد صفحات وفترة خاصة فيه (أمازون أقل وأبطأ عشان ما تحظرنا)، وبالتناوب على كلمات البحث."""
    cursors: dict = state.setdefault("cursors", {})
    last: dict = state.setdefault("last_scan", {})
    per_market: list[list[dict]] = []
    extra = (active_season(now) or {}).get("extra_pages", {})
    for name, m in CONFIG["markets"].items():
        srcs = [x for x in CONFIG["searches"] if x["market"] == name]
        base = m.get("pages_per_run", CONFIG["pages_per_run"])
        n = min(base + (extra.get(name, 0) if base else 0), len(srcs))
        if not n or now - last.get(name, 0) < m.get("every_min", 0) * 60 - 30:
            continue
        c = cursors.get(name, 0) % len(srcs)
        picked = [srcs[(c + i) % len(srcs)] for i in range(n)]
        if m.get("gap"):  # فاصل خاص قبل كل صفحة من هالمتجر (أمازون: 20-30 ث يقلل الحظر)
            picked = [dict(x, _gap=tuple(m["gap"])) for x in picked]
        per_market.append(picked)
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
    season = active_season(time.time())
    if season:
        print(f"موسم: {season['name']} (صفحات إضافية {season.get('extra_pages')})")
    models: dict = state.setdefault("models", {})     # موديل -> {متجر: {"price":, "ts":}}

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if stats["day"] != today:
        if stats["day"]:
            send(f"✅ صياد الأسعار شغال\nأمس: {stats['pages']} صفحة، {stats['items']} منتج، "
                 f"{stats['alerts']} تنبيه، {stats['blocked']} حظر مؤقت", silent=True)
        stats.update(day=today, pages=0, blocked=0, items=0, alerts=0)

    # أول تشغيل بعد إضافة الـ token: رسالة ترحيب + رقم المحادثة (حتى لو ما فيه عروض)
    if os.environ.get("DEALS_BOT_TOKEN") and not state.get("welcomed"):
        if send("👋 صياد أخطاء الأسعار شغال!\nبيفحص إلكترونيات أمازون السعودية والمنيع (جديد + مجدد) "
                "ويرسل لك هنا أي سعر غلط أو تخفيض مجنون."):
            state["welcomed"] = True

    now = time.time()
    session = requests.Session()
    try:
        handle_commands(state, session)  # أوامرك (/find) أول شي عشان الرد يكون أسرع
    except Exception as e:
        print("commands error:", type(e).__name__, e)
    blocked_hosts: set[str] = set()
    exp_name = apply_experiment(state, batch)
    exp_ok = exp_blocked = 0
    for i, src in enumerate(batch):
        if i:
            time.sleep(random.uniform(*src.get("_gap", (3, 7))))
        market = CONFIG["markets"][src["market"]]
        if market["host"] in blocked_hosts:  # أمازون رفضتنا: نوقف الطلب عليها لين التشغيلة الجاية
            continue
        url = src.get("_url") or search_url(src)
        page = fetch_browser(url) if market.get("browser") else fetch(session, url)
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
            bf = market.get("floor_requires_brand", False)
            reasons = evaluate(ev, market["currency"], hist, market.get("trust_list_price", True), bf)
            if not excluded(it["title"]) and (not bf or starts_with_brand(it["title"])):
                cross = cross_store_reason(ev, src["market"], models, now)
                if cross:
                    reasons.append(cross)
                if market.get("used_fair") and not any(r.startswith("🚨") for r in reasons):
                    uf = used_fair_reason(it["title"], ev["price"])
                    if uf:
                        reasons.append(uf)
                else:
                    reasons += [r for r in market_deal_reasons(it, ev["price"], src["market"], models, now)
                                if r not in reasons]
            if reasons:
                # نفس العرض ما يتكرر: بالرقم، أو بالاسم+السعر (نفس الإعلان من بائع/رقم ثاني)
                tkey = "t:" + re.sub(r"\W+", " ", _ar_fold(norm_title(it["title"])))[:70] + f":{round(it['price'])}"
                prev = alerted.get(key) or alerted.get(tkey)
                fresh = (not prev or now - prev["ts"] > CONFIG["realert_hours"] * 3600
                         or it["price"] < prev["price"] * 0.95)  # يرجع بس إذا نزل 5% زيادة
                # 🚨 خطأ تسعير = إشعار بصوت، باقي العروض = إشعار صامت
                loud = any(r.startswith("🚨") for r in reasons)
                msg = format_alert(it, market, reasons, season)
                cstore = market.get("coupon_store")
                if fresh and cstore:
                    top = [a for a in cached_codes(state, cstore, session) if len(a["sites"]) >= 2][:2]
                    if top:
                        msg += "\n🎟️ أكواد ممكن تزيد الخصم: " + "، ".join(
                            f"<code>{a['code']}</code> {html.escape(a['desc'])}" for a in top)
                if fresh and send(msg, silent=not loud):
                    alerted[key] = alerted[tkey] = {"price": it["price"], "ts": now}
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

    close_browser()
    record_experiment(state, exp_name, exp_ok, exp_blocked)
    for fn in (scan_subscriptions, scan_paid_apps):
        try:
            stats["alerts"] += fn(state, session)
        except Exception as e:
            print(fn.__name__, "error:", type(e).__name__, e)
    try:
        stats["alerts"] += scan_flyers(state, session)
    except Exception as e:
        print("flyers error:", type(e).__name__, e)
    try:
        stats["alerts"] += scan_channels(state, session)
    except Exception as e:  # القنوات إضافة: ما نخليها توقف الصيد
        print("telegram channels error:", type(e).__name__, e)

    # نظافة: نحذف تنبيهات أقدم من أسبوع
    for k in [k for k, v in alerted.items() if now - v["ts"] > 30 * 86400]:
        del alerted[k]
    save_state(state)
    return 0


if __name__ == "__main__":
    sys.exit(run())
