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

import analyst
import config as C
import finviz
import journal
import market
import risk
import state
import technical
import uw
from compose import compose, NO_TRADE
from scoring import (ask_side_ratio, best_contract, contract_cost, exit_rule,
                     expected_profit_pct, flow_direction, flow_score,
                     technical_score, catalyst_score, liquidity_score,
                     pick_contracts_by_budget)
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
                               "call_ask_premium": 0.0, "put_ask_premium": 0.0,
                               "ask_premium": 0.0, "bid_premium": 0.0,
                               "vol_oi_ratio": 0.0, "alerts": 0,
                               "underlying_price": 0.0, "rules": set()})
        prem = a["total_premium"]
        ask, bid = a["ask_side_premium"], a["bid_side_premium"]
        d["premium_usd"] += prem
        d["ask_premium"] += ask
        d["bid_premium"] += bid
        d["alerts"] += 1
        if a["has_sweep"]:
            d["sweep_count"] += 1
        if a["type"] == "call":
            d["call_premium"] += prem
            d["call_ask_premium"] += ask
        else:
            d["put_premium"] += prem
            d["put_ask_premium"] += ask
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
    ratio = ask_side_ratio(flow)
    if ratio:
        bits.append(f"{ratio*100:.0f}% عند الطلب")
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
    raw_score = round(sum(breakdown.values()), 1)

    # Risk checks run only on candidates that would otherwise qualify — each
    # costs API calls, and there is no point pricing the risk of a setup that
    # is not a setup.
    assessment = {"penalty": 0.0, "flags": []}
    if raw_score >= C.THRESHOLD:
        assessment = risk.assess(ticker, direction, flow, chain)
    score = round(raw_score - assessment["penalty"], 1)
    if assessment["flags"]:
        print(f"  {ticker}: {raw_score} − {assessment['penalty']} risk = {score}")
        for f in assessment["flags"]:
            print(f"      ⚠ {f}")

    return {"ticker": ticker, "score": score, "raw_score": raw_score,
            "score_breakdown": breakdown, "risk": assessment,
            "direction": direction, "spot": round(spot, 2), "flow": flow,
            "flow_reason": flow_reason(flow, direction), "technical": tech,
            "news": [n["headline"] for n in news[:3]], "chain": chain}


def tradable_chain(chain):
    """Drop same-day contracts once too little of the session is left.

    A 0DTE contract is worth its intrinsic value at 16:00 ET and nothing more,
    so an entry taken minutes before the bell needs the whole measured move to
    land almost immediately. Set MIN_MINUTES_TO_CLOSE = 0 to allow them anyway.
    """
    if not C.MIN_MINUTES_TO_CLOSE:
        return chain
    left = market.minutes_to_close()
    if left >= C.MIN_MINUTES_TO_CLOSE:
        return chain
    kept = [c for c in chain if (c.get("dte") or 0) > 0]
    dropped = len(chain) - len(kept)
    if dropped:
        print(f"  dropped {dropped} same-day contracts — {left} min to the close "
              f"(minimum {C.MIN_MINUTES_TO_CLOSE})")
    return kept


def build_tiers(cand):
    picks = pick_contracts_by_budget(tradable_chain(cand["chain"]),
                                     cand["direction"], cand["spot"])
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
            "delta": c["delta"], "gamma": c.get("gamma"), "theta": c.get("theta"),
            "open_interest": c["open_interest"], "dte": c.get("dte"),
            "expected_profit_pct": expected_profit_pct(c, move),
            "exit": exit_rule(c.get("dte")),
        })
    return tiers


def to_payload(cand):
    p = {k: cand[k] for k in ("ticker", "score", "raw_score", "score_breakdown",
                              "risk", "direction", "spot", "flow_reason",
                              "technical", "news")}
    p["tiers"] = build_tiers(cand)
    p["time_riyadh"] = now_riyadh()
    return p


# ── Main ────────────────────────────────────────────────────────
def main(dry_run=False, limit_tickers=None):
    if not dry_run and not market.is_open():
        print("Market closed —", market.reason())
        return 0
    if not dry_run and state.capacity_left() == 0:
        print("Daily cap reached — scan skipped.")
        return 0

    alerts = uw.flow_alerts()
    print(f"UW flow alerts: {len(alerts)}")
    agg = aggregate_flow(alerts)

    # Finviz movers that the capped market-wide feed did not return. For each,
    # ask UW for that ticker's own flow — Finviz decides who gets looked at,
    # UW still supplies every number that is scored.
    movers = finviz.movers(limit=C.MAX_FINVIZ_MOVERS)
    if movers:
        extra = [m["ticker"] for m in movers if m["ticker"] not in agg]
        print(f"Finviz movers: {len(movers)} ({len(extra)} not in the UW feed)")
        for ticker in extra[:C.MAX_FINVIZ_LOOKUPS]:
            t_alerts = uw.ticker_flow_alerts(ticker)
            if t_alerts:
                agg.update(aggregate_flow(t_alerts))

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

        # Final read. An unreachable analyst returns None and the alert goes
        # out on the arithmetic — the layer may reject a setup, never silently
        # swallow one because a request failed.
        note = analyst.review(payload)
        if note:
            payload["analyst"] = note
            print(f"  {cand['ticker']} analyst: {note.get('verdict')} "
                  f"({note.get('conviction')}, {note.get('vs_base_rate')} من المعدل)")
            if C.ANALYST_CAN_BLOCK and note.get("verdict") == "SKIP":
                print(f"    ↳ rejected: {note.get('reading', '')[:120]}")
                journal.log_alert(payload, kind="analyst_skip")
                continue

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
