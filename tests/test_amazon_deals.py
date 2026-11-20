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
    deals.run()
    assert len(sent) == 1 and "MacBook" in sent[0]  # تنبيه وحيد ومو مكرر رغم 6 صفحات
    deals.run()
    assert len(sent) == 1  # ما يكرر نفس التنبيه
    state = json.loads((tmp_path / "s.json").read_text())
    assert state["cursor"] == 12


def test_renewed_ignored():
    it = {"asin": "X", "title": "Amazon Renewed (Refurbished) iPhone 13 (128GB)", "price": 1149.0,
          "list_price": None, "sponsored": False}
    assert deals.evaluate(it, "SAR", []) == []
    it["title"] = "Apple iPhone 16 Pro (256 GB) - Desert Titanium"
    it["price"] = 999.0
    assert any("خطأ تسعير" in r for r in deals.evaluate(it, "SAR", []))
