"""Paper-trading journal — every alert is appended to journal.csv.

Purpose: after 2-4 weeks Salem calibrates THRESHOLD and WEIGHTS in config.py
from his own results, not from guesses. Fill `outcome` and `result_pct` by hand
(or from your broker export) once a trade closes.
"""
import csv
import datetime

import config as C

FIELDS = [
    "timestamp_riyadh", "date", "ticker", "kind", "score", "direction",
    "flow_score", "technical_score", "catalyst_score", "liquidity_score",
    "spot", "level", "target", "stop", "atr", "volume_ratio", "break_atr",
    "tier", "option_symbol", "strike", "contract_type", "expiry",
    "ask", "cost", "delta", "open_interest",
    "expected_profit_pct", "outcome", "result_pct", "notes",
]


def _ensure_header():
    if not C.JOURNAL_FILE.exists() or C.JOURNAL_FILE.stat().st_size == 0:
        with open(C.JOURNAL_FILE, "w", newline="", encoding="utf-8") as fh:
            csv.DictWriter(fh, fieldnames=FIELDS).writeheader()


def log_alert(payload, kind="entry"):
    """One row per candidate contract, so each tier can be evaluated separately."""
    _ensure_header()
    now = datetime.datetime.now()
    base = {
        "timestamp_riyadh": now.strftime("%Y-%m-%d %H:%M:%S"),
        "date": now.date().isoformat(),
        "ticker": payload.get("ticker", ""),
        "kind": kind,
        "score": payload.get("score", ""),
        "direction": payload.get("direction", ""),
        "spot": payload.get("spot", ""),
        "outcome": "", "result_pct": "", "notes": "",
    }
    for k in ("flow", "technical", "catalyst", "liquidity"):
        base[f"{k}_score"] = (payload.get("score_breakdown") or {}).get(k, "")
    tech = payload.get("technical") or {}
    base.update({
        "level": tech.get("level", ""), "target": tech.get("target", ""),
        "stop": tech.get("stop", ""), "atr": tech.get("atr", ""),
        "volume_ratio": tech.get("volume_ratio", ""),
        "break_atr": tech.get("break_distance_atr", ""),
    })

    rows = []
    for t in payload.get("tiers", []):
        row = dict(base)
        row["tier"] = t.get("tier", "")
        if t.get("option_symbol"):
            row.update({
                "option_symbol": t.get("option_symbol", ""),
                "strike": t.get("strike", ""), "contract_type": t.get("type", ""),
                "expiry": t.get("expiry", ""), "ask": t.get("ask", ""),
                "cost": t.get("cost", ""), "delta": t.get("delta", ""),
                "open_interest": t.get("open_interest", ""),
                "expected_profit_pct": t.get("expected_profit_pct", ""),
            })
        rows.append(row)
    if not rows:
        rows = [base]

    with open(C.JOURNAL_FILE, "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    return len(rows)


def log_exit(payload):
    _ensure_header()
    now = datetime.datetime.now()
    row = {k: "" for k in FIELDS}
    row.update({
        "timestamp_riyadh": now.strftime("%Y-%m-%d %H:%M:%S"),
        "date": now.date().isoformat(),
        "ticker": payload.get("ticker", ""),
        "kind": "exit",
        "option_symbol": payload.get("option_symbol", ""),
        "ask": payload.get("current_price", ""),
        "result_pct": payload.get("pct", ""),
        "outcome": payload.get("type", ""),
        "notes": payload.get("contract", ""),
    })
    with open(C.JOURNAL_FILE, "a", newline="", encoding="utf-8") as fh:
        csv.DictWriter(fh, fieldnames=FIELDS).writerow(row)
