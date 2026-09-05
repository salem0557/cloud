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
EXPORT = "https://elite.finviz.com/export.ashx"


def movers(limit=None):
    """-> [{'ticker', 'change_pct', 'volume'}] ordered as Finviz returns them.

    Returns [] on any failure: a Finviz outage must degrade the scan to
    UW-only, never take it down.
    """
    if not C.FINVIZ_AUTH:
        return []
    try:
        r = requests.get(EXPORT, params={"v": "111", "f": SCREEN,
                                         "auth": C.FINVIZ_AUTH}, timeout=30)
    except requests.RequestException as e:
        print("[finviz] network error:", e)
        return []
    if not r.ok:
        print(f"[finviz] HTTP {r.status_code} — check FINVIZ_AUTH")
        return []
    if "<html" in r.text[:200].lower():
        print("[finviz] got HTML, not CSV — FINVIZ_AUTH is wrong or expired")
        return []

    out = []
    for row in csv.DictReader(io.StringIO(r.text)):
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
