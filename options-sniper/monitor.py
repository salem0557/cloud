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

import venv_boot

venv_boot.ensure(["requests"])

import config as C
import journal
import market
import paper
import reasoning
import state
import technical
import uw
from compose import compose, NO_TRADE
from scanner import aggregate_flow, flow_reason
from scoring import (ask_side_ratio, contract_cost, exit_rule,
                     expected_profit_pct, flow_direction,
                     technical_score, pick_contracts_by_budget)
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

        occ = uw.parse_occ(sym) or {}
        rule = exit_rule(uw._dte(occ.get("expiry")))

        kind = reason = advice = None
        if pct >= rule["take_pct"]:
            kind = "✅ جني ربح"
            reason = f"العقد وصل {pct:+.1f}% (حد الربح {rule['take_pct']}%)"
            advice = rule["note"] or "بيع كامل"
        elif pct <= rule["stop_pct"]:
            kind = "⛔ وقف خسارة"
            reason = f"العقد نزل {pct:+.1f}% (حد الخسارة {rule['stop_pct']}%)"
            advice = "بيع كامل"
        elif rule["dte"] == 0 and market.past_hard_exit():
            # a same-day contract keeps only intrinsic value into the bell, and
            # the last half hour is where that collapse is fastest
            kind = "⏰ خروج زمني"
            reason = (f"عقد ينتهي اليوم وتجاوزنا {C.ZERO_DTE_HARD_EXIT_ET} "
                      f"بتوقيت نيويورك (الوضع {pct:+.1f}%)")
            advice = "بيع كامل الآن — لا تحمله للإغلاق"
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
            if tech and tech["broke_level"]:
                if technical.is_late(tech):
                    print(f"  {t}: break already extended — skipped")
                elif not technical.holds(tech):
                    print(f"  {t}: broke and reversed inside the candle "
                          f"(close {tech['close']} in a {tech['bar_low']}-"
                          f"{tech['bar_high']} bar) — skipped")
            continue

        # Pressure NOW, not at scan time. A shortlist entry can be twenty
        # minutes old, and the flow that put it there may have turned; the
        # break is only worth taking if buyers (or sellers) are still leaning.
        try:
            fresh = aggregate_flow(uw.ticker_flow_alerts(t)).get(t)
        except uw.UWError:
            fresh = None
        if fresh:
            ratio = ask_side_ratio(fresh)
            side = flow_direction(fresh)
            if side and side != item["direction"]:
                print(f"  {t}: flow turned {side} against a "
                      f"{item['direction']} setup — skipped")
                continue
            if ratio and ratio < C.MIN_ASK_SIDE_RATIO:
                print(f"  {t}: buying pressure faded ({ratio*100:.0f}% at the "
                      f"ask, need {C.MIN_ASK_SIDE_RATIO*100:.0f}%) — skipped")
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
        move = tech["expected_move"]
        picks = pick_contracts_by_budget(chain, item["direction"], tech["close"],
                                         expected_move=move,
                                         atr=tech.get("atr", 0.0))
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
                   "flow_reason": (flow_reason(fresh, item["direction"]) if fresh
                                   else "كسر مؤكد على فريم 15د بعد تدفق خيارات"),
                   "news": [], "time_riyadh": now_riyadh()}
        # The chain was only ever built in scanner.to_payload, so an alert that
        # came through the watchlist — the path Salem actually wants — arrived
        # without the reasoning.
        payload["reasoning"] = reasoning.chain(payload)
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


def mark_paper():
    """Advance the paper book on the same 5-minute beat as the monitor.

    A 0DTE paper position that is not marked before 15:30 never closes, and an
    unclosed position is not a result. This runs whether or not Salem is at the
    screen, which is the only way a paper month finishes.
    """
    try:
        closed = paper.mark(verbose=True)
    except Exception as e:                      # never take the monitor down
        print(f"paper mark failed: {e}")
        return 0
    return len(closed)


def send_paper_daily():
    """After the bell, once. The monitor is the only thing still running then.

    It has to fire on a closed market, so main()'s market-open guard cannot
    cover it — a summary that only sends while the market is open would never
    send at all.
    """
    now = market.now_et()
    if now.weekday() >= 5 or market.is_holiday(now):
        return False
    if now.time() < market.closes_at(now):
        return False                        # bell has not gone yet
    try:
        return paper.send_daily()
    except Exception as e:                  # never take the monitor down
        print(f"paper daily failed: {e}")
        return False


def main(dry_run=False):
    # Both of these have to run on a CLOSED market. A position opened at 15:29
    # is still being marked at 15:44, and a summary that only sends while the
    # market is open would never send at all.
    mark_paper()
    if not dry_run and send_paper_daily():
        print("paper daily summary sent")
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
