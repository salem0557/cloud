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
    box = page.locator("input[type=search], input[placeholder*='بحث'], input[name*=search], input[name=q]")
    print("search inputs:", box.count())
    if box.count():
        box.first.fill(q)
        box.first.press("Enter")
        page.wait_for_timeout(8000)
        print("after search:", page.url, "|", page.title()[:80])
        body = re.sub(r"\s+", " ", page.inner_text("body"))
        prices = re.findall(r"[\d,]+(?:\.\d+)?\s?(?:ر\.س|ريال|SAR)", body)
        print("prices_found:", len(prices), prices[:10])
        print("text:", body[:700])


def main() -> int:
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(locale="ar-SA")
        try:
            mahally_search(page)
        except Exception as e:
            print("mahally ERROR", type(e).__name__, str(e)[:200])
        b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
