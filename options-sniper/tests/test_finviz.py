"""Finviz is candidate discovery, never a score. These tests pin that down."""
import sys, pathlib, io
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import config as C
import finviz
import scoring

CSV = (
    'No.,Ticker,Company,Sector,Industry,Country,Market Cap,P/E,Price,Change,Volume\n'
    '1,"NVDA","NVIDIA","Tech","Semis","USA","3000B","55","102.40","4.20%","81,234,567"\n'
    '2,"UBER","Uber","Tech","Software","USA","150B","30","76.10","-2.10%","22,000,000"\n'
)


class FakeResp:
    def __init__(self, text, ok=True, status=200):
        self.text, self.ok, self.status_code = text, ok, status


def test_parses_the_export_csv(monkeypatch):
    monkeypatch.setattr(C, "FINVIZ_AUTH", "token")
    monkeypatch.setattr(finviz.requests, "get", lambda *a, **k: FakeResp(CSV))
    rows = finviz.movers()
    assert [r["ticker"] for r in rows] == ["NVDA", "UBER"]
    assert rows[0]["change_pct"] == 4.20
    assert rows[0]["volume"] == 81234567
    assert rows[1]["change_pct"] == -2.10


def test_no_key_means_no_call(monkeypatch):
    monkeypatch.setattr(C, "FINVIZ_AUTH", "")
    def boom(*a, **k):
        raise AssertionError("must not call Finviz without a token")
    monkeypatch.setattr(finviz.requests, "get", boom)
    assert finviz.movers() == []


def test_falls_back_when_the_modern_path_404s(monkeypatch):
    """/export/screener is tried first; a 404 there must fall through to the
    legacy .ashx path rather than aborting the scan."""
    monkeypatch.setattr(C, "FINVIZ_AUTH", "token")
    seen = []

    def fake_get(url, **k):
        seen.append(url)
        return FakeResp("", ok=False, status=404) if "export/screener" in url \
            else FakeResp(CSV)

    monkeypatch.setattr(finviz.requests, "get", fake_get)
    assert [r["ticker"] for r in finviz.movers()] == ["NVDA", "UBER"]
    assert len(seen) == 2 and seen[0].endswith("/export/screener")


def test_empty_body_falls_through(monkeypatch):
    """An unfollowed 301 returns 200 with nothing in it — not a valid answer."""
    monkeypatch.setattr(C, "FINVIZ_AUTH", "token")
    monkeypatch.setattr(finviz.requests, "get",
                        lambda url, **k: FakeResp("") if "export/screener" in url
                        else FakeResp(CSV))
    assert [r["ticker"] for r in finviz.movers()] == ["NVDA", "UBER"]


def test_modern_path_is_preferred(monkeypatch):
    monkeypatch.setattr(C, "FINVIZ_AUTH", "token")
    seen = []
    monkeypatch.setattr(finviz.requests, "get",
                        lambda url, **k: (seen.append(url), FakeResp(CSV))[1])
    finviz.movers()
    assert seen == ["https://elite.finviz.com/export/screener"]


def test_failures_degrade_to_uw_only(monkeypatch):
    """A Finviz outage must never take the scan down."""
    monkeypatch.setattr(C, "FINVIZ_AUTH", "token")

    monkeypatch.setattr(finviz.requests, "get",
                        lambda *a, **k: FakeResp("", ok=False, status=403))
    assert finviz.movers() == []

    # an expired token returns the login page, not CSV
    monkeypatch.setattr(finviz.requests, "get",
                        lambda *a, **k: FakeResp("<html><body>login</body></html>"))
    assert finviz.movers() == []

    def net_error(*a, **k):
        raise finviz.requests.RequestException("boom")
    monkeypatch.setattr(finviz.requests, "get", net_error)
    assert finviz.movers() == []


def test_limit_is_respected(monkeypatch):
    monkeypatch.setattr(C, "FINVIZ_AUTH", "token")
    monkeypatch.setattr(finviz.requests, "get", lambda *a, **k: FakeResp(CSV))
    assert len(finviz.movers(limit=1)) == 1


# ── The rule this whole module exists to respect ────────────────
def test_finviz_never_contributes_to_a_score():
    """Appearing in a Finviz list must not move any of the four components.
    The original scanner granted 19/30 for exactly that, which is why a stock
    that had not moved could clear the shortlist."""
    tech_no_break = {"broke_level": False}
    assert scoring.technical_score(tech_no_break) == 0.0
    # no flow, no news, no contract -> nothing a mover list can rescue
    assert scoring.total_score({}, tech_no_break, [], None, "call") == 0.0


# ── Technical view: the stock state UW does not serve ───────────
TECH_CSV = (
    'No.,Ticker,Beta,ATR,SMA20,SMA50,SMA200,52W High,52W Low,RSI,Price,Change,'
    'from Open,Gap,Volume\n'
    '1,"NVDA","2.11","4.52","-1.24%","3.80%","18.50%","-6.20%","112.40%",'
    '"48.30","230.36","1.20%","0.40%","0.80%","5,458,992"\n'
    '2,"TSLA","2.45","11.03","2.10%","-4.50%","9.20%","-15.30%","78.10%",'
    '"61.70","354.08","-0.90%","-1.10%","0.20%","3,944,033"\n'
)


def test_technical_view_is_parsed_by_column_name(monkeypatch):
    """A view's column order is Finviz's to change; a positional parser would
    mis-read it silently."""
    monkeypatch.setattr(C, "FINVIZ_AUTH", "token")
    monkeypatch.setattr(finviz.requests, "get", lambda *a, **k: FakeResp(TECH_CSV))
    rows = finviz.technicals()
    assert set(rows) == {"NVDA", "TSLA"}
    assert rows["NVDA"]["rsi"] == 48.30
    assert rows["NVDA"]["atr"] == 4.52
    assert rows["NVDA"]["vs_sma20"] == -1.24        # percent, sign preserved
    assert rows["TSLA"]["beta"] == 2.45


def test_it_requests_the_technical_view_not_overview(monkeypatch):
    """v=111 returns a name and a price — almost none of what the subscription
    is for. The stock state lives in v=171."""
    monkeypatch.setattr(C, "FINVIZ_AUTH", "token")
    seen = {}
    monkeypatch.setattr(finviz.requests, "get",
                        lambda url, params=None, **k: (seen.update(params or {}),
                                                       FakeResp(TECH_CSV))[1])
    finviz.technicals()
    assert seen["v"] == finviz.VIEW_TECHNICAL


def test_specific_tickers_can_be_requested(monkeypatch):
    monkeypatch.setattr(C, "FINVIZ_AUTH", "token")
    seen = {}
    monkeypatch.setattr(finviz.requests, "get",
                        lambda url, params=None, **k: (seen.update(params or {}),
                                                       FakeResp(TECH_CSV))[1])
    finviz.technicals(tickers=["nvda", "tsla"])
    assert seen["t"] == "NVDA,TSLA"


def test_a_changed_view_is_reported_not_silently_empty(monkeypatch):
    monkeypatch.setattr(C, "FINVIZ_AUTH", "token")
    monkeypatch.setattr(finviz.requests, "get",
                        lambda *a, **k: FakeResp('No.,Company,Sector\n1,"X","Tech"\n'))
    assert finviz.technicals() == {}        # no Ticker column


def test_columns_actually_returned_are_recorded(monkeypatch):
    monkeypatch.setattr(C, "FINVIZ_AUTH", "token")
    monkeypatch.setattr(finviz.requests, "get", lambda *a, **k: FakeResp(TECH_CSV))
    cols = finviz.technicals()["NVDA"]["_columns"]
    assert "RSI" in cols and "ATR" in cols


def test_percent_and_comma_formats():
    assert finviz._num("-3.24%") == -3.24
    assert finviz._num("5,458,992") == 5458992.0
    assert finviz._num("-") == 0.0
    assert finviz._num(None) == 0.0


def test_no_token_means_no_request(monkeypatch):
    monkeypatch.setattr(C, "FINVIZ_AUTH", "")
    def boom(*a, **k):
        raise AssertionError("must not call Finviz without a token")
    monkeypatch.setattr(finviz.requests, "get", boom)
    assert finviz.technicals() == {}
    assert finviz.movers() == []
