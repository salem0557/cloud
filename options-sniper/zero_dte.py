"""What Salem actually trades: a same-day contract, sold at +40% within minutes.

Every earlier run measured the wrong thing. explosion.py reads
/option-contract/{id}/historic, which serves one row per DAY, so a 0DTE
contract appears there as a single row and cannot be measured at all. What it
did measure was 98-99% contracts of 22+ days to expiry, held five days, sold at
2x. Salem holds for minutes and sells at +40%. The four screens that came back
losing were describing a strategy that is not his.

This reads /option-contract/{id}/intraday — one minute per row — and answers
the question in his terms:

    buy a contract expiring today, at some minute of the session
    sell the moment it is up TAKE%
    cut it if it is down STOP%
    and if neither happens, get out after MAX_HOLD minutes, or at the hard
    exit before the close, because a 0DTE contract expires worthless tonight

The break-even is printed rather than assumed: taking +40% against a -25% stop
needs a hit rate above 38.5%, which is a very different bar from the 50% a 2x
target needed. Whether the real rate clears it is what this measures.
"""
import argparse
import json
import statistics
import sys
from collections import defaultdict

import venv_boot

venv_boot.ensure(["requests"])

import config as C
import uw
from explosion import fill_price          # noqa: E402  the same fill rules


HARD_EXIT = "15:30"          # a 0DTE contract is not held into the close


def minute_of(row):
    """'HH:MM' in Eastern, from whatever timestamp the row carries."""
    t = row.get("time") or ""
    if "T" in t:
        t = t.split("T", 1)[1]
    return t[:5]


def _take_filled(row, target, model):
    """Did the target actually become sellable in this minute?

    Under `bid` the sale has to reach the BID, because that is where a seller
    gets out. Under the looser models a print at or above the target is enough.
    """
    if model == "bid" and row["bid"] > 0:
        return row["bid"] >= target
    return row["high"] >= target


def entry_exit(rows, i, take_pct, stop_pct, max_hold, model, hard_exit):
    """One trade: buy at row i, then walk forward minute by minute.

    The order of the checks inside a minute is deliberately pessimistic. A
    minute whose range covers BOTH the target and the stop is counted as a
    stop: an OHLC bar cannot say which came first, and assuming the good one is
    how a backtest invents an edge.

    Two assumptions still favour the trade, and neither can be removed from
    minute bars: the target is credited at exactly the limit price, and the
    stop at exactly the stop, so slippage through a fast 0DTE move is not
    charged. Both make the result better than reality, never worse.
    """
    entry = fill_price(rows[i]["bid"], rows[i]["ask"] or rows[i]["close"],
                       model, buying=True)
    if entry <= 0:
        return None
    target = entry * (1 + take_pct / 100.0)
    stop = entry * (1 - stop_pct / 100.0)

    seen = 0
    for r in rows[i + 1:i + 1 + max_hold]:
        if minute_of(r) >= hard_exit:
            break
        seen += 1
        if r["low"] > 0 and r["low"] <= stop:        # both in one bar -> stop
            return {"exit": stop, "entry": entry, "minutes": seen,
                    "why": "stop"}
        if _take_filled(r, target, model):
            return {"exit": target, "entry": entry, "minutes": seen,
                    "why": "take"}

    if not seen:
        return None
    last = rows[i + seen]
    out = fill_price(last["bid"], last["ask"] or last["close"], model,
                     buying=False)
    return {"exit": out if out > 0 else last["close"], "entry": entry,
            "minutes": seen, "why": "timeout"}


def break_even(take_pct, stop_pct):
    """The hit rate this take/stop pair needs, if every loser hits the stop.

    Optimistic in Salem's favour: a timeout that ends flat is neither, and a
    gap through the stop is worse than the stop. It is the floor of the bar,
    not the bar."""
    return stop_pct / (take_pct + stop_pct) * 100.0


def bucket(name, value):
    if value is None:
        return f"{name}=?"
    if name == "minute":
        h = int(value[:2]) if value[:2].isdigit() else 0
        return ("time=09:30-10" if value < "10:00" else
                "time=10-11:30" if value < "11:30" else
                "time=11:30-14" if value < "14:00" else "time=14-15:30")
    if name == "price":
        cost = value * 100
        return f"budget={'$50' if cost <= 50 else '$100' if cost <= 100 else '$200' if cost <= 200 else '>$200'}"
    if name == "spread_pct":
        return f"spread={'<5%' if value < 5 else '5-15%' if value < 15 else '15-30%' if value < 30 else '30%+'}"
    if name == "minute_volume":
        return f"minvol={'<10' if value < 10 else '10-50' if value < 50 else '50-200' if value < 200 else '200+'}"
    if name == "ask_share":
        return f"ask={'<40%' if value < 0.4 else '40-60%' if value < 0.6 else '60-80%' if value < 0.8 else '80%+'}"
    if name == "moneyness":
        return f"otm={'<0.5%' if value < 0.5 else '0.5-1.5%' if value < 1.5 else '1.5-3%' if value < 3 else '3%+'}"
    return f"{name}={value}"


FEATURES = ["minute", "price", "spread_pct", "minute_volume", "ask_share",
            "moneyness"]


def features(rows, i, meta):
    """Only what a trader could see at the moment of the buy."""
    r = rows[i]
    price = r["ask"] or r["close"]
    spread_pct = None
    if r["bid"] > 0 and r["ask"] > 0:
        mid = (r["bid"] + r["ask"]) / 2
        spread_pct = (r["ask"] - r["bid"]) / mid * 100 if mid > 0 else None
    sided = (r["ask_volume"] or 0) + (r["bid_volume"] or 0)
    spot, strike = meta.get("stock_price", 0), meta.get("strike", 0)
    moneyness = None
    if spot > 0 and strike > 0:
        gap = (strike - spot) if meta.get("type") == "call" else (spot - strike)
        moneyness = round(gap / spot * 100, 3)
    return {
        "minute": minute_of(r),
        "price": price,
        "spread_pct": spread_pct,
        "minute_volume": r["volume"],
        "ask_share": (r["ask_volume"] / sided) if sided else None,
        "moneyness": moneyness,
    }


def scan_contract(rows, meta, args, model):
    """Every minute of this session that could have been an entry."""
    out = []
    for i in range(len(rows) - 1):
        price = rows[i]["ask"] or rows[i]["close"]
        if not (args.min_price <= price <= args.max_price):
            continue
        if minute_of(rows[i]) >= args.hard_exit:
            continue
        trade = entry_exit(rows, i, args.take, args.stop, args.max_hold,
                           model, args.hard_exit)
        if not trade:
            continue
        trade["multiple"] = trade["exit"] / trade["entry"]
        trade["symbol"] = meta["option_symbol"]
        trade.update(features(rows, i, meta))
        out.append(trade)
    return out


def summarise(obs, take, stop, label=""):
    if not obs:
        print("  no trades")
        return {}
    avg = statistics.mean(o["multiple"] for o in obs)
    took = [o for o in obs if o["why"] == "take"]
    stopped = [o for o in obs if o["why"] == "stop"]
    timed = [o for o in obs if o["why"] == "timeout"]
    n_contracts = len({o["symbol"] for o in obs})
    be = break_even(take, stop)
    hit = len(took) / len(obs) * 100

    print(f"  {len(obs)} trades across {n_contracts} contracts{label}")
    print(f"    hit +{take:.0f}%           : {hit:.1f}%  "
          f"({len(took)} of {len(obs)})")
    print(f"    stopped out -{stop:.0f}%   : {len(stopped)/len(obs)*100:.1f}%")
    print(f"    timed out          : {len(timed)/len(obs)*100:.1f}%")
    print(f"    returned per $1    : ${avg:.3f}")
    print(f"    break-even needs   : {be:.1f}% hitting the target")
    print(f"    VERDICT            : {'EDGE' if hit > be and avg > 1.0 else 'no edge'}"
          f" — {hit:.1f}% vs {be:.1f}% needed")
    if took:
        print(f"    median minutes to target : "
              f"{statistics.median(o['minutes'] for o in took):.0f}")
    return {"avg": avg, "hit": hit, "n": len(obs), "contracts": n_contracts}


def report_features(obs, avg):
    print(f"\n  What separates the winners (run average ${avg:.3f})\n")
    for name in FEATURES:
        groups = defaultdict(list)
        for o in obs:
            groups[bucket(name, o.get(name))].append(o)
        rows = []
        for key, items in groups.items():
            if len(items) < 10:
                continue
            a = statistics.mean(x["multiple"] for x in items)
            hit = sum(1 for x in items if x["why"] == "take") / len(items) * 100
            rows.append((a, key, len(items), len({x["symbol"] for x in items}),
                         hit))
        if not rows:
            continue
        print(f"  {name}")
        for a, key, n, cs, hit in sorted(rows, reverse=True):
            mark = "  <- beats the average" if a > avg * 1.15 else ""
            thin = "  (thin)" if cs < 10 else ""
            print(f"    ${a:6.3f}/$1  {a/avg:.2f}x avg  {hit:5.1f}% hit  "
                  f"n={n:5d} ({cs:3d} contracts)  {key}{thin}{mark}")
        print()


def run_one(date, args, model):
    print(f"\n{'#'*64}\n# 0DTE session {date}\n{'#'*64}")
    print(f"Buy a contract expiring {date}, sell at +{args.take:.0f}%, "
          f"cut at -{args.stop:.0f}%,")
    print(f"give up after {args.max_hold} minutes, out by {args.hard_exit}.")
    print(f"Fills: {model}. Premium ${args.min_price}-${args.max_price} "
          f"(${args.min_price*100:.0f}-${args.max_price*100:.0f} a contract)\n")
    # expiry_dates, not min_dte/max_dte: the screener measures dte from TODAY,
    # so asking for dte 0 on a past session matched nothing on every date.
    try:
        pool = uw.screen_contracts(is_otm="true", expiry_dates=[date],
                                   min_volume=args.min_volume, type=args.type,
                                   limit=250, date=date)
    except uw.UWError as e:
        print(f"Screener failed: {e}")
        return None
    print(f"Screener returned {len(pool)} contracts expiring that day")
    if not pool:
        # Say what the session actually held instead of guessing at expiry
        # calendars — the last guess sent Salem looking at the wrong thing.
        try:
            any_c = uw.screen_contracts(is_otm="true", min_volume=args.min_volume,
                                        limit=250, date=date)
        except uw.UWError:
            any_c = []
        if not any_c:
            print(f"That session returned no contracts at all — {date} is "
                  "probably not a trading day, or is outside the plan's "
                  "history.")
        else:
            seen = sorted({c.get("expiry") for c in any_c if c.get("expiry")})
            print(f"  {len(any_c)} contracts traded that session, expiring: "
                  f"{', '.join(seen[:8])}"
                  + (f" (+{len(seen)-8} more)" if len(seen) > 8 else ""))
            print(f"  None expire on {date} itself, so that session had no "
                  "0DTE contracts above the volume floor. Pick a date from "
                  "the list above.")
        return None

    pool = [c for c in pool
            if args.min_price <= c["price"] <= args.max_price][:args.contracts]
    if not pool:
        print("None inside the premium band.")
        return None
    print(f"{len(pool)} inside the budget. One request each.\n")

    tapes, failures, shape_shown = [], [], False
    for n, c in enumerate(pool, 1):
        try:
            rows = uw.contract_intraday(c["option_symbol"], date=date)
        except uw.UWError as err:
            failures.append(f"{c['option_symbol']}: {err}")
            if len(failures) == 1:
                print(f"  ! {c['option_symbol']} intraday failed: {err}")
            continue
        if len(rows) < 5:
            continue
        if not shape_shown:
            # UW does not publish this row shape; report what actually arrived
            print(f"  row fields: {', '.join(rows[0]['_keys'][:14])}")
            quoted = sum(1 for r in rows if r["bid"] > 0 and r["ask"] > 0)
            print(f"  {len(rows)} minutes, {quoted} carry a bid/ask. "
                  + ("Fills use the real quote.\n" if quoted > len(rows) / 2
                     else "NO QUOTES — fills fall back to the trade price, so "
                          "the spread is NOT charged and every number below "
                          "is optimistic.\n"))
            shape_shown = True
        tapes.append((c, rows))
        if n % 20 == 0:
            print(f"  {n}/{len(pool)} contracts pulled")

    if failures:
        print(f"\n  {len(failures)} contracts had no intraday data")
    if not tapes:
        print("\nNo usable tapes. Either the band is empty or intraday data "
              "is not served on this plan.")
        return None

    obs = [t for c, rows in tapes for t in scan_contract(rows, c, args, model)]
    if not obs:
        print("\nNo trades inside the premium band.")
        return None

    print(f"\n{'='*64}")
    stats = summarise(obs, args.take, args.stop)

    # the same entries under the other two fill assumptions, no new requests
    print("\n  Same entries under the other fill models:")
    for other in ("high", "mid", "bid"):
        if other == model:
            continue
        alt = [t for c, rows in tapes for t in scan_contract(rows, c, args, other)]
        if not alt:
            continue
        a = statistics.mean(o["multiple"] for o in alt)
        h = sum(1 for o in alt if o["why"] == "take") / len(alt) * 100
        print(f"    --fills {other:4s}: ${a:.3f} per $1, {h:.1f}% hit, n={len(alt)}")

    report_features(obs, stats["avg"])
    return {"date": date, **stats}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dates", help="comma-separated sessions to test")
    p.add_argument("--date", help="one session (YYYY-MM-DD)")
    p.add_argument("--take", type=float, default=40.0, help="take profit %%")
    p.add_argument("--stop", type=float, default=25.0, help="stop loss %%")
    p.add_argument("--max-hold", type=int, default=15,
                   help="minutes to give the trade before getting out")
    p.add_argument("--hard-exit", default=HARD_EXIT)
    p.add_argument("--min-price", type=float, default=0.05)
    p.add_argument("--max-price", type=float, default=2.00)
    p.add_argument("--min-volume", type=int, default=200)
    p.add_argument("--contracts", type=int, default=60)
    p.add_argument("--type", default=None, choices=["call", "put"])
    p.add_argument("--fills", default="mid", choices=["high", "mid", "bid"])
    args = p.parse_args(argv)

    dates = ([d.strip() for d in args.dates.split(",") if d.strip()]
             if args.dates else [args.date] if args.date else [None])
    results = [r for r in (run_one(d, args, args.fills) for d in dates) if r]
    if not results:
        return 1

    if len(results) > 1:
        print(f"\n{'='*64}\nREPLICATION across {len(results)} sessions")
        for r in results:
            print(f"  {r['date']}: ${r['avg']:.3f}/$1, {r['hit']:.1f}% hit, "
                  f"{r['contracts']} contracts")
        be = break_even(args.take, args.stop)
        wins = [r for r in results if r["hit"] > be and r["avg"] > 1.0]
        print(f"\n  {len(wins)} of {len(results)} sessions cleared break-even "
              f"({be:.1f}%).")
        if len(wins) < len(results):
            print("  A rule that works on some sessions and not others is a "
                  "coin flip with extra steps.")

    out = C.DATA_DIR / "zero_dte.json"
    try:
        out.write_text(json.dumps(
            [{k: v for k, v in r.items() if k != "obs"} for r in results],
            indent=2))
        print(f"\nWrote {out}")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
