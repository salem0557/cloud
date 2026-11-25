"""تجربة: متصفح Chromium عادي (بهويته الحقيقية، بدون تخفّي) يفتح صفحات بحث متاجر،
ينتظر الصفحة تكمل تحميل، ويطبع هل ظهرت منتجات وأسعار أو صفحة حظر."""
import re
import sys

from playwright.sync_api import sync_playwright

PAGES = {
    "jarir": "https://www.jarir.com/sa-en/catalogsearch/result?search=iphone",
    "mahally": "https://mahally.com/ar/search?q=iphone",
}


def probe(page, name: str, url: str) -> None:
    print(f"\n===== {name} =====")
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(8000)  # وقت للجافاسكربت يحمّل المنتجات
    except Exception as e:
        print("ERROR", type(e).__name__, str(e)[:200])
        return
    html = page.content()
    title = page.title()
    blocked = bool(re.search(r"Attention Required|Just a moment|التحقق البشري|captcha", title + html[:5000], re.I))
    prices = re.findall(r"(?:SAR|SR|ر\.س)\s?[\d,]+(?:\.\d+)?|[\d,]+(?:\.\d+)?\s?(?:SAR|SR|ر\.س)", page.inner_text("body"))
    print(f"url={page.url} | title={title[:80]} | blocked={blocked} | len={len(html)} | prices_found={len(prices)}")
    print("sample prices:", prices[:8])
    body = re.sub(r"\s+", " ", page.inner_text("body"))
    i = body.lower().find("iphone")
    print("text sample:", body[max(0, i - 100):i + 400] if i >= 0 else body[:400])


def mahally_search(page, q: str = "ايفون") -> None:
    """محلي: نفتح الرئيسية ونكتب في مربع البحث مثل أي زائر، ونشوف وين يودينا ووش يطلع."""
    print("\n===== mahally (search box) =====")
    page.goto("https://mahally.com/ar/", wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(5000)
    print("home:", page.url, "|", page.title()[:80])
    hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))")
    print("sample links:", [h for h in hrefs if h and ("search" in h or "product" in h or "/p/" in h)][:12])
    # بطاقات المنتجات في الرئيسية: نشوف شكل النص والسعر
    cards = page.locator("a[href*='/products/']")
    print("product links on home:", cards.count())
    for t in cards.all_inner_texts()[:6]:
        print("  card:", re.sub(r"\s+", " ", t)[:160])
    # 1) صفحة منتج: كيف مكتوب الاسم والسعر (JSON-LD أو نص)
    href = page.eval_on_selector("a[href*='/products/']", "e => e.href")
    page.goto(href, wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(5000)
    print("product:", page.url, "|", page.title()[:100])
    ld = page.eval_on_selector_all("script[type='application/ld+json']", "els => els.map(e => e.textContent)")
    print("ld+json:", [x[:400] for x in ld][:2])
    body = re.sub(r"\s+", " ", page.inner_text("body"))
    print("product text:", body[:500])
    # 2) أشكال روابط بحث شائعة
    for u in ["https://mahally.com/ar/search?query=%D8%A7%D9%8A%D9%81%D9%88%D9%86",
              "https://mahally.com/ar/search/?query=%D8%A7%D9%8A%D9%81%D9%88%D9%86",
              "https://mahally.com/ar/products?query=%D8%A7%D9%8A%D9%81%D9%88%D9%86",
              "https://mahally.com/ar/products/?query=%D8%A7%D9%8A%D9%81%D9%88%D9%86"]:
        page.goto(u, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(5000)
        n = page.locator("a[href*='/products/']").count()
        print(f"try {u[-40:]} -> {page.title()[:50]} | product links={n}")
    # 3) نكتب في مربع البحث مباشرة (حتى لو فيه نافذة فوقه)
    page.goto("https://mahally.com/ar/", wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(4000)
    page.keyboard.press("Escape")
    box = page.locator("input[type=search]:visible, input[placeholder*='بحث']:visible, input[name=q]:visible")
    if box.count():
        box.first.fill(q, force=True, timeout=10000)
        box.first.press("Enter")
        page.wait_for_timeout(8000)
        print("after search:", page.url, "|", page.title()[:80],
              "| product links:", page.locator("a[href*='/products/']").count())
    # بطاقات نتائج البحث: نص البطاقة كاملة (الاسم + السعر + السعر قبل الخصم)
    page.goto("https://mahally.com/ar/browse/?query=%D8%A7%D9%8A%D9%81%D9%88%D9%86", wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(7000)
    cards = page.eval_on_selector_all("a[href*='/products/']", """els => els.slice(0, 40).map(a => {
        let n = a; for (let i = 0; i < 4 && n.parentElement; i++) { n = n.parentElement; if (n.innerText.length > 30) break; }
        return [a.getAttribute('href').split('?')[0], n.innerText.replace(/\\s+/g, ' ').slice(0, 200)];
    })""")
    seen = set()
    for href, txt in cards:
        if href in seen:
            continue
        seen.add(href)
        print("  result:", href, "|", txt)
        if len(seen) >= 8:
            break
    html = page.content()
    print("has __NEXT_DATA__:", "__NEXT_DATA__" in html, "| algolia:", "algolia" in html.lower())


def desertcart(page) -> None:
    """ديزرت كارت: الأسعار تنحمّل بالجافاسكربت. نطبع شكل روابط المنتجات ونص البطاقات."""
    print("\n===== desertcart =====")
    page.goto("https://www.desertcart.com.sa/search?query=rtx%204070", wait_until="domcontentloaded", timeout=45000)
    page.wait_for_timeout(9000)
    print("url:", page.url, "| title:", page.title()[:80])
    hrefs = page.eval_on_selector_all("a[href]", "els => els.map(e => e.getAttribute('href'))")
    prod = [h for h in hrefs if h and ("/products/" in h or "/p/" in h)]
    print("product-like links:", len(prod), prod[:5])
    sel = "a[href*='/products/']" if any("/products/" in h for h in prod) else "a[href*='/p/']"
    cards = page.eval_on_selector_all(sel, """els => els.slice(0, 30).map(a => {
        let n = a; for (let i = 0; i < 5 && n.parentElement; i++) { n = n.parentElement; if (/SAR|ر\\.س|﷼/.test(n.innerText) && n.innerText.length > 40) break; }
        return [a.getAttribute('href').split('?')[0], n.innerText.replace(/\\s+/g, ' ').slice(0, 220)];
    })""")
    seen = set()
    for h, t in cards:
        if h in seen:
            continue
        seen.add(h)
        print("  card:", h, "|", t)
        if len(seen) >= 8:
            break
    body = re.sub(r"\s+", " ", page.inner_text("body"))
    print("SAR count:", body.count("SAR"), "| sample:", body[:300])


def main() -> int:
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(locale="ar-SA")
        try:
            desertcart(page)
        except Exception as e:
            print("desertcart ERROR", type(e).__name__, str(e)[:200])
        b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
