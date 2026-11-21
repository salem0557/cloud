import json
import os

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
    assert deals.evaluate(tv, "SAR", [2999, 2899, 2999]) == []


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
    monkeypatch.setattr(deals, "send", lambda t: sent.append(t) or True)
    monkeypatch.delenv("DEALS_BOT_TOKEN", raising=False)
    deals.run()
    assert len(sent) == 1 and "MacBook" in sent[0]  # تنبيه وحيد ومو مكرر رغم 6 صفحات
    deals.run()
    assert len(sent) == 1  # ما يكرر نفس التنبيه
    state = json.loads((tmp_path / "s.json").read_text())
    assert state["cursor"] == 12 % len(deals.CONFIG["searches"])


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
    monkeypatch.setattr(deals, "send", lambda t: sent.append(t) or True)
    deals.run(); deals.run()
    assert sum("👋" in t for t in sent) == 1


def test_stops_on_block(tmp_path, monkeypatch):
    monkeypatch.setattr(deals, "STATE_PATH", tmp_path / "s.json")
    calls = []
    monkeypatch.setattr(deals, "fetch", lambda s, u: calls.append(u) or None)
    monkeypatch.setattr(deals.time, "sleep", lambda x: None)
    monkeypatch.delenv("DEALS_BOT_TOKEN", raising=False)
    deals.run()
    assert len(calls) == 1  # كل الصفحات الأولى من نفس الموقع: يوقف بعد أول رفض


def test_whole_word_floor_match():
    # تنبيه غلط حقيقي: شاشة 165Hz انحسبت تلفزيون 65 بوصة
    t = ("GAMEON Epic Series 27 Inch Fast IPS Gaming Monitor, FHD 1080p 165Hz Computer Monitor, "
         "0.5ms, 300 Nits, HDR, HDMI 2.1, Adaptive Sync, Built-in Speakers, Ideal for PS5 & Xbox, Smart TV Box")
    assert deals.floor_hit(t, 429.0, "SAR") is None
    assert deals.floor_hit("Samsung 65 Inch Crystal UHD 4K Smart TV", 500.0, "SAR")
    assert deals.floor_hit("ASUS TUF Gaming GeForce RTX 4070 Super 12GB Graphics Card", 600.0, "SAR")
    assert deals.floor_hit("Samsung Galaxy S25 Ultra 5G, 256GB", 900.0, "SAR")
