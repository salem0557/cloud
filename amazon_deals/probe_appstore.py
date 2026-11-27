"""تجربة: بيانات App Store العامة — بحث iTunes (السعر) + صفحة التطبيق (أسعار الاشتراكات داخل التطبيق) في السعودية وتركيا."""
import json
import re

import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"

for term in ["google one", "youtube"]:
    r = requests.get("https://itunes.apple.com/search", params={"term": term, "country": "sa", "entity": "software", "limit": 1},
                     timeout=20).json()
    if not r.get("results"):
        print(term, "no results", r)
        continue
    a = r["results"][0]
    print(f"\n===== {a['trackName']} id={a['trackId']} price={a.get('formattedPrice')} =====")
    for cc in ("sa", "tr"):
        u = f"https://apps.apple.com/{cc}/app/id{a['trackId']}"
        p = requests.get(u, timeout=25, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
        t = p.text
        print(f"[{cc}] status={p.status_code} len={len(t)} url={p.url}")
        i = max(t.find("In-App Purchases"), t.find("In‑App Purchases"))
        if i > 0:
            print("  IAP section:", re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " | ", t[i:i + 2500]))[:1200])
        for key in ("serialized-server-data", "shoebox", "application/json", "inAppPurchases", "\"offers\""):
            if key in t:
                j = t.find(key)
                print(f"  has {key}: ...{re.sub(chr(10), ' ', t[j:j + 300])}")
