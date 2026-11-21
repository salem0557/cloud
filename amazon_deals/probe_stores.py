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


if __name__ == "__main__":
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
