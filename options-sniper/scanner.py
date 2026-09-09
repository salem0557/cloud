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
import venv_boot

venv_boot.ensure(["requests"])

import config as C
import finviz
import journal
import mine
import paper
import reasoning
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
    # Seconds, not just minutes: this stamp is what tells Salem how old the
    # quoted contract price is, and the gap he is guarding against is itself
    # measured in minutes.
    return datetime.datetime.now().strftime("%H:%M:%S")


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
    """Returns a candidate, or None. When it returns None it also sets
    evaluate.last_skip to the reason, so a scan that scores nothing can say
    why instead of printing an empty shortlist and leaving it there."""
    evaluate.last_skip = None
    candles = uw.candles(ticker, timeframe="5D")

    # WHICH WAY. Under the watchlist strategy the BREAK picks the direction:
    # "اختراق مقاومة او كسر دعم". Taking it from the option flow instead — as
    # discovery mode does, because flow is the only thing it knows about a name
    # — made the flow agree with itself by construction, and measured the wrong
    # side of the chart whenever the tape and the price disagreed.
    if C.WATCHLIST_ONLY:
        direction, tech = None, None
        for d in ("call", "put"):
            t = technical.analyse(candles, d)
            if t and technical.confirms(t):
                direction, tech = d, t
                break
        if direction is None:
            # No break held. Before giving up, ask whether one FAILED — a bar
            # that pierced the level and closed back through it. That is the
            # bottom Salem wants to buy, and it is invisible to confirms()
            # because it is precisely a break that did not confirm.
            rev = technical.reversal(candles)
            if rev:
                direction, tech = rev
        if direction is None:
            direction = flow_direction(flow) or "call"
            tech = technical.analyse(candles, direction)
    else:
        direction = flow_direction(flow)
        tech = technical.analyse(candles, direction)

    if tech is None:
        evaluate.last_skip = (f"no candles" if not candles else
                              f"only {len(candles)} bars, need "
                              f"{max(C.CANDLES_LOOKBACK, C.ATR_PERIOD + 2)}")
        return None
    near_miss = False
    if tech["broke_level"] and technical.is_late(tech):
        if C.PAPER_NEAR_MISS and technical.is_near_miss(tech):
            # Room left, but under the rule's minimum. Salem does not see this
            # one; the paper book takes it so the rule can be judged on results
            # instead of on the reasoning behind it.
            near_miss = True
            print(f"  {ticker}: {technical.remaining_atr(tech):.2f} ATR left, "
                  f"rule wants {C.MIN_REMAINING_ATR} — paper book only")
        else:
            # the move already passed its measured target: entering buys the top
            print(f"  {ticker}: break already extended "
                  f"({technical.remaining_atr(tech):.2f} ATR left) — skipped")
            evaluate.last_skip = "break already past its target"
            return None

    spot = tech["close"] or flow["underlying_price"]
    if spot <= 0:
        evaluate.last_skip = "no price"
        return None

    chain = uw.option_chain(ticker)
    if not chain:
        evaluate.last_skip = "empty option chain"
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

    evaluate.last_skip = None
    return {"ticker": ticker, "score": score, "raw_score": raw_score,
            "score_breakdown": breakdown, "risk": assessment,
            "near_miss": near_miss,
            "direction": direction, "spot": round(spot, 2), "flow": flow,
            "flow_reason": flow_reason(flow, direction), "technical": tech,
            "news": [n["headline"] for n in news[:3]], "chain": chain}


evaluate.last_skip = None       # set even if evaluate() is never called


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
    """The three contracts, with everything the message needs on each.

    monitor.py used to build this dict by hand and left out `dte` and `exit`,
    so an alert from the watchlist path — the one Salem actually receives —
    arrived with no expiry tag, no exit plan, no hold clock and no hard-exit
    line. It read like a swing trade on a contract that expires tonight.

    One builder, called from both paths, is the only version of this that
    cannot drift again.
    """
    move = cand["technical"]["expected_move"]
    picks = pick_contracts_by_budget(tradable_chain(cand["chain"]),
                                     cand["direction"], cand["spot"],
                                     expected_move=move,
                                     atr=cand["technical"].get("atr", 0.0))
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
    # The causal chain, in the order Salem reads it: the stock broke a level,
    # the level implies a target, the target moves a strike, the greeks turn
    # that into a contract move. Built from the numbers already in `p` — it
    # computes nothing and states no link whose inputs are missing.
    p["reasoning"] = reasoning.chain(p)
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

    if C.WATCHLIST_ONLY:
        # Discovery off. These names and nothing else, every scan, all session.
        agg = {}
        for ticker in C.WATCHLIST:
            try:
                t_alerts = uw.ticker_flow_alerts(ticker)
            except uw.UWError as e:
                print(f"  [flow] {ticker}: {e}")
                continue
            if t_alerts:
                agg.update(aggregate_flow(t_alerts))
        print(f"Watchlist: {len(C.WATCHLIST)} names, "
              f"{len(agg)} with option flow today")
        return _scan(agg, dry_run, limit_tickers)

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

    # The big names, whether or not anything about them was "unusual" today.
    # UW's feed lists the unusual and Finviz screens for movers; a mega-cap
    # trading its normal huge volume is neither, so it is simply never seen.
    missing = [t for t in C.CORE_TICKERS if t not in agg]
    if missing:
        for ticker in missing:
            try:
                t_alerts = uw.ticker_flow_alerts(ticker)
            except uw.UWError as e:
                print(f"  [core] {ticker}: {e}")
                continue
            if t_alerts:
                agg.update(aggregate_flow(t_alerts))
        print(f"Core names: {len(C.CORE_TICKERS)} "
              f"({len(missing)} not in the UW feed)")

    return _scan(agg, dry_run, limit_tickers)


def _scan(agg, dry_run, limit_tickers):
    if C.WATCHLIST_ONLY:
        # A name with no unusual option flow today still has a chart. "No
        # alerts on the feed" is not "nothing is happening" — it is the normal
        # state of a mega-cap, which is why these names were invisible before.
        for t in C.WATCHLIST:
            agg.setdefault(t, {"premium_usd": 0.0, "sweep_count": 0,
                               "call_premium": 0.0, "put_premium": 0.0,
                               "call_ask_premium": 0.0, "put_ask_premium": 0.0,
                               "ask_premium": 0.0, "bid_premium": 0.0,
                               "vol_oi_ratio": 0.0, "alerts": 0,
                               "underlying_price": 0.0, "rules": []})

    # Not a lockout any more: a name that alerted earlier is still evaluated,
    # and state.record_alert() decides at the send whether this break is a new
    # and stronger one or the same setup arriving again.
    already = set() if C.REALERT else set(state.read().get("alerted_tickers", []))
    cap = limit_tickers or C.MAX_CANDIDATES_PER_SCAN

    def usable(t, f):
        # A core name is looked at whatever its premium. The floor exists to
        # stop paying for data calls on names nobody is trading, and that is
        # not what a mega-cap's quiet hour is.
        return t not in already and (t in C.CORE_TICKERS
                                     or f["premium_usd"] >= C.MIN_TICKER_PREMIUM)

    eligible = sorted(((t, f) for t, f in agg.items() if usable(t, f)),
                      key=lambda kv: kv[1]["premium_usd"], reverse=True)
    # Core names take their slots first, so a busy day in small caps cannot
    # push every large cap past the cap and out of the scan.
    core = [(t, f) for t, f in eligible if t in C.CORE_TICKERS]
    rest = [(t, f) for t, f in eligible if t not in C.CORE_TICKERS]
    ranked = (core + rest)[:cap]
    print(f"Tickers worth a data call ({len(core)} core): {[t for t, _ in ranked]}")

    candidates = []
    dropped = {}
    for ticker, flow in ranked:
        try:
            cand = evaluate(ticker, flow, dry_run)
        except uw.UWError as e:
            print(f"  {ticker}: {e}")
            dropped["request failed"] = dropped.get("request failed", 0) + 1
            continue
        if cand:
            candidates.append(cand)
            print(f"  {ticker}: {cand['score']} {cand['score_breakdown']}")
        else:
            # getattr, not evaluate.last_skip: the attribute belongs to the
            # function object, so anything that wraps or replaces evaluate —
            # a decorator, a test double — makes the scan crash on a line that
            # only exists to log a reason.
            why = getattr(evaluate, "last_skip", None) or "skipped"
            dropped[why] = dropped.get(why, 0) + 1

    # A scan that scores nothing must say why. Without this the log read
    # "60 tickers worth a data call" and then, silently, an empty shortlist.
    if dropped:
        print("  dropped: " + ", ".join(f"{n}x {why}"
                                        for why, n in sorted(dropped.items(),
                                                             key=lambda kv: -kv[1])))
    print(f"Scored {len(candidates)} of {len(ranked)} tickers")

    candidates.sort(key=lambda c: c["score"], reverse=True)

    shortlist = [{"ticker": c["ticker"], "score": c["score"],
                  # base = flow + catalyst + liquidity, i.e. everything EXCEPT the
                  # technical component. monitor.py re-adds technicals from a fresh
                  # break so the two layers never double-count the same 30 points.
                  "base_score": round(c["score"] - c["score_breakdown"]["technical"], 1),
                  # The three components the monitor cannot recompute. Without
                  # them a watchlist alert had no breakdown line at all, and
                  # "did the factors line up" is exactly how Salem judges a
                  # setup.
                  "base_breakdown": {k: c["score_breakdown"][k] for k in
                                     ("flow", "catalyst", "liquidity")},
                  "direction": c["direction"], "spot": c["spot"],
                  "level": c["technical"]["level"],
                  "target": c["technical"]["target"],
                  "stop": c["technical"]["stop"],
                  "updated": datetime.datetime.now().isoformat(timespec="seconds")}
                 for c in candidates
                 if c["score"] >= C.WATCHLIST_FLOOR and not c.get("near_miss")]
    C.SHORTLIST_FILE.write_text(json.dumps(shortlist, indent=2, ensure_ascii=False))
    print(f"Shortlist ({len(shortlist)}): {[x['ticker'] for x in shortlist]}")

    sent = 0
    # candidates are sorted by score, so the loop stops at the LOWER of the two
    # gates. Between PAPER_THRESHOLD and THRESHOLD a setup is real enough to be
    # worth measuring and not good enough to send.
    # The loop stops at the lowest gate any setup could be judged by; each
    # candidate is then measured against its own.
    gates = [C.THRESHOLD, C.BREAK_THRESHOLD]
    if C.PAPER_NEAR_MISS:
        gates.append(C.PAPER_THRESHOLD)
    # Under the watchlist strategy the score is not a gate, so the loop must
    # not stop on it — it stops on the break, per candidate, below.
    floor = float("-inf") if C.WATCHLIST_ONLY else min(gates)
    for cand in candidates:
        if cand["score"] < floor:
            break
        if C.WATCHLIST_ONLY:
            # Salem's rule, not a score: a 15m break with volume behind it and
            # the option flow not pointing the other way.
            side = flow_direction(cand["flow"]) if cand.get("flow") else None
            signal = technical.is_signal(cand["technical"], side, cand["direction"])
            gate = -1 if signal else float("inf")
        else:
            gate = technical.alert_gate(cand["technical"])
        payload = to_payload(cand)

        # Two ways to end up in the paper book and not in 943: the score sits
        # in the band below the alert gate, or the break has room left but
        # under MIN_REMAINING_ATR. Either way: no Telegram, no daily cap, no
        # journal entry as an alert.
        if cand.get("near_miss") or cand["score"] < gate:
            # Below its own gate. It goes to the paper book only if the book is
            # on AND it clears the paper gate — otherwise it is taken nowhere.
            if not C.PAPER_NEAR_MISS or cand["score"] < C.PAPER_THRESHOLD:
                continue
            payload["near_miss"] = True
            why = ("score {:.1f} < {} but >= {}".format(
                       cand["score"], gate, C.PAPER_THRESHOLD)
                   if cand["score"] < gate
                   else "{:.2f} ATR left, alert wants {}".format(
                       technical.remaining_atr(cand["technical"]),
                       C.MIN_REMAINING_ATR))
            if not dry_run and paper.record(payload):
                print(f"  {cand['ticker']}: paper book only — {why}")
            continue

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

        # The caps warn, they do not withhold. Salem picks his own entries, so
        # an alert is information and the decision is his; only the paper book
        # is gated, inside paper.record().
        ok, why = paper.may_open(cand["direction"])
        if not ok:
            payload["caution"] = why
            print(f"  {cand['ticker']}: alerting with a caution — {why}")

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
        t = cand["technical"]
        if not state.record_alert(cand["ticker"], level=t.get("level"),
                                  direction=cand["direction"],
                                  atr=t.get("atr")):
            print(f"  {cand['ticker']}: already alerted on this move, or the "
                  f"day's cap is reached")
            continue
        mid = send(msg)
        if mid:
            mine.remember_alert(mid, payload)
            journal.log_alert(payload)
            # Every alert becomes a paper position automatically. A month of
            # results only exists if nobody has to remember to write it down.
            paper.record(payload)
            sent += 1
        else:
            state.release_alert(cand["ticker"])
    print(f"Alerts sent: {sent}")
    print(uw.spent())
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
