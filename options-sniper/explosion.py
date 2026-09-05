"""What precedes a contract multiplying, measured on contracts rather than stocks.

Salem's actual goal, stated plainly after the first backtest: catch a cheap
contract before it runs — the UBER 76C that went from about $0.05 to $0.87 and
back to $0.01. He added "the stock does not matter to me".

The stock does matter — that contract moved because UBER moved toward 76, and
no rule can see an option explode without looking at its underlying. But the
criticism underneath was correct, and it invalidates what the first backtest
measured. That test asked whether the stock reached level + 1.5 ATR, and
judged the rule against the 40% hit rate that geometry needs. A 1.5 ATR move
lifts a far-OTM contract by a fraction; the 17x came from a move several times
larger. The system was optimising for the wrong outcome, and 37.8% was a
verdict on a question Salem never asked.

So this measures the right one. For every day a contract traded, it asks what
that contract was worth over the following days, and calls a multiple of
EXPLOSION_MULTIPLE an explosion. Then it compares what was observable on the
entry day — implied volatility, open interest, volume against open interest,
how much of that volume was bought at the ask, sweep share, days to expiry,
how far out of the money — between the ones that ran and the ones that died.

Two things this cannot tell you, and both matter more than the hit rate:

  the exit   the UBER contract ended at $0.01. Everything measured here is a
             maximum reached along the way, not a result. A rule that catches
             every explosion still loses if it holds to expiry.
  the cost   entries are taken at the ask and exits credited at the high, and
             a real fill sits inside a spread that is wide on exactly these
             contracts.

Run it before believing any of it: python explosion.py --help
"""
import argparse
import datetime
import json
import statistics
import sys
from collections import defaultdict

import venv_boot

venv_boot.ensure(["requests"])

import config as C
import uw


def forward_multiple(rows, i, horizon):
    """Best multiple a buy at row i's ask reached within `horizon` days.

    Entry at the ask because that is what a taker pays; the ask is also what
    the budget filter prices contracts on, so the two agree.
    """
    entry = rows[i]["ask"] or rows[i]["last"]
    if entry <= 0:
        return None
    window = rows[i + 1:i + 1 + horizon]
    if not window:
        return None
    peak = max((r["high"] or r["last"]) for r in window)
    end = window[-1]["last"] or window[-1]["bid"]
    return {
        "entry": entry,
        "peak_multiple": round(peak / entry, 2),
        "end_multiple": round(end / entry, 2) if end else 0.0,
        "days_to_peak": next((n for n, r in enumerate(window, 1)
                              if (r["high"] or r["last"]) >= peak), len(window)),
    }


def features(rows, i, meta):
    """Only what was visible on the entry day — no future leakage."""
    r = rows[i]
    prior = rows[max(0, i - 5):i]
    oi = r["open_interest"] or 0
    vol = r["volume"] or 0
    ask_v, bid_v = r["ask_volume"] or 0, r["bid_volume"] or 0
    sided = ask_v + bid_v
    avg_vol = statistics.mean([p["volume"] or 0 for p in prior]) if prior else 0
    dte = _dte(meta.get("expiry"), r["date"])
    return {
        "price": r["ask"] or r["last"],
        "iv": r["iv"],
        "vol_oi": round(vol / oi, 2) if oi else 0.0,
        "vol_vs_avg": round(vol / avg_vol, 2) if avg_vol else 0.0,
        "ask_share": round(ask_v / sided, 2) if sided else 0.0,
        "sweep_share": round((r["sweep_volume"] or 0) / vol, 2) if vol else 0.0,
        "open_interest": oi,
        "dte": dte,
    }


def _dte(expiry, on_date):
    try:
        return (datetime.date.fromisoformat(expiry)
                - datetime.date.fromisoformat(on_date)).days
    except (TypeError, ValueError):
        return None


# ── Buckets: the shapes a live scanner could actually filter on ──
def bucket(name, value):
    if value is None:
        return f"{name}=?"
    if name == "price":
        return f"price={'<0.10' if value < 0.10 else '0.10-0.50' if value < 0.50 else '0.50-1.50' if value < 1.5 else '1.50+'}"
    if name == "vol_oi":
        return f"vol/OI={'<0.5' if value < 0.5 else '0.5-2' if value < 2 else '2-5' if value < 5 else '5+'}"
    if name == "ask_share":
        return f"ask={'<40%' if value < 0.4 else '40-60%' if value < 0.6 else '60-80%' if value < 0.8 else '80%+'}"
    if name == "sweep_share":
        return f"sweep={'0' if value <= 0 else '<20%' if value < 0.2 else '20-50%' if value < 0.5 else '50%+'}"
    if name == "dte":
        return f"dte={'0-2' if value <= 2 else '3-7' if value <= 7 else '8-21' if value <= 21 else '22+'}"
    if name == "iv":
        return f"iv={'<50%' if value < 0.5 else '50-100%' if value < 1.0 else '100%+'}"
    if name == "vol_vs_avg":
        return f"vol/avg={'<1x' if value < 1 else '1-3x' if value < 3 else '3-10x' if value < 10 else '10x+'}"
    return f"{name}={value}"


FEATURES = ["price", "vol_oi", "ask_share", "sweep_share", "dte", "iv", "vol_vs_avg"]


def scan_contract(symbol, meta, horizon, min_price, max_price):
    rows = uw.contract_history(symbol)      # raises; the caller reports why
    if len(rows) < 3:
        return []
    out = []
    for i in range(len(rows) - 1):
        price = rows[i]["ask"] or rows[i]["last"]
        if not (min_price <= price <= max_price):
            continue
        fwd = forward_multiple(rows, i, horizon)
        if not fwd:
            continue
        out.append({"symbol": symbol, "date": rows[i]["date"],
                    **fwd, "features": features(rows, i, meta)})
    return out


def summarise(obs, threshold):
    if not obs:
        return {"count": 0}
    wins = [o for o in obs if o["peak_multiple"] >= threshold]
    peaks = sorted(o["peak_multiple"] for o in obs)
    return {
        "count": len(obs),
        "explosion_rate": round(len(wins) / len(obs) * 100, 1),
        "median_peak": round(statistics.median(peaks), 2),
        "p90_peak": round(peaks[int(len(peaks) * 0.9)], 2),
        "median_end": round(statistics.median(o["end_multiple"] for o in obs), 2),
        "median_days_to_peak": statistics.median(o["days_to_peak"] for o in obs),
        # what a flat bet on every one of these would have returned, if you
        # somehow sold each at its peak — the ceiling, not the result
        "avg_peak": round(statistics.mean(o["peak_multiple"] for o in obs), 2),
    }


def main(args):
    print(f"Finding candidates: OTM, {args.min_dte}-{args.max_dte} DTE, "
          f"${args.min_price}-${args.max_price}\n")
    try:
        raw_pool = uw.screen_contracts(
            is_otm="true", min_dte=args.min_dte, max_dte=args.max_dte,
            min_volume=args.min_volume, type=args.type, limit=250)
    except uw.UWError as e:
        print(f"Screener failed: {e}")
        print("If this is a 403, the plan does not serve "
              "/api/screener/option-contracts.")
        return 2
    print(f"Screener returned {len(raw_pool)} contracts")
    if not raw_pool:
        print("Nothing matched the screen itself. Loosen --min-volume or widen "
              "the DTE range. Note the screener reflects the last session, so "
              "a long weekend can thin it out.")
        return 1

    pool = [c for c in raw_pool
            if args.min_price <= c["price"] <= args.max_price][:args.contracts]
    if not pool:
        prices = sorted(c["price"] for c in raw_pool if c["price"] > 0)
        band = (f"cheapest ${prices[0]:.2f}, median ${prices[len(prices)//2]:.2f}, "
                f"dearest ${prices[-1]:.2f}") if prices else "all zero"
        print(f"None priced ${args.min_price}-${args.max_price} ({band}). "
              f"Adjust --min-price/--max-price.")
        return 1
    print(f"{len(pool)} contracts. Pulling each one's own history "
          f"(this is one request each)\n")

    obs, failures, empties = [], [], 0
    for n, c in enumerate(pool, 1):
        try:
            got = scan_contract(c["option_symbol"], c, args.horizon,
                                args.min_price, args.max_price)
        except uw.UWError as err:
            failures.append(f"{c['option_symbol']}: {err}")
            if len(failures) == 1:
                # say it once, immediately, instead of after 147 silent rounds
                print(f"  ! {c['option_symbol']} history failed: {err}")
            continue
        if not got:
            empties += 1
        obs += got
        if n % 25 == 0 or n == len(pool):
            print(f"  {n}/{len(pool)} contracts, {len(obs)} entry days so far")

    if not obs:
        print()
        if failures:
            print(f"{len(failures)} of {len(pool)} history requests failed. First: "
                  f"{failures[0]}")
            print("A 403 means the plan does not serve "
                  "/api/option-contract/{id}/historic.")
        else:
            print(f"Every contract returned history, but none had {3}+ days "
                  f"inside ${args.min_price}-${args.max_price}. These are new "
                  "contracts with a short tape — widen the price band or raise "
                  "--max-dte to reach older ones.")
        return 1
    if failures:
        print(f"\n({len(failures)} contracts had no history and were skipped)")

    overall = summarise(obs, args.multiple)
    print(f"\n{'═' * 64}")
    print(f"{overall['count']} entry days across {len(pool)} contracts")
    print(f"  reached {args.multiple}x within {args.horizon} days : "
          f"{overall['explosion_rate']}%")
    print(f"  median peak multiple  : {overall['median_peak']}x")
    print(f"  90th percentile peak  : {overall['p90_peak']}x")
    print(f"  median END multiple   : {overall['median_end']}x   "
          f"← held to the window's end, not sold at the peak")
    print(f"  median days to peak   : {overall['median_days_to_peak']}")

    print(f"\nBreak-even: buying every one of these needs a "
          f"{round(100 / args.multiple, 1)}% hit rate at {args.multiple}x "
          f"to return the stake.")

    print(f"\n{'═' * 64}\nWhat separates the ones that ran:\n")
    for feat in FEATURES:
        groups = defaultdict(list)
        for o in obs:
            groups[bucket(feat, o["features"].get(feat))].append(o)
        rows = [(k, summarise(v, args.multiple)) for k, v in groups.items()
                if len(v) >= C.BASE_RATE_MIN_SAMPLE]
        if not rows:
            continue
        print(f"  {feat}")
        for k, s in sorted(rows, key=lambda kv: -kv[1]["explosion_rate"]):
            print(f"    {s['explosion_rate']:>5}%  n={s['count']:>5}  "
                  f"median {s['median_peak']:>5}x  {k}")
        print()

    payload = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "params": vars(args), "overall": overall,
        "by_feature": {f: {k: summarise(v, args.multiple)
                           for k, v in defaultdict(
                               list, {bucket(f, o["features"].get(f)): []
                                      for o in obs}).items()}
                       for f in FEATURES},
    }
    for f in FEATURES:
        groups = defaultdict(list)
        for o in obs:
            groups[bucket(f, o["features"].get(f))].append(o)
        payload["by_feature"][f] = {k: summarise(v, args.multiple)
                                    for k, v in groups.items()
                                    if len(v) >= C.BASE_RATE_MIN_SAMPLE}
    C.EXPLOSION_FILE.write_text(json.dumps(payload, indent=2))
    print(f"Wrote {C.EXPLOSION_FILE}")
    print("\nTwo caveats this cannot measure: every multiple above is a PEAK "
          "reached along the way, not what you would have got — the UBER "
          "contract ended at $0.01 — and entries are priced at the ask with "
          "exits credited at the high, which no real fill matches.")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--multiple", type=float, default=5.0,
                    help="what counts as an explosion (default 5x)")
    ap.add_argument("--horizon", type=int, default=10,
                    help="trading days allowed to reach it")
    ap.add_argument("--min-price", type=float, default=0.02)
    ap.add_argument("--max-price", type=float, default=1.00)
    ap.add_argument("--min-dte", type=int, default=1)
    ap.add_argument("--max-dte", type=int, default=45)
    ap.add_argument("--min-volume", type=int, default=500)
    ap.add_argument("--contracts", type=int, default=150,
                    help="how many contracts to pull history for (1 request each)")
    ap.add_argument("--type", choices=["call", "put"], default=None)
    a = ap.parse_args()
    try:
        sys.exit(main(a))
    except uw.UWError as e:
        print("UW error:", e, file=sys.stderr)
        sys.exit(2)
