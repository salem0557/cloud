"""Full-market scan (cron: every 30 min during US market hours).

Pipeline: UW unusual flow -> aggregate per ticker -> real 15m technicals from UW
candles -> score in code -> shortlist.json -> for score >= THRESHOLD: budget
filter contracts -> compose Arabic alert -> Telegram -> journal.csv.

The daily cap is enforced in state.py under a file lock, not here.
"""
import argparse
import datetime
import json
import sys

import config as C
import journal
import state
import technical
import uw
from compose import compose, NO_TRADE
from scoring import (best_contract, contract_cost, expected_profit_pct,
                     flow_direction, flow_score, technical_score,
                     catalyst_score, liquidity_score, pick_contracts_by_budget)
from telegram_send import send


def now_riyadh():
    return datetime.datetime.now().strftime("%H:%M")


# ── Aggregate UW flow alerts per ticker ─────────────────────────
def aggregate_flow(alerts):
    agg = {}
    for a in alerts:
        t = a["ticker"]
        d = agg.setdefault(t, {"premium_usd": 0.0, "sweep_count": 0,
                               "call_premium": 0.0, "put_premium": 0.0,
                               "vol_oi_ratio": 0.0, "alerts": 0,
                               "underlying_price": 0.0, "rules": set()})
        prem = a["total_premium"]
        d["premium_usd"] += prem
        d["alerts"] += 1
        if a["has_sweep"]:
            d["sweep_count"] += 1
        if a["type"] == "call":
            d["call_premium"] += prem
        else:
            d["put_premium"] += prem
        d["vol_oi_ratio"] = max(d["vol_oi_ratio"], a["volume_oi_ratio"])
        if a["underlying_price"]:
            d["underlying_price"] = a["underlying_price"]
        if a["alert_rule"]:
            d["rules"].add(a["alert_rule"])
    for d in agg.values():
        d["rules"] = sorted(d["rules"])
    return agg


def flow_reason(flow, direction):
    side = "شراء كول" if direction == "call" else "شراء بوت"
    prem = flow["premium_usd"]
    bits = [f"{side} بـ ${prem/1e6:.1f}M علاوة" if prem >= 1e6
            else f"{side} بـ ${prem/1e3:.0f}K علاوة"]
    if flow["sweep_count"]:
        bits.append(f"{flow['sweep_count']} سويب")
    if flow["vol_oi_ratio"] >= 1:
        bits.append(f"فوليوم/OI {flow['vol_oi_ratio']:.1f}")
    return "، ".join(bits)


# ── Build one candidate ─────────────────────────────────────────
def evaluate(ticker, flow, dry_run=False):
    direction = flow_direction(flow)

    candles = uw.candles(ticker, timeframe="5D")
    tech = technical.analyse(candles, direction)
    if tech is None:
        return None                      # not enough candle history -> skip
    if tech["broke_level"] and technical.is_late(tech):
        # the move already reached its measured target: entering now buys the top
        print(f"  {ticker}: break already extended "
              f"({technical.remaining_atr(tech):.2f} ATR left) — skipped")
        return None

    spot = tech["close"] or flow["underlying_price"]
    if spot <= 0:
        return None

    chain = uw.option_chain(ticker)
    if not chain:
        return None

    news = uw.news(ticker)
    best = best_contract(chain, direction, spot)

    breakdown = {
        "flow": flow_score(flow),
        "technical": technical_score(tech),
        "catalyst": catalyst_score(news, direction),
        "liquidity": liquidity_score(best),
    }
    score = round(sum(breakdown.values()), 1)

    return {"ticker": ticker, "score": score, "score_breakdown": breakdown,
            "direction": direction, "spot": round(spot, 2), "flow": flow,
            "flow_reason": flow_reason(flow, direction), "technical": tech,
            "news": [n["headline"] for n in news[:3]], "chain": chain}


def build_tiers(cand):
    picks = pick_contracts_by_budget(cand["chain"], cand["direction"], cand["spot"])
    move = cand["technical"]["expected_move"]
    tiers = []
    for label, c in picks:
        if c is None:
            tiers.append({"tier": label, "option_symbol": None})
            continue
        tiers.append({
            "tier": label, "option_symbol": c["option_symbol"],
            "strike": c["strike"], "type": c["type"], "expiry": c["expiry"],
            "ask": c["ask"], "bid": c["bid"], "cost": contract_cost(c),
            "delta": c["delta"], "open_interest": c["open_interest"],
            "expected_profit_pct": expected_profit_pct(c, move),
        })
    return tiers


def to_payload(cand):
    p = {k: cand[k] for k in ("ticker", "score", "score_breakdown", "direction",
                              "spot", "flow_reason", "technical", "news")}
    p["tiers"] = build_tiers(cand)
    p["time_riyadh"] = now_riyadh()
    return p


# ── Main ────────────────────────────────────────────────────────
def main(dry_run=False, limit_tickers=None):
    if not dry_run and state.capacity_left() == 0:
        print("Daily cap reached — scan skipped.")
        return 0

    alerts = uw.flow_alerts()
    print(f"UW flow alerts: {len(alerts)}")
    agg = aggregate_flow(alerts)

    already = set(state.read().get("alerted_tickers", []))
    ranked = sorted(
        ((t, f) for t, f in agg.items()
         if f["premium_usd"] >= C.MIN_TICKER_PREMIUM and t not in already),
        key=lambda kv: kv[1]["premium_usd"], reverse=True,
    )[:limit_tickers or C.MAX_CANDIDATES_PER_SCAN]
    print(f"Tickers worth a data call: {[t for t, _ in ranked]}")

    candidates = []
    for ticker, flow in ranked:
        try:
            cand = evaluate(ticker, flow, dry_run)
        except uw.UWError as e:
            print(f"  {ticker}: {e}")
            continue
        if cand:
            candidates.append(cand)
            print(f"  {ticker}: {cand['score']} {cand['score_breakdown']}")

    candidates.sort(key=lambda c: c["score"], reverse=True)

    shortlist = [{"ticker": c["ticker"], "score": c["score"],
                  # base = flow + catalyst + liquidity, i.e. everything EXCEPT the
                  # technical component. monitor.py re-adds technicals from a fresh
                  # break so the two layers never double-count the same 30 points.
                  "base_score": round(c["score"] - c["score_breakdown"]["technical"], 1),
                  "direction": c["direction"], "spot": c["spot"],
                  "level": c["technical"]["level"],
                  "target": c["technical"]["target"],
                  "stop": c["technical"]["stop"],
                  "updated": datetime.datetime.now().isoformat(timespec="seconds")}
                 for c in candidates if c["score"] >= C.WATCHLIST_FLOOR]
    C.SHORTLIST_FILE.write_text(json.dumps(shortlist, indent=2, ensure_ascii=False))
    print(f"Shortlist ({len(shortlist)}): {[x['ticker'] for x in shortlist]}")

    sent = 0
    for cand in candidates:
        if cand["score"] < C.THRESHOLD:
            break
        payload = to_payload(cand)
        msg = compose("entry", payload)
        if msg.startswith(NO_TRADE):
            print(cand["ticker"], msg)
            continue
        if dry_run:
            print("\n" + "=" * 50 + f"\n[DRY RUN] {cand['ticker']}\n" + "=" * 50)
            print(msg)
            journal.log_alert(payload)
            sent += 1
            continue
        if not state.record_alert(cand["ticker"]):
            print("Daily cap reached — stopping.")
            break
        if send(msg):
            journal.log_alert(payload)
            sent += 1
        else:
            state.release_alert(cand["ticker"])
    print(f"Alerts sent: {sent}")
    return sent


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="print alerts instead of sending, ignore the daily cap")
    ap.add_argument("--limit", type=int, help="max tickers to evaluate")
    args = ap.parse_args()
    try:
        main(dry_run=args.dry_run, limit_tickers=args.limit)
    except uw.UWError as e:
        print("UW error:", e, file=sys.stderr)
        sys.exit(2)
    except Exception as e:
        print("scanner error:", e, file=sys.stderr)
        sys.exit(1)
