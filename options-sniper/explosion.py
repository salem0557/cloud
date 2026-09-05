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


def tick(price):
    """Options quote in $0.01 below $3.00 and $0.05 above. This is why a mid
    is not always a price you can enter: on a contract quoted 0.01 x 0.02 the
    mid is 0.015, and no exchange will accept that order. You pay 0.02."""
    return 0.01 if price < 3.0 else 0.05


def to_tick(price, up):
    """Round to a tradeable increment — up when paying, down when receiving."""
    t = tick(price)
    n = price / t
    return round((int(n) + 1) * t if up and n % 1 else int(n) * t, 4)


def fill_price(bid, ask, model, buying):
    """What a fill is worth under each assumption.

    high  the day's print. Somebody got it; it is not an order you can place.
    mid   a limit at the midpoint, rounded to a tradeable tick — against you,
          because a mid that is not on the tick grid is not available.
    bid   hit the bid to sell, lift the ask to buy. The floor, not the norm.
    """
    if model == "bid":
        return ask if buying else bid
    if model == "mid":
        mid = (bid + ask) / 2
        return to_tick(mid, up=buying)
    return ask if buying else max(bid, ask)


def forward_multiple(rows, i, horizon, model="mid"):
    """What a buy at row i was worth over the next `horizon` days.

    The exit assumption is the whole argument about the "cheap contracts
    explode" result, so it is a parameter rather than a decision made here.
    Crediting the day's HIGH flatters it; hitting the BID punishes it twice,
    since the entry already paid the full ask. A tick-rounded MID sits between
    and is what a limit order actually reaches on a contract with a book.

    On a $0.02 contract none of that helps: the spread is half the price and
    the mid is not on the tick grid, so the entry is the ask either way.
    """
    entry = fill_price(rows[i]["bid"], rows[i]["ask"] or rows[i]["last"],
                       model, buying=True)
    if entry <= 0:
        return None
    window = rows[i + 1:i + 1 + horizon]
    if not window:
        return None

    def out(r):
        if model == "high":
            return (r["high"] or r["last"]) or 0.0
        return fill_price(r["bid"], r["ask"] or r["last"], model, buying=False)

    peak = max(out(r) for r in window)
    end = out(window[-1])
    spread = (rows[i]["ask"] or 0) - (rows[i]["bid"] or 0)
    return {
        "entry": entry,
        "entry_bid": rows[i]["bid"],
        "spread_pct": round(spread / entry * 100, 1) if entry else 0.0,
        "peak_multiple": round(peak / entry, 2),
        "end_multiple": round(end / entry, 2) if end else 0.0,
        "days_to_peak": next((n for n, r in enumerate(window, 1)
                              if out(r) >= peak), len(window)),
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
        "spread_pct": round((r["ask"] - r["bid"]) / r["ask"] * 100, 1)
        if r["ask"] else None,
    }


def _dte(expiry, on_date):
    try:
        return (datetime.date.fromisoformat(expiry)
                - datetime.date.fromisoformat(on_date)).days
    except (TypeError, ValueError):
        return None


def combinations(obs, feats, threshold, min_n):
    """Marginals cannot answer the question they raise.

    If cheap contracts explode 31% of the time and short-dated ones 20%, the
    cheap AND short-dated intersection could be 50% or it could be 15% —
    nothing in the per-feature tables distinguishes those. Pairs are measured
    directly.
    """
    out = []
    for a in range(len(feats)):
        for b in range(a + 1, len(feats)):
            fa, fb = feats[a], feats[b]
            groups = defaultdict(list)
            for o in obs:
                key = (bucket(fa, o["features"].get(fa)),
                       bucket(fb, o["features"].get(fb)))
                groups[key].append(o)
            for key, rows in groups.items():
                if len(rows) >= min_n:
                    out.append((" + ".join(key), summarise(rows, threshold)))
    return sorted(out, key=lambda kv: -kv[1]["realised_avg"])


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
    if name == "spread_pct":
        return f"spread={'<10%' if value < 10 else '10-25%' if value < 25 else '25-50%' if value < 50 else '50%+'}"
    if name == "vol_vs_avg":
        return f"vol/avg={'<1x' if value < 1 else '1-3x' if value < 3 else '3-10x' if value < 10 else '10x+'}"
    return f"{name}={value}"


FEATURES = ["price", "spread_pct", "vol_oi", "ask_share", "sweep_share",
            "dte", "iv", "vol_vs_avg"]


def scan_contract(symbol, meta, horizon, min_price, max_price,
                  model="mid", max_spread_pct=None):
    rows = uw.contract_history(symbol)      # raises; the caller reports why
    if len(rows) < 3:
        return []
    out = []
    for i in range(len(rows) - 1):
        price = rows[i]["ask"] or rows[i]["last"]
        if not (min_price <= price <= max_price):
            continue
        if model != "high" and rows[i]["bid"] <= 0:
            continue                # no bid means no exit; not a tradeable entry
        fwd = forward_multiple(rows, i, horizon, model)
        if not fwd:
            continue
        if max_spread_pct is not None and fwd["spread_pct"] > max_spread_pct:
            continue
        out.append({"symbol": symbol, "date": rows[i]["date"],
                    "side": (meta.get("type") or "").lower(),
                    **fwd, "features": features(rows, i, meta)})
    return out


def realised(o, take_multiple):
    """What a rule that sells at the first touch of `take_multiple` actually got.

    This is the number the peak columns cannot give. A 31% touch rate says
    nothing about profit on its own: the other 69% do not return zero, they
    return whatever the contract was worth at the end of the window, and the
    UBER contract Salem sent ended at $0.01 after touching 17x. Selling at a
    fixed multiple is the simplest rule that closes that gap, so it is the one
    measured.

    Still optimistic — the touch is credited at the target price, and a real
    fill on a contract this wide is worse.
    """
    return take_multiple if o["peak_multiple"] >= take_multiple else o["end_multiple"]


def summarise(obs, threshold, take=None):
    """Includes `contracts` — how many DISTINCT contracts the rows came from.

    Entry days are not independent observations. A 10-day forward window
    starting on Monday shares nine of its ten days with the one starting
    Tuesday, so a contract that ran once contributes a win on every entry day
    it had. At roughly 13 entry days per contract, a bucket showing n=104 can
    be eight contracts, and a single explosion can carry the whole bucket.
    The contract count is the sample size that matters.
    """
    if not obs:
        return {"count": 0}
    wins = [o for o in obs if o["peak_multiple"] >= threshold]
    peaks = sorted(o["peak_multiple"] for o in obs)
    take = take or threshold
    got = [realised(o, take) for o in obs]
    syms = {o.get("symbol") for o in obs if o.get("symbol")}
    # per contract: its best entry day, so one explosion counts once
    by_sym = defaultdict(list)
    for o in obs:
        by_sym[o.get("symbol")].append(realised(o, take))
    per_contract = [statistics.mean(v) for v in by_sym.values()] or [0.0]
    return {
        "contracts": len(syms),
        "per_contract_avg": round(statistics.mean(per_contract), 3),
        # what a sell-at-`take` rule returned per dollar staked. Below 1.0 the
        # bucket loses money however impressive its touch rate looks.
        "realised_avg": round(statistics.mean(got), 3),
        "realised_median": round(statistics.median(got), 3),
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
    model = args.fills
    label = {"high": "the day's print (not an order you can place)",
             "mid": "a tick-rounded limit at the midpoint",
             "bid": "hitting the bid (the floor)"}[model]
    print(f"Finding candidates: OTM, {args.min_dte}-{args.max_dte} DTE, "
          f"${args.min_price}-${args.max_price}")
    print(f"Fills: {model} — {label}"
          + (f", max spread {args.max_spread}%" if args.max_spread else ""))
    if args.date:
        print(f"Screened as of {args.date} — out-of-sample against today's run")
    print()
    try:
        raw_pool = uw.screen_contracts(
            is_otm="true", min_dte=args.min_dte, max_dte=args.max_dte,
            min_volume=args.min_volume, type=args.type, limit=250,
            date=args.date)
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
                                args.min_price, args.max_price,
                                model=model, max_spread_pct=args.max_spread)
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

    print(f"\n  SELLING AT {args.multiple}x, holding the rest to the window's end:")
    print(f"    returned per $1 staked : ${overall['realised_avg']}")
    print(f"    break-even needs       : {round(100 / args.multiple, 1)}% "
          f"touching {args.multiple}x")

    # How much of that number is the fill assumption rather than the setup?
    print(f"\n  Same entries under the other fill models:")
    for alt in ("high", "mid", "bid"):
        if alt == model:
            continue
        alt_obs = []
        for c in pool:
            try:
                alt_obs += scan_contract(c["option_symbol"], c, args.horizon,
                                         args.min_price, args.max_price,
                                         model=alt, max_spread_pct=args.max_spread)
            except uw.UWError:
                continue
        if alt_obs:
            s = summarise(alt_obs, args.multiple)
            print(f"    --fills {alt:<4} : ${s['realised_avg']} per $1, "
                  f"{s['explosion_rate']}% touching, n={s['count']}")

    # Calls against puts. A long-options result measured over a rising stretch
    # looks profitable whatever the filters say, because every call benefits.
    # If calls pay and puts do not, the finding is the market's direction, not
    # the setup's. Both sides paying is the only reading that survives.
    calls = [o for o in obs if o.get("side") == "call"]
    puts = [o for o in obs if o.get("side") == "put"]
    if calls and puts:
        cs, ps = summarise(calls, args.multiple), summarise(puts, args.multiple)
        print(f"\n  Calls vs puts — the period-bias check:")
        print(f"    calls : ${cs['realised_avg']} per $1, {cs['explosion_rate']}% "
              f"touching, {cs['contracts']} contracts")
        print(f"    puts  : ${ps['realised_avg']} per $1, {ps['explosion_rate']}% "
              f"touching, {ps['contracts']} contracts")
        # "> 1.0" is too crude a test. A side sitting a few percent above the
        # stake on a dozen contracts has not shown anything; it has failed to
        # lose. Requiring a real margin and a real sample keeps the check from
        # blessing its own result.
        def verdict(s):
            if s["contracts"] < C.MIN_CONTRACTS:
                return "too thin to read"
            if s["realised_avg"] >= 1.0 + C.SIDE_EDGE_MARGIN:
                return "pays"
            if s["realised_avg"] >= 1.0 - C.SIDE_EDGE_MARGIN:
                return "flat — not losing, but not an edge"
            return "loses"

        cv, pv = verdict(cs), verdict(ps)
        print(f"    calls: {cv}   puts: {pv}")
        if cv == "pays" and pv == "pays":
            print("    Both sides pay — not simply a rising market.")
        elif cv == "pays" and pv.startswith("flat"):
            print("    Calls carry the result and puts merely hold their value.\n"
                  "    In a rising stretch buying puts should LOSE, so flat puts "
                  "say the\n    result is not pure drift — but the edge that is "
                  "measurable here is\n    in calls alone. Suggestive, not "
                  "settled. Re-run with --date over an\n    earlier stretch to "
                  "test it out of sample.")
        elif cv == "pays":
            print("    Only calls pay while puts lose: on this sample that is "
                  "the market\n    going up, not an edge in the setup.")
        elif pv == "pays":
            print("    Only puts pay — the mirror of the same problem.")
        else:
            print("    Neither side shows an edge.")

    base = overall["realised_avg"]
    print(f"\n{'═' * 64}\nWhat separates the ones that ran")
    print(f"(measured against the run's own average of ${base}, not against "
          f"$1.00 —\n almost everything clears $1.00 when the overall does)\n")
    for feat in FEATURES:
        groups = defaultdict(list)
        for o in obs:
            groups[bucket(feat, o["features"].get(feat))].append(o)
        rows = [(k, summarise(v, args.multiple)) for k, v in groups.items()
                if len(v) >= C.BASE_RATE_MIN_SAMPLE]
        if not rows:
            continue
        print(f"  {feat}")
        for k, s in sorted(rows, key=lambda kv: -kv[1]["realised_avg"]):
            # measured against the run's own average, not against $1.00. Almost
            # every bucket clears $1.00 when the overall does; the question is
            # whether a filter beats taking everything.
            lift = s["realised_avg"] / base if base else 0
            mark = ("  ← beats the average" if lift >= 1.15
                    else ("  (thin)" if s["contracts"] < C.MIN_CONTRACTS else ""))
            print(f"    ${s['realised_avg']:>5}/$1  {lift:>4.2f}x avg  "
                  f"{s['explosion_rate']:>5}% hit  n={s['count']:>5} "
                  f"({s['contracts']:>3} contracts)  {k}{mark}")
        print()

    print(f"{'═' * 64}\nBest pairs (>= {C.BASE_RATE_MIN_SAMPLE} samples), "
          f"by what they actually returned:\n")
    pairs = combinations(obs, FEATURES, args.multiple, C.BASE_RATE_MIN_SAMPLE)
    for k, s in pairs[:12]:
        lift = s["realised_avg"] / base if base else 0
        mark = ("  ← beats the average" if lift >= 1.15
                else ("  (thin)" if s["contracts"] < C.MIN_CONTRACTS else ""))
        print(f"  ${s['realised_avg']:>5}/$1  {lift:>4.2f}x avg  "
              f"{s['explosion_rate']:>5}% hit  n={s['count']:>5} "
              f"({s['contracts']:>3} contracts)  {k}{mark}")
    thin = [k for k, s in pairs[:12] if s["contracts"] < C.MIN_CONTRACTS]
    if thin:
        print(f"\n  {len(thin)} of the top 12 rest on fewer than "
              f"{C.MIN_CONTRACTS} distinct contracts. Entry days on one "
              "contract\n  overlap almost completely, so those are closer to "
              "a handful of events\n  than to the sample size shown.")
    print()

    payload = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "params": vars(args), "overall": overall,
        "pairs": {k: s for k, s in pairs[:40]},
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
    print("\nStill optimistic in one way under every model: the whole "
          "position is assumed to fill at the touch, and size moves a thin "
          "book. Re-run with --fills high / bid to see how much of any result "
          "is the fill assumption rather than the setup.")
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
    ap.add_argument("--fills", default="mid", choices=["high", "mid", "bid"],
                    help="high: the day's print, not an order you can place. "
                         "mid: a tick-rounded limit, what a real order reaches. "
                         "bid: hit the bid, the floor. Default mid.")
    ap.add_argument("--max-spread", type=float, default=None,
                    help="skip entries wider than this %% spread at entry")
    ap.add_argument("--date", default=None,
                    help="screen as of this date (YYYY-MM-DD) instead of today. "
                         "Every result so far comes from ONE screen of what is "
                         "interesting now; an earlier date gives a different "
                         "population and is the out-of-sample test.")
    a = ap.parse_args()
    try:
        sys.exit(main(a))
    except uw.UWError as e:
        print("UW error:", e, file=sys.stderr)
        sys.exit(2)
