"""Finviz Elite — the stock-level state Unusual Whales does not serve.

This module used one view: v=111, Overview, which returns a name, a sector, a
price and a volume. That is almost none of what an Elite subscription is for,
and it meant Finviz contributed a list of tickers and nothing else while every
piece of analysis ran on UW.

That was defensible while the research was contract-level. It stopped being so
once the out-of-sample runs showed the missing variable was the STOCK: a
contract explodes because its underlying moves, and nothing in a contract's own
tape says whether it will. v=171, the Technical view, carries exactly that —
ATR, RSI, distance from each moving average, the gap, beta, position in the
52-week range — and UW serves none of it.

Two rules hold throughout:
  * Finviz never scores. It supplies candidates and stock state; every number
    that reaches an alert is computed from UW data.
  * Columns are read by header NAME, never by position. A view's column order
    is Finviz's to change, and a positional parser would mis-read it silently.
"""
import csv
import io

import requests

import config as C

# relative volume > 2, average volume > 500k, price > $5, US common stock.
# Deliberately loose: this is a net, not a filter — scoring does the rejecting.
SCREEN = "sh_avgvol_o500,sh_relvol_o2,sh_price_o5,geo_usa"

VIEW_OVERVIEW = "111"       # name, sector, price, volume

# Finviz's current export path. The legacy .ashx URL still works but answers
# with a 301, and a client that does not follow redirects gets an empty body.
EXPORT_URLS = (
    "https://elite.finviz.com/export/screener",
    "https://elite.finviz.com/export.ashx",
)
_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/120.0 Safari/537.36")


def _fetch(params):
    """-> CSV text, or None. Any failure degrades the caller to UW-only."""
    if not C.FINVIZ_AUTH:
        return None
    for url in EXPORT_URLS:
        try:
            r = requests.get(url, params=params, timeout=30,
                             headers={"User-Agent": _UA}, allow_redirects=True)
        except requests.RequestException as e:
            print(f"[finviz] network error on {url}: {e}")
            continue
        if r.status_code == 404:
            continue                          # path not served — try the other
        if not r.ok:
            print(f"[finviz] HTTP {r.status_code} — check FINVIZ_AUTH")
            return None
        if "<html" in r.text[:200].lower():
            print("[finviz] got HTML, not CSV — FINVIZ_AUTH is wrong or expired")
            return None
        if r.text.strip():
            return r.text
    print("[finviz] no usable response from either export path")
    return None


# ── Overview: candidate discovery ───────────────────────────────
def movers(limit=None):
    """-> [{'ticker', 'change_pct', 'volume'}]. Candidates only, never scored."""
    text = _fetch({"v": VIEW_OVERVIEW, "f": SCREEN, "auth": C.FINVIZ_AUTH})
    if not text:
        return []
    out = []
    for row in csv.DictReader(io.StringIO(text)):
        ticker = (row.get("Ticker") or "").strip().upper()
        if not ticker:
            continue
        out.append({"ticker": ticker,
                    "change_pct": _num(row.get("Change")),
                    "volume": _int(row.get("Volume"))})
        if limit and len(out) >= limit:
            break
    return out


def _num(v):
    """Finviz writes percents as "-3.24%" and volumes with commas."""
    if v is None:
        return 0.0
    s = str(v).replace("%", "").replace(",", "").strip()
    if s in ("", "-"):
        return 0.0
    try:
        return float(s)
    except ValueError:
        return 0.0


def _int(v):
    return int(_num(v))
