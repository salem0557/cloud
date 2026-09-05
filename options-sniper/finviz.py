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
VIEW_TECHNICAL = "171"      # ATR, RSI, SMA distances, gap, beta, 52w position

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


# ── Technical: stock state ──────────────────────────────────────
# Finviz's column headers mapped to the names used here.
_TECH_COLUMNS = {
    "Beta": "beta", "ATR": "atr", "RSI": "rsi",
    "SMA20": "vs_sma20", "SMA50": "vs_sma50", "SMA200": "vs_sma200",
    "52W High": "vs_52w_high", "52W Low": "vs_52w_low",
    "Rel Volume": "rel_volume", "Avg Volume": "avg_volume",
    "Price": "price", "Change": "change_pct", "from Open": "from_open",
    "Gap": "gap", "Volatility W": "vol_week", "Volatility M": "vol_month",
}


def technicals(tickers=None, screen=None, limit=None):
    """Finviz Technical view -> {TICKER: {...}}.

    Carries whatever of _TECH_COLUMNS the response actually returned, plus
    `_columns` naming them, so a changed view shows up as a short list rather
    than as quietly missing features. Percent strings like "-3.24%" become
    floats; SMA columns are the stock's distance from that average.
    """
    params = {"v": VIEW_TECHNICAL, "auth": C.FINVIZ_AUTH}
    if tickers:
        params["t"] = ",".join(t.upper() for t in tickers)
    else:
        params["f"] = screen or SCREEN

    text = _fetch(params)
    if not text:
        return {}

    reader = csv.DictReader(io.StringIO(text))
    headers = reader.fieldnames or []
    if "Ticker" not in headers:
        print(f"[finviz] technical view has no Ticker column "
              f"(got: {', '.join(headers[:8])})")
        return {}
    found = [c for c in headers if c in _TECH_COLUMNS]
    if not found:
        print(f"[finviz] none of the expected technical columns are present "
              f"(got: {', '.join(headers[:10])})")

    out = {}
    for row in reader:
        t = (row.get("Ticker") or "").strip().upper()
        if not t:
            continue
        rec = {"ticker": t, "_columns": found}
        for col, name in _TECH_COLUMNS.items():
            if col in row:
                rec[name] = _num(row[col])
        out[t] = rec
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
