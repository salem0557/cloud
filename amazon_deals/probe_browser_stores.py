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


def main() -> int:
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(locale="en-US")
        for n, u in PAGES.items():
            probe(page, n, u)
        b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
