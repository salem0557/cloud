"""Finviz Elite screener — candidate discovery only.

What this is NOT: a source of score. Finviz tells you a stock has unusual
relative volume today; it does not tell you whether a 15-minute level broke,
which is what the technical 30 measures. Awarding points for "appeared in a
Finviz list" is what the original scanner did, and it is the reason a ticker
could score 19/30 on technicals while price did nothing.

What this IS: a second way in. uw.flow_alerts() returns a capped feed of the
most recent market-wide alerts, so a ticker with real flow can simply miss the
window. Finviz surfaces those movers, and scanner.py then asks UW for that
ticker's own flow before scoring it exactly like any other candidate.

Every number still comes from UW. Finviz only decides who gets looked at.
"""
import csv
import io

import requests

import config as C

# relative volume > 2, average volume > 500k, price > $5, US common stock.
# Deliberately loose: this is a net, not a filter — scoring does the rejecting.
SCREEN = "sh_avgvol_o500,sh_relvol_o2,sh_price_o5,geo_usa"

# Finviz's current export path is /export/<section>. The legacy .ashx URL still
# works but answers with a 301, and a client that does not follow redirects gets
# an empty body. requests does follow them, so the fallback stays safe either
# way; the modern path is tried first so we are not relying on that redirect.
EXPORT_URLS = (
    "https://elite.finviz.com/export/screener",
    "https://elite.finviz.com/export.ashx",
)


def movers(limit=None):
    """-> [{'ticker', 'change_pct', 'volume'}] ordered as Finviz returns them.

    Returns [] on any failure: a Finviz outage must degrade the scan to
    UW-only, never take it down.
    """
    if not C.FINVIZ_AUTH:
        return []

    text = None
    for url in EXPORT_URLS:
        try:
            r = requests.get(url, params={"v": "111", "f": SCREEN,
                                          "auth": C.FINVIZ_AUTH},
                             timeout=30, allow_redirects=True)
        except requests.RequestException as e:
            print(f"[finviz] network error on {url}: {e}")
            continue
        if r.status_code == 404:
            continue                      # path not served — try the other one
        if not r.ok:
            print(f"[finviz] HTTP {r.status_code} — check FINVIZ_AUTH")
            return []
        if "<html" in r.text[:200].lower():
            print("[finviz] got HTML, not CSV — FINVIZ_AUTH is wrong or expired")
            return []
        if not r.text.strip():
            continue                      # empty body: an unfollowed redirect
        text = r.text
        break

    if text is None:
        print("[finviz] no usable response from either export path")
        return []

    out = []
    for row in csv.DictReader(io.StringIO(text)):
        ticker = (row.get("Ticker") or "").strip().upper()
        if not ticker:
            continue
        out.append({
            "ticker": ticker,
            "change_pct": _pct(row.get("Change")),
            "volume": _int(row.get("Volume")),
        })
        if limit and len(out) >= limit:
            break
    return out


def _pct(v):
    try:
        return float(str(v).replace("%", "").strip())
    except (TypeError, ValueError):
        return 0.0


def _int(v):
    try:
        return int(float(str(v).replace(",", "").strip()))
    except (TypeError, ValueError):
        return 0
