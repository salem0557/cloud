"""تجربة: قسم الاشتراكات (In-App Purchases) داخل serialized-server-data في صفحة التطبيق."""
import json
import re

import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"


def walk(o, path=""):
    if isinstance(o, dict):
        yield path, o
        for k, v in o.items():
            yield from walk(v, f"{path}.{k}")
    elif isinstance(o, list):
        for i, v in enumerate(o):
            yield from walk(v, f"{path}[{i}]")


for app_id in ("1451784328",):
    for cc in ("sa", "tr"):
        t = requests.get(f"https://apps.apple.com/{cc}/app/id{app_id}", timeout=25,
                         headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}).text
        m = re.search(r'id="serialized-server-data"[^>]*>(.*?)</script>', t, re.S)
        data = json.loads(m.group(1))
        print(f"\n===== {cc} =====")
        for path, d in walk(data):
            if path.endswith("inAppPurchases") or ("inAppPurchases" in path and isinstance(d.get("items"), list)):
                print("PATH", path, "keys", list(d)[:10])
                print(json.dumps(d, ensure_ascii=False)[:2500])
                break
        top = data["data"][0]["data"]
        print("top keys:", list(top)[:30])
        sm = top.get("shelfMapping") or {}
        print("shelfMapping keys:", list(sm)[:40])
        for k, v in sm.items():
            if "purchase" in k.lower() or "iap" in k.lower():
                print("SHELF", k, json.dumps(v, ensure_ascii=False)[:3000])
        raw = m.group(1)
        for kw in ("In-App", "Purchases", "Premium", "Google One"):
            j = raw.find(kw)
            if j > 0:
                print(f"ctx {kw}:", raw[max(0, j - 200):j + 600].replace("\n", " "))
        print("money-like:", sorted(set(re.findall(r'"([^"]{0,25}\d+[.,]\d{2}[^"]{0,15})"', raw)))[:25])
        # أي نص فيه سعر بالعملة
        prices = sorted(set(re.findall(r'"(?:price|priceFormatted|text|subtitle)":"([^"]{0,40}(?:SAR|ر\.س|TL|₺|TRY)[^"]{0,20})"', m.group(1))))
        print("price strings:", prices[:20])
