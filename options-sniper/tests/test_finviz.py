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
