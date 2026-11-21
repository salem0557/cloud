"""تجربة: متصفح Chromium عادي يفتح تريندول، ولو طلعت صفحة "اختر دولتك" يضغط على السعودية
مثل أي زائر، وبعدها يطبع وش ظهر (عدد المنتجات وأمثلة أسعار). بدون تزوير كوكيز أو تخفّي."""
import re
import sys

from playwright.sync_api import sync_playwright

URL = "https://www.trendyol.com/ar/sr?q=iphone"


def main() -> int:
    with sync_playwright() as p:
        b = p.chromium.launch()
        page = b.new_page(locale="ar-SA")
        page.goto(URL, wait_until="domcontentloaded", timeout=45000)
        page.wait_for_timeout(3000)
        print("first url:", page.url, "| title:", page.title())
        if "select-country" in page.url:
            body = page.inner_text("body")[:600].replace("\n", " | ")
            print("country page text:", body)
            for label in ["Saudi Arabia", "السعودية", "المملكة العربية السعودية", "Suudi Arabistan"]:
                loc = page.get_by_text(label, exact=False)
                if loc.count():
                    print("clicking:", label)
                    loc.first.click()
                    page.wait_for_timeout(5000)
                    break
            else:
                print("no Saudi option found")
            print("after click url:", page.url, "| title:", page.title())
            if "select-country" in page.url or "/sr" not in page.url:
                page.goto(URL, wait_until="domcontentloaded", timeout=45000)
                page.wait_for_timeout(4000)
                print("reloaded url:", page.url)
        html = page.content()
        cards = page.locator("[class*='product-card'], [class*='p-card'], a[href*='-p-']")
        print("cards:", cards.count(), "| len:", len(html), "| SAR/ر.س:", html.count("SAR") + html.count("ر.س"))
        for t in page.locator("a[href*='-p-']").all_inner_texts()[:5]:
            print("  •", re.sub(r"\s+", " ", t)[:160])
        b.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
