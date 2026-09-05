"""Shortlist + open-positions monitor (cron: every 5 min during market hours).

Cheap layer: pure Python checks on 15m candles, now sourced from Unusual Whales
(real-time) instead of yfinance (delayed ~15 min — useless for short-dated
options). The message is composed only when a break actually confirms or an exit
rule triggers.
"""
import argparse
import datetime
import json
import sys

import config as C
import journal
import market
import state
import technical
import uw
from compose import compose, NO_TRADE
from scoring import (contract_cost, expected_profit_pct, technical_score,
                     pick_contracts_by_budget)
from telegram_send import send


def load_json(path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text())
    except (ValueError, OSError):
        return default


def now_riyadh():
    return datetime.datetime.now().strftime("%H:%M")


# ── Exit monitoring: positions.json ─────────────────────────────
def check_positions(dry_run=False):
    """positions.json format (you add a row after entering a trade):
    [{"ticker":"UBER","contract":"UBER 260911 C76","entry_price":0.85,
      "option_symbol":"UBER260911C00076000"}]

    Exit alerts are NOT counted against the 5/day entry cap — being told to
    close a position you already hold must never be suppressed by a quota.
    """
    positions = load_json(C.POSITIONS_FILE, [])
    if not positions:
        return 0
    fired = 0
    for p in positions:
        sym = p.get("option_symbol") or p.get("yf_symbol")
        if not sym or not p.get("entry_price"):
            continue
        quote = uw.contract_quote(sym)
        if not quote or quote["price"] <= 0:
            print("[monitor] no quote for", sym)
            continue
        entry, price = float(p["entry_price"]), quote["price"]
        pct = (price - entry) / entry * 100

        kind = reason = advice = None
        if pct >= C.PROFIT_TAKE_PCT:
            kind = "✅ جني ربح"
            reason = f"العقد وصل {pct:+.1f}% (حد الربح {C.PROFIT_TAKE_PCT}%)"
            advice = "بيع نصف الكمية ورفع الوقف على الباقي إلى سعر الدخول"
        elif pct <= C.STOP_LOSS_PCT:
            kind = "⛔ وقف خسارة"
            reason = f"العقد نزل {pct:+.1f}% (حد الخسارة {C.STOP_LOSS_PCT}%)"
            advice = "بيع كامل"
        if not kind or p.get("last_alert") == kind:
            continue

        payload = {"ticker": p.get("ticker", ""), "type": kind,
                   "contract": p.get("contract", sym), "option_symbol": sym,
                   "entry_price": entry, "current_price": round(price, 2),
                   "pct": round(pct, 1), "reason": reason, "advice": advice,
                   "time_riyadh": now_riyadh()}
        msg = compose("exit", payload)
        if dry_run:
            print("\n[DRY RUN exit]\n" + msg)
            fired += 1
            continue
        if send(msg):
            p["last_alert"] = kind
            journal.log_exit(payload)
            fired += 1
    if not dry_run:
        C.POSITIONS_FILE.write_text(json.dumps(positions, indent=2, ensure_ascii=False))
    return fired


# ── Entry monitoring: shortlist breakouts ───────────────────────
def check_shortlist(dry_run=False):
    shortlist = load_json(C.SHORTLIST_FILE, [])
    if not shortlist:
        return 0
    already = set(state.read().get("alerted_tickers", []))
    sent = 0
    for item in shortlist:
        if not dry_run and state.capacity_left() == 0:
            print("Daily cap reached — stopping.")
            break
        t = item["ticker"]
        if t in already:
            continue
        try:
            candles = uw.candles(t, timeframe="5D")
            tech = technical.analyse(candles, item["direction"])
        except uw.UWError as e:
            print(f"  {t}: {e}")
            continue
        if not technical.confirms(tech):
            if tech and tech["broke_level"] and technical.is_late(tech):
                print(f"  {t}: break already extended — skipped")
            continue

        # re-score: shortlist carries flow+catalyst+liquidity ("base_score");
        # the technical 30 is recomputed from the break that just confirmed.
        base = item.get("base_score")
        if base is None:                       # shortlist from an older scanner run
            base = item["score"]
        score = round(min(100.0, base + technical_score(tech)), 1)
        if score < C.THRESHOLD:
            print(f"  {t}: break confirmed but score {score} < {C.THRESHOLD}")
            continue

        try:
            chain = uw.option_chain(t)
        except uw.UWError as e:
            print(f"  {t}: {e}")
            continue
        picks = pick_contracts_by_budget(chain, item["direction"], tech["close"])
        move = tech["expected_move"]
        tiers = []
        for label, c in picks:
            if c is None:
                tiers.append({"tier": label, "option_symbol": None})
            else:
                tiers.append({
                    "tier": label, "option_symbol": c["option_symbol"],
                    "strike": c["strike"], "type": c["type"], "expiry": c["expiry"],
                    "ask": c["ask"], "bid": c["bid"], "cost": contract_cost(c),
                    "delta": c["delta"], "open_interest": c["open_interest"],
                    "expected_profit_pct": expected_profit_pct(c, move),
                })

        payload = {"ticker": t, "score": score, "direction": item["direction"],
                   "spot": tech["close"], "technical": tech, "tiers": tiers,
                   "flow_reason": "كسر مؤكد على فريم 15د بعد تدفق خيارات",
                   "news": [], "time_riyadh": now_riyadh()}
        msg = compose("entry", payload)
        if msg.startswith(NO_TRADE):
            print(t, msg)
            continue
        if dry_run:
            print("\n" + "=" * 50 + f"\n[DRY RUN] {t}\n" + "=" * 50 + "\n" + msg)
            sent += 1
            continue
        if not state.record_alert(t):
            break
        if send(msg):
            journal.log_alert(payload)
            sent += 1
        else:
            state.release_alert(t)
    return sent


def main(dry_run=False):
    if not dry_run and not market.is_open():
        print("Market closed —", market.reason())
        return
    exits = check_positions(dry_run)
    entries = check_shortlist(dry_run)
    print(f"exits: {exits}  entries: {entries}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    try:
        main(dry_run=args.dry_run)
    except uw.UWError as e:
        print("UW error:", e, file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print("monitor error:", e, file=sys.stderr)
        sys.exit(1)
