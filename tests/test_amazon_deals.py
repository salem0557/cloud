import json
import os

import pytest

os.environ["DEALS_DRY"] = "1"

from amazon_deals import deals  # noqa: E402


def card(asin, title, price, list_price=None, sponsored=False):
    lp = (f'<span class="a-price a-text-price"><span class="a-offscreen">SAR {list_price}</span></span>'
          if list_price else "")
    sp = '<span class="puis-sponsored-label-text">Sponsored</span>' if sponsored else ""
    return (f'<div data-component-type="s-search-result" data-asin="{asin}">{sp}'
            f'<h2 aria-label="{title}"><span>{title}</span></h2>'
            f'<span class="a-price"><span class="a-offscreen">SAR {price}</span>'
            f'<span aria-hidden="true">x</span></span>{lp}</div>')


PAGE = "<html><body>" + "".join([
    card("B0MAC00001", "Apple 2024 MacBook Pro Laptop with M4 chip, 14-inch", "1,000.00", "7,499.00"),
    card("B0CASE0001", "Hard Shell Case for MacBook Pro 14 inch", "49.00", "199.00"),
    card("B0WATCH001", "Apple Watch Series 10 [GPS 42mm] Smartwatch with Jet Black Aluminium Case with Sport Band",
         "1,499.00"),
    card("B0TV000001", "Samsung 65 Inch Crystal UHD 4K Smart TV", "2,199.00", "2,999.00"),
]) + "</body></html>"


def test_parse_results():
    items = {i["asin"]: i for i in deals.parse_results(PAGE)}
    assert items["B0MAC00001"]["price"] == 1000.0
    assert items["B0MAC00001"]["list_price"] == 7499.0
    assert items["B0WATCH001"]["list_price"] is None
    assert len(items) == 4


def test_macbook_error_alerts():
    it = deals.parse_results(PAGE)[0]
    reasons = deals.evaluate(it, "SAR", [])
    assert any("خطأ تسعير" in r for r in reasons)
    assert any("خصم 87%" in r for r in reasons)


def test_accessory_ignored_but_real_watch_not():
    items = {i["asin"]: i for i in deals.parse_results(PAGE)}
    assert deals.evaluate(items["B0CASE0001"], "SAR", []) == []
    assert not deals.excluded(items["B0WATCH001"]["title"])


def test_normal_discount_no_alert():
    tv = deals.parse_results(PAGE)[3]
    assert deals.evaluate(tv, "SAR", [2299, 2249, 2299]) == []  # نفس سعره المعتاد تقريباً
    assert any(r.startswith("📉") for r in deals.evaluate(tv, "SAR", [2999, 2899, 2999]))  # تخفيض مخفي 27%


def test_history_drop():
    tv = dict(deals.parse_results(PAGE)[3], price=700.0, list_price=None)
    reasons = deals.evaluate(tv, "SAR", [2999, 2899, 2999])
    assert any("نزل" in r for r in reasons)


def test_blocked_page():
    assert deals.is_blocked('<form action="/errors/validateCaptcha">')
    assert not deals.is_blocked(PAGE)


def test_run_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(deals, "STATE_PATH", tmp_path / "s.json")
    monkeypatch.setattr(deals, "fetch", lambda s, u: PAGE)
    monkeypatch.setattr(deals.time, "sleep", lambda x: None)
    sent = []
    monkeypatch.setattr(deals, "send", lambda t, silent=False: sent.append(t) or True)
    monkeypatch.delenv("DEALS_BOT_TOKEN", raising=False)
    monkeypatch.setitem(deals.CONFIG, "amazon_experiment", False)
    monkeypatch.setattr(deals, "active_season", lambda now: None)
    deals.run()
    assert len(sent) == 1 and "MacBook" in sent[0]  # تنبيه وحيد ومو مكرر رغم 6 صفحات
    deals.run()
    assert len(sent) == 1  # ما يكرر نفس التنبيه
    state = json.loads((tmp_path / "s.json").read_text())
    assert state["cursors"]["sa"] == 6  # 3 صفحات أمازون كل تشغيلة


def test_pick_batch_rates():
    state = {}
    b = deals.pick_batch(state, 1000.0)
    markets = [x["market"] for x in b]
    assert markets.count("sa") == 3 and markets.count("almanea") == 3 and "us" not in markets
    assert all(x["_gap"] == (20, 30) for x in b if x["market"] == "sa")
    assert deals.search_url(next(x for x in b if x["market"] == "sa")).startswith("https://www.amazon.sa/-/en/s?k=")
    b2 = deals.pick_batch(state, 1300.0)
    assert [x["market"] for x in b2].count("sa") == 3
    assert b2[0]["q"] != b[0]["q"]  # تناوب على كلمات البحث


def item(title, price, list_price=None):
    return {"asin": "X", "title": title, "price": price, "list_price": list_price, "sponsored": False}


def test_renewed_uses_lower_floor():
    # مجدد iPhone 16 بـ 1100: طبيعي للمجدد (الحد 1600×0.6=960) فما ينبه
    assert deals.evaluate(item("Amazon Renewed (Refurbished) iPhone 16 (128GB)", 1100.0), "SAR", []) == []
    # مجدد MacBook Pro بـ 1000 ريال: أقل من 3000×0.6=1800 → خطأ
    r = deals.evaluate(item("Apple MacBook Pro 14 M3 (Renewed Premium)", 1000.0), "SAR", [])
    assert any("خطأ تسعير" in x for x in r)
    assert deals.evaluate(item("Apple iPhone 16 Pro (256 GB) - Desert Titanium", 999.0), "SAR", [])


def test_renewed_bad_condition_ignored():
    assert deals.excluded("Apple MacBook Pro (Renewed) - Acceptable")
    assert not deals.excluded("Apple MacBook Pro (Renewed) - Excellent")
    assert not deals.excluded("Laptop with focused cooling")


def test_resale_tag_in_alert():
    msg = deals.format_alert(item("Apple MacBook Pro (Renewed Premium)", 1000.0),
                             deals.CONFIG["markets"]["sa"], ["x"])
    assert "♻️" in msg


def test_accessories_do_not_hit_floor():
    for t in ["GameSir G7 SE Wired Controller for Xbox Series X|S, Xbox One",
              "Venom High Capacity 1100mAh Rechargeable Battery Twin Pack - White (Xbox Series X & S/Xbox One)",
              "HyperX Cloud III – Wired Gaming Headset, PC, PS5, Xbox Series X|S",
              "PlayStation PS5 Disc Drive - for Digital Edition Consoles (model group – slim)"]:
        assert deals.floor_hit(t, 150.0, "SAR") is None, t
    assert deals.floor_hit("Xbox Series X 1TB Console", 500.0, "SAR")
    assert deals.floor_hit("PlayStation 5 Console Digital Edition Slim (KSA Version)", 500.0, "SAR")
    assert deals.floor_hit("ASUS ROG Strix G16 Gaming Laptop, 16GB, 1TB SSD, RTX 4060 Laptop GPU", 1500.0, "SAR")


def test_resale_ignores_list_price_discount():
    r = deals.evaluate(item("Lenovo ThinkPad E16 Gen 1 Laptop (Renewed)", 2599.0, 11144.0), "SAR", [])
    assert r == []


def test_welcome_sent_once(tmp_path, monkeypatch):
    monkeypatch.setattr(deals, "STATE_PATH", tmp_path / "s.json")
    monkeypatch.setattr(deals, "fetch", lambda s, u: "<html></html>")
    monkeypatch.setattr(deals.time, "sleep", lambda x: None)
    monkeypatch.setenv("DEALS_BOT_TOKEN", "x")
    sent = []
    monkeypatch.setattr(deals, "send", lambda t, silent=False: sent.append(t) or True)
    deals.run(); deals.run()
    assert sum("👋" in t for t in sent) == 1


def test_stops_on_block(tmp_path, monkeypatch):
    monkeypatch.setattr(deals, "STATE_PATH", tmp_path / "s.json")
    calls = []
    monkeypatch.setattr(deals, "fetch", lambda s, u: calls.append(u) or None)
    monkeypatch.setattr(deals.time, "sleep", lambda x: None)
    monkeypatch.delenv("DEALS_BOT_TOKEN", raising=False)
    deals.run()
    hosts = [u.split("/")[2] for u in calls]
    assert len(hosts) == len(set(hosts))  # بعد أول رفض من موقع ما نرجع له في نفس التشغيلة


def test_whole_word_floor_match():
    # تنبيه غلط حقيقي: شاشة 165Hz انحسبت تلفزيون 65 بوصة
    t = ("GAMEON Epic Series 27 Inch Fast IPS Gaming Monitor, FHD 1080p 165Hz Computer Monitor, "
         "0.5ms, 300 Nits, HDR, HDMI 2.1, Adaptive Sync, Built-in Speakers, Ideal for PS5 & Xbox, Smart TV Box")
    assert deals.floor_hit(t, 429.0, "SAR") is None
    assert deals.floor_hit("Samsung 65 Inch Crystal UHD 4K Smart TV", 500.0, "SAR")
    assert deals.floor_hit("ASUS TUF Gaming GeForce RTX 4070 Super 12GB Graphics Card", 600.0, "SAR")
    assert deals.floor_hit("Samsung Galaxy S25 Ultra 5G, 256GB", 900.0, "SAR")


ALMANEA = ('<html><script id="__NEXT_DATA__" type="application/json">' + json.dumps({"props": {"pageProps": {"hits": [
    {"objectID": "1", "sku": "IP16P", "name": "Apple iPhone 16 Pro 256GB", "url": "en/apple-iphone-16-pro",
     "prices_with_tax": {"price": 999.0, "original_price": 4999.0, "discounted_price": 999.0}},
    {"objectID": "2", "sku": "TV65", "name": ["Samsung 65 Inch Smart TV"], "url_key": "samsung-65",
     "prices_with_tax": {"price": 2599.0, "original_price": 2599.0}},
]}}}) + '</script></html>')


def test_parse_almanea():
    items = {i["asin"]: i for i in deals.parse_almanea(ALMANEA)}
    assert items["IP16P"]["price"] == 999.0 and items["IP16P"]["list_price"] == 4999.0
    assert items["IP16P"]["url"] == "https://www.almanea.sa/en/apple-iphone-16-pro"
    assert items["TV65"]["list_price"] is None and items["TV65"]["url"].endswith("/en/samsung-65")
    r = deals.evaluate(items["IP16P"], "SAR", [])
    assert any("خطأ تسعير" in x for x in r)
    msg = deals.format_alert(items["IP16P"], deals.CONFIG["markets"]["almanea"], r)
    assert "المنيع" in msg and "almanea.sa/en/apple-iphone-16-pro" in msg


def test_model_key_matches_across_stores():
    a = deals.model_key("Apple iPhone 16 Pro Max (256 GB) - Desert Titanium")
    b = deals.model_key("iPhone 16 Pro Max 256GB Natural Titanium")
    assert a == b == "iphone16-promax-256gb"
    assert deals.model_key("Apple iPhone 16 Pro (256 GB)") != a
    assert deals.model_key("Amazon Renewed iPhone 16 Pro Max 256GB") is None
    assert deals.model_key('Apple MacBook Air 13-inch M3 chip 16GB 512GB') == "macbookair-m3-13-512gb"
    assert deals.model_key("PlayStation 5 Console Digital Edition Slim") == "ps5-digital"


def test_cross_store_alert():
    models = {"iphone16-promax-256gb": {"almanea": {"price": 5499.0, "ts": 1000.0}}}
    it = {"title": "Apple iPhone 16 Pro Max (256 GB)", "price": 2999.0}
    r = deals.cross_store_reason(it, "sa", models, 2000.0)
    assert r and "المنيع" in r and "45%" in r
    it["price"] = 5200.0
    assert deals.cross_store_reason(it, "sa", models, 2000.0) is None


CHANNEL = """<div class="tgme_widget_message" data-post="deals/10"><div class="tgme_widget_message_text">كفر جوال</div></div>
<div class="tgme_widget_message" data-post="deals/11"><div class="tgme_widget_message_text">خطأ تسعير! iPhone 16 بـ 999</div></div>"""


def test_channels(monkeypatch):
    monkeypatch.setitem(deals.CONFIG, "telegram_channels", ["deals"])
    class R:
        status_code = 200
        text = CHANNEL
    class S:
        def get(self, url, timeout):
            return R()
    sent = []
    monkeypatch.setattr(deals, "send", lambda t, silent=False: sent.append(t) or True)
    state = {}
    assert deals.scan_channels(state, S()) == 0 and state["tg_seen"]["deals"] == 11  # أول مرة: بس نحفظ المكان
    state["tg_seen"]["deals"] = 9
    assert deals.scan_channels(state, S()) == 1 and "iPhone 16" in sent[0] and "t.me/deals/11" in sent[0]


def test_inflated_list_price_store():
    tv = item("Samsung Smart TV 77 Inch 4K OLED, Tizen OS - QA77S90HAUXSA", 10099.0, 18999.0)
    assert deals.evaluate(tv, "SAR", [], trust_list=False) == []                       # خصم وهمي بدون سجل
    assert deals.evaluate(tv, "SAR", [10099, 10199, 10099], trust_list=False) == []    # نفس سعره المعتاد
    r = deals.evaluate(tv, "SAR", [12999, 12999, 13499], trust_list=False)             # نزل فعلاً 22%
    assert any("خصم" in x for x in r)
    assert any("خصم" in x for x in deals.evaluate(tv, "SAR", []))                      # أمازون: نثق بالسعر المشطوب


def test_experiment_rotates_and_records(tmp_path, monkeypatch):
    monkeypatch.setattr(deals, "STATE_PATH", tmp_path / "s.json")
    monkeypatch.setattr(deals.time, "sleep", lambda x: None)
    monkeypatch.delenv("DEALS_BOT_TOKEN", raising=False)
    monkeypatch.setitem(deals.CONFIG, "amazon_experiment", True)
    monkeypatch.setitem(deals.CONFIG, "telegram_channels", [])
    urls = []
    monkeypatch.setattr(deals, "fetch", lambda s, u: urls.append(u) or None)
    for _ in range(len(deals.EXPERIMENT)):
        deals.run()
    st = json.loads((tmp_path / "s.json").read_text())["exp"]["stats"]
    assert set(st) == set(deals.EXPERIMENT) and all(v["blocked"] == 1 for v in st.values())
    assert any("/-/en/s?k=" in u for u in urls) and any("bestsellers" in u for u in urls)


def test_new_categories_floors():
    assert deals.floor_hit("Gree 18000 BTU Split Air Conditioner, Inverter, Cold Only", 650.0, "SAR")
    assert deals.floor_hit("Replacement Remote Control for Split Air Conditioner", 50.0, "SAR") is None
    assert deals.floor_hit("AMD Ryzen 7 7800X3D 8-Core Desktop Processor", 300.0, "SAR")
    assert deals.floor_hit("Thermalright CPU Cooler for AMD Ryzen 7 / Intel Core i7", 120.0, "SAR") is None


def test_coupon_makes_a_deal():
    assert deals.parse_coupon("Save 20% with coupon", 1000.0) == 200.0
    assert deals.parse_coupon("Save SAR 150.00 with coupon", 1000.0) == 150.0
    assert deals.parse_coupon("وفر 15% مع الكوبون", 1000.0) == 150.0
    assert deals.parse_coupon("Free delivery", 1000.0) is None
    page = card("B0TVCPN001", "Samsung 65 Inch Crystal UHD 4K Smart TV", "1,999.00", "3,199.00")
    page = page.replace("</div>", '<span class="s-coupon-unclipped">Save 25% with coupon</span></div>')
    it = deals.parse_results("<html>" + page + "</html>")[0]
    assert it["coupon"] == round(1999 * 0.25, 2)
    # بدون كوبون: خصم 37% (أقل من 45) — مع الكوبون يصير 53% → تنبيه
    assert deals.evaluate(it, "SAR", []) == []
    ev = dict(it, price=it["price"] - it["coupon"])
    assert any("خصم" in r for r in deals.evaluate(ev, "SAR", []))
    msg = deals.format_alert(it, deals.CONFIG["markets"]["sa"], ["x"])
    assert "🎟️" in msg and "بعد الكوبون" in msg


def test_seasons_and_priority(monkeypatch):
    from datetime import datetime, timezone
    ts = lambda y, m, d: datetime(y, m, d, 12, tzinfo=timezone.utc).timestamp()
    assert deals.active_season(ts(2026, 11, 21))["name"].startswith("الجمعة البيضاء")
    assert deals.active_season(ts(2027, 2, 20))["name"] == "رمضان 2027" or deals.active_season(ts(2027, 2, 20))
    assert deals.active_season(ts(2026, 10, 5)) is None
    b = deals.pick_batch({}, ts(2026, 11, 21))
    assert [x["market"] for x in b].count("almanea") == 5 and "us" not in [x["market"] for x in b]
    msg = deals.format_alert(item("Apple iPhone 16 Pro 256GB", 999.0), deals.CONFIG["markets"]["sa"], ["x"],
                             deals.active_season(ts(2026, 11, 21)))
    assert "🗓️ موسم" in msg


def test_services_excluded():
    assert deals.excluded("Split Air Conditioner Installation - 1 Ton")
    assert deals.excluded("AppleCare+ for MacBook Pro 14-inch")
    assert deals.excluded("خدمة تركيب مكيف سبليت")
    assert deals.evaluate(item("Split Air Conditioner Installation - 1 Ton", 220.0), "SAR", []) == []
    assert not deals.excluded("Gree 18000 BTU Split Air Conditioner, Inverter, Cold Only")
    assert not deals.excluded("LG Window Air Conditioner 18000 BTU, Easy to Install, Cold Only")
    assert not deals.excluded("مكيف سبليت جري 18000 وحدة سهل التركيب")


def test_ps5_faceplates_not_a_console():
    t = ("Newentor Disc Edition Face Plates with Cooling Vents Cover Skins Shell Panels for PS5 Slim Cover Console, "
         "Playstation 5 Slim Covers Protective Shell")
    assert deals.model_key(t) is None
    assert deals.evaluate(item(t, 69.0), "SAR", []) == []
    models = {"ps5-disc": {"almanea": {"price": 2749.0, "ts": 1000.0}}}
    assert deals.cross_store_reason({"title": "PlayStation 5 Console Slim Disc", "price": 300.0}, "sa", models, 2000.0) is None
    assert deals.model_key("Sony PlayStation 5 Slim Console Disc Edition (KSA Version)") == "ps5-disc"
    assert deals.model_key("Apple Watch Series 10 GPS 42mm Aluminium Case with Sport Band") is None
    assert deals.model_key("Apple AirPods Pro 2 Wireless Earbuds with MagSafe Charging Case (USB-C)") == "airpods-pro2"
    assert deals.model_key("PlayStation 5 Console Slim Disc with DualSense Wireless Controller") == "ps5-disc"
    assert deals.model_key("Silicone Case for Apple iPhone 16 Pro Max 256GB") is None


def test_real_devices_not_dropped():
    keep = [
        "Samsung 65 Inch Crystal UHD 4K Smart TV, Black, 2 Years Warranty",
        "Gree Pular 18000 BTU Split Air Conditioner, Inverter, Cold Only, 10 Years Compressor Warranty",
        "Apple iPhone 16 Pro Max (256 GB) - Desert Titanium with AppleCare eligible",
        "Sony PlayStation 5 Console Slim Disc Edition with DualSense Controller (KSA Version)",
        "Apple AirPods Pro 2 Wireless Earbuds with MagSafe Charging Case (USB-C)",
        "Apple Watch Ultra 2 GPS + Cellular 49mm Black Titanium Case with Black Ocean Band",
        "ASUS ROG Strix G16 Gaming Laptop, RTX 4060, 16GB, 1TB SSD, Backlit Keyboard",
    ]
    for t in keep:
        assert not deals.excluded(t), t


FOOD = """<div class="tgme_widget_message" data-post="deals/20"><div class="tgme_widget_message_text">ببلاش
* عرض زبدة غير مملحة بـ 1.00 ريال https://3rrood.com/bvPCrtxH
* عرض كرواسون شوكولاتة https://3rrood.com/abTVqq</div></div>
<div class="tgme_widget_message" data-post="deals/21"><div class="tgme_widget_message_text">شاشة سامسونج 55 بوصة TV بخصم 40% https://ty.gl/x1</div></div>
<div class="tgme_widget_message" data-post="deals/22"><div class="tgme_widget_message_text">خصم قوي على المكيفات اليوم https://ty.gl/x2</div></div>"""


def test_channel_ignores_keywords_inside_links(monkeypatch):
    monkeypatch.setitem(deals.CONFIG, "telegram_channels", ["deals"])
    class R:
        status_code = 200
        text = FOOD
    class S:
        def get(self, url, timeout):
            return R()
    sent = []
    monkeypatch.setattr(deals, "send", lambda t, silent=False: sent.append(t) or True)
    state = {"tg_seen": {"deals": 19}}
    assert deals.scan_channels(state, S()) == 2 and "سامسونج" in sent[0] and "المكيفات" in sent[1]


TY = """<div class="tgme_widget_message" data-post="d/30"><div class="tgme_widget_message_text">طقم حقائب سفر خصم 20% https://ty.gl/aa</div></div>
<div class="tgme_widget_message" data-post="d/31"><div class="tgme_widget_message_text">جاكيت شتوي من تريندول بـ 199 ريال (بدل 699 ريال - خصم 72%) https://ty.gl/bb</div></div>
<div class="tgme_widget_message" data-post="d/32"><div class="tgme_widget_message_text">✨<b>معطر</b> <b>ال</b><b>جسم</b> من <b>جيس</b> بـ 28.45 ريال (بدل 58 ريال - خصم 51%).<br>كود: X https://ty.gl/cc</div></div>
<div class="tgme_widget_message" data-post="d/33"><div class="tgme_widget_message_text">خطأ تسعير! كنبة بـ 99 https://ty.gl/dd</div></div>"""


def test_trendyol_strong_deals_any_category(monkeypatch):
    monkeypatch.setitem(deals.CONFIG, "telegram_channels", ["d"])
    monkeypatch.setitem(deals.CONFIG, "trendyol_any_category", True)
    class R:
        status_code = 200
        text = TY
    class S:
        def get(self, url, timeout):
            return R()
    sent = []
    monkeypatch.setattr(deals, "send", lambda t, silent=False: sent.append(t) or True)
    assert deals.scan_channels({"tg_seen": {"d": 29}}, S()) == 2
    assert "72%" in sent[0] and "🛍️ تريندول" in sent[0] and "خطأ تسعير" in sent[1]


def test_channel_text_keeps_words_on_one_line():
    posts = deals.parse_channel(TY)
    perfume = dict(posts)[32]
    assert perfume.startswith("✨معطر الجسم من جيس بـ 28.45 ريال")
    assert perfume.count("\n") == 1


MAHALLY = json.dumps([
    ["/ar/products/1633394969/1588772393/?q=1", "ايفون 15 عادي 128 جيجا مستعمل مضمون متجر وجهتك الاولى 5.00 (3) ادفع بالتقسيط 2357.5 -15% 1999"],
    ["/ar/products/1444699754/215132508/", "ايفون 17 برو ماكس 256 جيجا ميكانيزم الكترونيات 4.93 (14) 1411"],
    ["/ar/products/784538497/93268365/", "ايفون 16 عادي 128 قيقا الخليفي للاتصالات 5.00 (15) ادفع بالتقسيط 3099"],
], ensure_ascii=False)


def test_parse_mahally_and_arabic_rules():
    items = {i["asin"]: i for i in deals.parse_mahally(MAHALLY)}
    used = items["1588772393"]
    assert used["price"] == 1999 and used["list_price"] == 2357.5 and deals.is_resale(used["title"])
    assert used["url"] == "https://mahally.com/ar/products/1633394969/1588772393/"
    pm = items["215132508"]
    assert deals.model_key(pm["title"]) == "iphone17-promax-256gb"
    assert any("خطأ تسعير" in r for r in deals.evaluate(pm, "SAR", [], trust_list=False))
    assert deals.evaluate(items["93268365"], "SAR", [], trust_list=False) == []


@pytest.fixture(autouse=True)
def _no_real_browser(monkeypatch):
    monkeypatch.setattr(deals, "fetch_browser", lambda url: None)


def test_knockoff_phones_ignored():
    for t, price in [("شبيه ايفون 17 برو ماكس بلاك فون", 289.0),
                     ("جوال OALE 17 Pro Max – ذاكرة 128GB – متوافق كلياً مع كفرات آيفون 17 برو ماكس عجيب غريب .كوم", 399.0),
                     ("ايفون 17 برو ماكس نسخة هاي كوبي 256 جيجا", 450.0)]:
        assert deals.excluded(t), t
        assert deals.evaluate(item(t, price), "SAR", [], trust_list=False) == [], t
    assert not deals.excluded("مكيف سبليت جري 18000 وحدة متوافق مع الواي فاي")
    assert not deals.excluded("Samsung 65 Inch Smart TV, Compatible with Alexa")
    real = item("ابل - ايفون 17 برو ماكس، بسعة 256 جيجا، 5 جي، 6.9 بوصة، ازرق المخازن السوداء", 1500.0)
    assert any("خطأ تسعير" in r for r in deals.evaluate(real, "SAR", [], trust_list=False))
    msg = deals.format_alert(real, deals.CONFIG["markets"]["mahally"], ["x"])
    assert "🇸🇦" in msg and "🇺🇸" not in msg


MAHALLY_SERVICES = [
    "خدمة فحص ماك بوك برو MacBook Pro kiwik",
    "تغيير بطارية ماك بوك اير | MacBook Air كيوتك ريبير | Q-TECH Repair",
    "استبدال شاشة ماك بوك برو A1706 - A1708 أصلية مؤسسة مؤشر التقنية - مركز صيانة الحاسب",
    "خدمة تنظيف ماك بوك برو MacBook Pro kiwik",
    "سوفت وير ماك بوك برو | Macbook Pro كيوتك ريبير | Q-TECH Repair",
    "بطارية ماك بوك برو A1425 مع التركيب وضمان 6 أشهر مؤسسة مؤشر التقنية",
    "شاحن ماك بوك برو 60W 2012 ابل 13 انش 60W MAGSAFE POWER ADAPTER ايزون | iZONE",
    "ترقية سعة ماك بوك برو | MacBook Pro كيوتك ريبير | Q-TECH Repair",
    "محول USB C الى HDMI 4K 30Hz متوافق مع ايباد 10/برو 6/اير 5/ميني 6، ماك بوك برو، سامسونج S23 الترا",
    "اصلاح مذربورد ماك بوك برو A2141 مؤسسة مؤشر التقنية - مركز صيانة الحاسب",
    "تغيير تتش بار ماك بوك برو MacBook Pro A2338 kiwik",
]


def test_mahally_services_and_parts_ignored():
    for t in MAHALLY_SERVICES:
        assert deals.evaluate(item(t, 99.0), "SAR", [], trust_list=False) == [], t
    # ماك بوك قديم (i5) بسعر طبيعي: مو خطأ تسعير
    assert deals.evaluate(item("لابتوب ماك بوك اير اي فايف همسات الابداع", 1250.0), "SAR", [], trust_list=False) == []
    # ماك بوك حديث بسعر غلط: ينبه
    r = deals.evaluate(item("Apple MacBook Air 13-inch M3 chip 16GB 512GB", 1250.0), "SAR", [])
    assert any("خطأ تسعير" in x for x in r)
    r = deals.evaluate(item("ماك بوك برو M4 Pro 14 انش 512 جيجا", 1999.0), "SAR", [], trust_list=False)
    assert any("خطأ تسعير" in x for x in r)


def test_ipad_accessories_and_repairs_ignored():
    for t, price in [("ماجيك كيبورد ايباد برو و اير و A16 و الجيل العاشر - أسود بريم بوكس PREMBOX", 379.0),
                     ("Lujain pencil pro| قلم ايباد برو lujain_pencil", 135.0),
                     ("تغيير شاشة ايباد برو | iPad Pro كيوتك ريبير | Q-TECH Repair", 1100.0)]:
        assert deals.evaluate(item(t, price), "SAR", [], trust_list=False) == [], t
    assert any("خطأ تسعير" in r for r in deals.evaluate(item("Apple iPad Pro 11-inch M4 256GB Wi-Fi", 900.0), "SAR", []))


def test_tv_screen_protectors_ignored():
    for t, price in [("واقي شاشة تلفزيون 65 بوصة مقاوم للخدوش والصدمات متجر قوة الشاشة لحماية شاشة التلفزيون", 278.0),
                     ("واقي شاشة تلفزيون مسطح 65 بوصة سمارت كرييشنز - أكريليك شفاف 3 مم", 392.14),
                     ("تعليقة جدارية لشاشة تلفزيون 65 بوصة", 120.0)]:
        assert deals.evaluate(item(t, price), "SAR", [], trust_list=False) == [], t
    assert any("خطأ تسعير" in r for r in deals.evaluate(item("شاشة سامسونج 65 بوصة سمارت 4K", 700.0), "SAR", [], trust_list=False))


def test_mahally_brand_first_gate():
    junk = ["موجه هواء مكيف سبليت كلاسيك من ويتفورمز | Witforms Classic ويتفورمز",
            "ريموت مكيف سبليت سامسونج أصلي تحكم عن بعد موديل DB93-11489C مقشة",
            "واقي شاشة تلفزيون 65 بوصة مقاوم للخدوش"]
    for t in junk:
        assert deals.evaluate(item(t, 90.0), "SAR", [], trust_list=False, brand_first=True) == [], t
    real = ["ابل - ايفون 17 برو ماكس، بسعة 256 جيجا، 5 جي",
            "ايفون 17 برو ماكس 256 جيجا ميكانيزم الكترونيات",
            "مكيف سبليت جري 18000 وحدة انفرتر",
            "سامسونج جالكسي S25 الترا 256 جيجا",
            "كاش ابل ايفون 17 برو ماكس ،6.9 بوصة، 256 جيجا"]
    for t in real:
        assert deals.starts_with_brand(t), t
    assert any("خطأ تسعير" in r for r in deals.evaluate(item(real[0], 900.0), "SAR", [], trust_list=False, brand_first=True))


def test_brand_gate_tolerates_typos_and_promo_words():
    for t in ["ايفوون 17 برو ماكس 256 جيجا", "سامسنج جالكسي S25 الترا", "أبل ايفون 16 برو",
              "🔥عرض خاص ايفون 17 برو ماكس 256 جيجا", "جوال سامسونج جالكسي S24", "إيفون ١٧ برو"]:
        assert deals.starts_with_brand(t), t
    for t in ["موجه هواء مكيف سبليت", "ريموت مكيف سبليت سامسونج", "واقي شاشة تلفزيون 65 بوصة"]:
        assert not deals.starts_with_brand(t), t


def test_brand_gate_safety_net():
    # ما يبدأ بماركة لكن السعر قريب من سعر الجهاز: تنبيه "تأكد" (مو 🚨)
    r = deals.evaluate(item("للبيع ايفون 17 برو ماكس 256 جيجا", 1500.0), "SAR", [], trust_list=False, brand_first=True)
    assert r and r[0].startswith("⚠️") and not any(x.startswith("🚨") for x in r)
    # إكسسوار رخيص جداً: ولا شي
    assert deals.evaluate(item("موجه هواء مكيف سبليت", 90.0), "SAR", [], trust_list=False, brand_first=True) == []


def test_knockoff_misspelled():
    t = "شبية ساعة ابل الترا من شركة انتك الالمانيه بضمان سنتين بلو ستور"
    assert deals.excluded(t)
    assert deals.evaluate(item(t, 159.0), "SAR", [], trust_list=False, brand_first=True) == []
    assert deals.excluded("ايفون 17 نسخة طبق الاصل")
    assert deals.excluded("إصلاح شاشة ايفون")


def test_copycat_titles_too_cheap_on_small_stores():
    for t in ["ايربودز ماكس اسود MOMENT", "ايربودز ماكس ابيض MOMENT"]:
        assert deals.evaluate(item(t, 96.0), "SAR", [], trust_list=False, brand_first=True) == [], t
    # نفس الفكرة من تاجر ثاني: أرخص من 20% من سعر الجهاز → ما ينبه
    assert deals.evaluate(item("ايربودز ماكس فضي متجر النخبة", 150.0), "SAR", [], trust_list=False, brand_first=True) == []
    # خطأ تسعير معقول (40% من الحد) → ينبه
    assert any("خطأ تسعير" in r for r in
               deals.evaluate(item("ايربودز ماكس فضي متجر النخبة", 450.0), "SAR", [], trust_list=False, brand_first=True))
    # أمازون ما يتأثر: ماك بوك M4 بـ 500 (17%) يبقى ينبه
    assert any("خطأ تسعير" in r for r in deals.evaluate(item("Apple MacBook Pro 14 M4 chip 512GB", 500.0), "SAR", []))


def test_market_verified_deals():
    now = 2000.0
    models = {"iphone16-promax-256gb": {"sa": {"price": 4999.0, "ts": 1000.0}, "almanea": {"price": 5199.0, "ts": 1000.0}}}
    # مستعمل في محلي بـ 2,900 (أرخص 42% من الجديد) → ♻️
    used = {"title": "ايفون 16 برو ماكس 256 جيجا مستعمل نظيف", "price": 2900.0, "list_price": None}
    r = deals.market_deal_reasons(used, 2900.0, "mahally", models, now)
    assert r and r[0].startswith("♻️") and "42%" in r[0]
    # مستعمل بسعر عادي (أرخص 15%) → ولا شي
    assert deals.market_deal_reasons(dict(used, price=4250.0), 4250.0, "mahally", models, now) == []
    # جديد بخصم 30% وأرخص 15% من أمازون → 🔥 عرض مؤكد
    new = {"title": "ابل ايفون 16 برو ماكس 256 جيجا", "price": 4249.0, "list_price": 6099.0}
    r = deals.market_deal_reasons(new, 4249.0, "mahally", models, now)
    assert r and r[0].startswith("🔥") and "عرض مؤكد" in r[0]
    # خصم كبير بس السعر نفس السوق → ولا شي (الخصم وهمي)
    assert deals.market_deal_reasons(dict(new, price=4950.0, list_price=8999.0), 4950.0, "mahally", models, now) == []


def test_accessories_with_arabic_prefixes_and_bundles():
    t1 = ("Peslv Privacy Screen Compatible With MacBook Pro 14 Inch (2021-2023, M1, M2, M3)-A2918 A2992 A2442 A2779, "
          "Magnetic Removable Matte Anti Blue Light Gla")
    assert deals.evaluate(item(t1, 159.04), "SAR", []) == []
    t2 = "بكج ساعة وسماعة من موج ماكس , ساعة الترا وسماعة ايربودز برو فون زون"
    assert deals.evaluate(item(t2, 145.0), "SAR", [], trust_list=False, brand_first=True) == []
    t3 = "علبة فردية ايربودز برو الجيل الثاني اصلية ابل ibznss Store متجر اي بزنس"
    assert deals.evaluate(item(t3, 250.0), "SAR", [], trust_list=False, brand_first=True) == []
    assert deals._has_word("كفر وسماعة للايفون", ["سماعة"]) and deals._has_word("حامل بالكفر", ["كفر"])
    # أجهزة حقيقية ما تتأثر
    assert any("خطأ تسعير" in r for r in deals.evaluate(item("Apple MacBook Pro 14-inch M4 chip, 512GB, Space Black", 1500.0), "SAR", []))
    assert any("خطأ تسعير" in r for r in
               deals.evaluate(item("ابل ايربودز برو 2 مع علبة شحن MagSafe", 200.0), "SAR", [], trust_list=False, brand_first=True))


def test_trendyol_non_electronics_off_by_default(monkeypatch):
    monkeypatch.setitem(deals.CONFIG, "telegram_channels", ["d"])
    post = ('<div class="tgme_widget_message" data-post="d/40"><div class="tgme_widget_message_text">'
            '🫖 ترمس شاي وقهوة بتصميم شجرة نخيل من أسيب (57.93 ريال بدل 273.19 ريال - خصم 81%) https://ty.gl/2q</div></div>'
            '<div class="tgme_widget_message" data-post="d/41"><div class="tgme_widget_message_text">'
            'سماعة سامسونج Galaxy Buds من تريندول بخصم 60% https://ty.gl/3q</div></div>')
    class R:
        status_code = 200
        text = post
    class S:
        def get(self, url, timeout):
            return R()
    sent = []
    monkeypatch.setattr(deals, "send", lambda t, silent=False: sent.append(t) or True)
    assert deals.scan_channels({"tg_seen": {"d": 39}}, S()) == 1 and "Galaxy" in sent[0]


D4D = ('<script type="application/ld+json">' + json.dumps({"@graph": [{"@type": "ItemList", "itemListElement": [
    {"@type": "ListItem", "item": {"@type": "SaleEvent", "name": "eXtra", "description": "eXtra White Friday",
                                   "url": "https://d4donline.com/x/1", "endDate": "2026-11-30T20:59:00+00:00"}},
    {"@type": "ListItem", "item": {"@type": "SaleEvent", "name": "Tamimi Markets", "description": "groceries",
                                   "url": "https://d4donline.com/x/2"}}]}]}) + '</script>')


def test_flyers(monkeypatch):
    monkeypatch.setitem(deals.CONFIG, "flyers", dict(deals.CONFIG["flyers"], url="https://d4donline.com/x"))
    class R:
        status_code = 200
        text = D4D
    class S:
        def get(self, url, timeout, headers=None):
            return R()
    sent = []
    monkeypatch.setattr(deals, "send", lambda t, silent=False: sent.append(t) or True)
    state = {}
    assert deals.scan_flyers(state, S()) == 0            # أول مرة: نحفظ الموجود
    state["flyers_seen"].pop("https://d4donline.com/x/1")
    assert deals.scan_flyers(state, S()) == 1 and "eXtra" in sent[0] and "2026-11-30" in sent[0]


AD_PREFIX = "Sponsored Sponsored You\u2019re seeing this ad based on the product\u2019s relevance to your search query. Let us know "


def test_amazon_sponsored_ac_kits():
    titles = ["4PCS Portable Air Conditioner Window Kit with Seal", "Air Conditioner Window Kit Waterproof UV",
              "Window Air Conditioner Side Panels with Foam", "POENVFPO Upgraded Portable Air Conditioner Window Kit"]
    page = "<html>" + "".join(card(f"B0AD{i:06d}", AD_PREFIX + t, "132.64") for i, t in enumerate(titles)) + "</html>"
    items = deals.parse_results(page)
    assert len(items) == 4
    for it in items:
        assert not it["title"].lower().startswith("sponsored"), it["title"]
        assert it["sponsored"]
        assert deals.evaluate(it, "SAR", []) == [], it["title"]
    # حتى بدون علامة الإعلان: Window Kit = إكسسوار
    assert deals.floor_hit("Portable Air Conditioner Window Kit AC Window Seal", 130.0, "SAR") is None
    assert deals.floor_hit("LG Window Air Conditioner 18000 BTU Cold Only", 400.0, "SAR")


def test_gaming_monitor_not_console():
    t = ("GAMEON Epic Series 24 Inch Fast IPS Gaming Monitor, FHD 1080p 165Hz Computer Monitor, 0.5ms MPRT, HDR400, "
         "HDMI 2.1, Adaptive Sync, Built-in Speakers, Ideal for PS5 Console & Xbox Series X")
    assert deals.floor_hit(t, 349.0, "SAR") is None
    assert deals.floor_hit("Sony PlayStation 5 Slim Console Disc Edition", 500.0, "SAR")
    assert deals.floor_hit("Xbox Series X 1TB Console", 500.0, "SAR")


def test_console_games_not_consoles():
    for t in ["Assassin\u2019s Creed Black Flag Resynced - Launch Edition - [Xbox Series X] 9 July 2026",
              "EA SPORTS FC 26 Standard Edition (PS5) Console Exclusive Bonus",
              "Call of Duty Black Ops 7 - Xbox Series X"]:
        assert deals.floor_hit(t, 269.33, "SAR") is None, t
    assert deals.floor_hit("Xbox Series X 1TB Console", 500.0, "SAR")
    assert deals.floor_hit("Microsoft Xbox Series X Console 1TB Digital Edition", 500.0, "SAR")
    assert deals.floor_hit("Sony PlayStation 5 Slim Console Digital Edition", 500.0, "SAR")
