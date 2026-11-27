"""فحص تجريبي: هل نقدر نقرأ صفحات البحث في متاجر سعودية؟ يطبع ملخص لكل متجر.

طلب واحد عادي لكل متجر (بدون تحايل على أي حماية)، والنتيجة في سجل GitHub Actions.
"""
import json
import os
import re
import sys

import requests

STORES = {
    "extra": "https://www.extra.com/en-sa/search/?q=iphone",
    "almanea": "https://www.almanea.sa/en/search?q=iphone",
    "jarir": "https://www.jarir.com/sa-en/catalogsearch/result?search=iphone",
    "noon": "https://www.noon.com/saudi-en/search/?q=iphone",
    "blackbox": "https://www.blackbox.com.sa/en/search?q=iphone",
    "mahally_home": "https://mahally.com/ar/",
    "mahally_search": "https://mahally.com/ar/search?q=iphone",
    "mahally_search2": "https://mahally.com/ar/products?search=iphone",
    "trendyol_en": "https://www.trendyol.com/en/sr?q=iphone",
    "trendyol_ar": "https://www.trendyol.com/ar/sr?q=iphone",
    "trendyol_home": "https://www.trendyol.com/en",
    "new_virgin": "https://www.virginmegastore.sa/en/search?q=iphone",
    "new_lulu": "https://www.luluhypermarket.com/en-sa/search?q=iphone",
    "new_carrefour": "https://www.carrefourksa.com/mafsau/en/search?keyword=iphone",
    "new_desertcart": "https://www.desertcart.sa/search?query=iphone",
    "new_ubuy": "https://www.ubuy.com.sa/en/search/?q=iphone",
    "new_clicflyer": "https://clicflyer.com/en/saudi-arabia/riyadh/offers",
    "new_d4d": "https://d4donline.com/en/saudi-arabia/riyadh/offers",
    "new_stc": "https://www.stc.com.sa/en/personal/devices/smartphones",
    "pc_newegg": "https://www.newegg.com/global/sa-en/p/pl?d=rtx+4070",
    "pc_desertcart": "https://www.desertcart.com.sa/category/electronics/computers/components",
    "pc_desertcart_search": "https://www.desertcart.com.sa/search?query=rtx%204070",
    "pc_opensooq": "https://sa.opensooq.com/en/computers-and-laptops/computer-parts-and-components",
    "app_reddit_rss": "https://www.reddit.com/r/AppHookup/new/.rss",
    "app_reddit_json": "https://www.reddit.com/r/AppHookup/new.json?limit=10",
    "app_oldreddit": "https://old.reddit.com/r/AppHookup/new/",
    "app_iosnoops": "https://www.iosnoops.com/",
    "app_iosnoops_free": "https://www.iosnoops.com/iphone-ipad-apps-gone-free/",
    "app_appsliced": "https://appsliced.co/apps/iphone?sort=latest&price=free&cat=0&page=1",
    "app_9to5toys_feed": "https://9to5toys.com/guides/apps-games/feed/",
    "app_9to5toys_tag": "https://9to5toys.com/guides/ios/feed/",
    "app_igeeks": "https://www.igeeksblog.com/ios-apps-price-drop/",
    "app_appspree": "https://appspree.com/",
    "app_donanimhaber": "https://www.donanimhaber.com/rss/tum/",
    "app_stacksocial": "https://www.stacksocial.com/collections/apps-software",
    "app_appadvice": "https://appadvice.com/apps-gone-free",
    "cpn_almowafir": "https://almowafir.com/en/store/amazon/",
    "cpn_everysaving": "https://www.everysavingksa.com/coupons/amazon.sa",
    "cpn_alcoupon": "https://saudi.alcoupon.com/en/discount-codes/amazon-saudi-arabia",
    "cpn_sahseh": "https://www.sahseh.co/stores/en-amazon?id=465",
    "cpn_clicflyer": "https://clicflyer.com/coupons/en/saudi-arabia/codes/amazon-1607",
}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"


def summarize(name: str, url: str) -> None:
    print(f"\n===== {name} =====\n{url}")
    try:
        r = requests.get(url, timeout=30, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    except requests.RequestException as e:
        print("ERROR", type(e).__name__, e)
        return
    t = r.text
    title = re.search(r"<title[^>]*>(.*?)</title>", t, re.S | re.I)
    print(f"status={r.status_code} final_url={r.url} len={len(t)} type={r.headers.get('content-type')}")
    print("title:", (title.group(1).strip()[:120] if title else None))
    markers = {
        "__NEXT_DATA__": "__NEXT_DATA__" in t,
        "ld+json": t.count("application/ld+json"),
        "SAR": len(re.findall(r"\bSAR\b", t)),
        "ر.س": t.count("ر.س"),
        '"price"': t.count('"price"'),
        "iphone": t.lower().count("iphone"),
        "captcha/challenge": bool(re.search(r"captcha|cf-chl|challenge-platform|akamai|px-captcha", t, re.I)),
    }
    print("markers:", json.dumps(markers, ensure_ascii=False))
    m = re.search(r'"price"\s*:', t) or re.search(r"\bSAR\b", t)
    if m:
        s = max(0, m.start() - 400)
        print("snippet:", re.sub(r"\s+", " ", t[s:m.start() + 400]))
    # روابط API ظاهرة في الصفحة (تساعد نعرف من وين تجي الأسعار)
    # أكواد خصم ظاهرة (حروف كبيرة/أرقام 3-15) قرب كلمة code/كود
    codes = re.findall(r'(?:data-code|data-clipboard-text|code)["\'=:\s>]+([A-Z0-9]{3,15})\b', t)
    print("codes-like:", sorted(set(codes))[:15])
    # أشكال أسعار ثانية + روابط منتجات (لمتاجر مثل ديزرت كارت)
    print("SR/﷼/AED counts:", len(re.findall(r"\bSR\b", t)), t.count("﷼"), len(re.findall(r"\bSAR\s?\d", t)))
    prods = sorted(set(re.findall(r'href="(/products/[^"?]+)', t)))[:5]
    print("product links:", len(set(re.findall(r'href="(/products/[^"?]+)', t))), prods)
    i = t.find("/products/")
    if i > 0:
        print("around first product:", re.sub(r"<[^>]+>", " | ", t[i - 200:i + 1500])[:1200])
    for key in ("__NUXT__", "window.__INITIAL_STATE__", "__APOLLO_STATE__", "application/json"):
        if key in t:
            print("has", key)
    apis = sorted(set(re.findall(r"https?://[a-z0-9.\-]+/(?:api|search|v\d)[^\"'\s<>]{0,80}", t)))[:10]
    if apis:
        print("api-like urls:", *apis, sep="\n  ")


def probe_country_page(url: str) -> None:
    """صفحة اختيار الدولة: نطبع الروابط والنماذج اللي فيها السعودية عشان نعرف كيف تنختار."""
    print(f"\n===== country page =====\n{url}")
    r = requests.get(url, timeout=25, headers={"User-Agent": UA})
    t = r.text
    links = sorted(set(re.findall(r'href="([^"]*(?:sa|saudi|SA)[^"]*)"', t)))[:20]
    print("links:", *links, sep="\n  ")
    for m in re.finditer(r"(?i)(saudi|السعودية|\"SA\"|'SA')", t):
        print("ctx:", re.sub(r"\s+", " ", t[max(0, m.start() - 200):m.start() + 200]))
        break
    print("forms:", re.findall(r"<form[^>]*>", t)[:5])
    print("cookies set:", list(r.cookies.keys()))


def probe_channel(ch: str) -> None:
    """قناة تيليجرام عامة: كم منشور ظاهر، تاريخ آخر منشور، ومثال."""
    print(f"\n===== tg:{ch} =====")
    try:
        r = requests.get(f"https://t.me/s/{ch}", timeout=20, headers={"User-Agent": UA})
    except requests.RequestException as e:
        print("ERROR", type(e).__name__)
        return
    posts = re.findall(r'data-post="[^"]+/(\d+)"', r.text)
    dates = re.findall(r'<time datetime="([^"]+)"', r.text)
    texts = re.findall(r'class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>', r.text, re.S)
    print(f"status={r.status_code} final_url={r.url} posts={len(posts)} last_post={posts[-1] if posts else None} "
          f"last_date={dates[-1] if dates else None}")
    for t in texts[-3:]:
        print("  •", re.sub(r"<[^>]+>|\s+", " ", t)[:160])


def probe_app_sites() -> None:
    """بنية صفحة iGeeksBlog (تطبيقات صارت مجانية) وعناوين RSS دونانم هابر."""
    r = requests.get("https://www.igeeksblog.com/paid-iphone-apps-gone-free/", timeout=30, headers={"User-Agent": UA})
    t = r.text
    print("igeeks", r.status_code, len(t), "modified:", re.findall(r'"dateModified":"([^"]+)"', t)[:2])
    for m in list(re.finditer(r"apps\.apple\.com/[^\"\s]*", t))[:4]:
        print("---\n", re.sub(r"\s+", " ", t[m.start() - 900:m.start() + 150]))
    for h in re.findall(r"<h[23][^>]*>(.*?)</h[23]>", t, re.S)[:25]:
        print("H:", re.sub(r"<[^>]+>|\s+", " ", h).strip()[:120])
    d = requests.get("https://www.donanimhaber.com/rss/tum/", timeout=30, headers={"User-Agent": UA}).text
    print("dh items:", d.count("<item>"))
    for it in re.findall(r"<item>(.*?)</item>", d, re.S)[:6]:
        print("ITEM:", re.sub(r"\s+", " ", it)[:400])


if __name__ == "__main__":
    if "appsites" in (os.environ.get("PROBE_ONLY") or ""):
        probe_app_sites()
        sys.exit()
    if "country" in (os.environ.get("PROBE_ONLY") or ""):
        probe_country_page("https://www.trendyol.com/en/select-country?cb=%2Fen%2Fsr%3Fq%3Diphone")
    for ch in os.environ.get("PROBE_TG", "").split():
        probe_channel(ch)
    only = (os.environ.get("PROBE_ONLY") or " ".join(sys.argv[1:])).split()
    for n, u in STORES.items():
        if os.environ.get("PROBE_TG") and not only:
            break
        if only and not any(n.startswith(o) for o in only):
            continue
        summarize(n, u)
