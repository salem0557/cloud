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
import datetime
import json
import statistics
import sys
from collections import Counter, defaultdict

import venv_boot

venv_boot.ensure(["requests"])

import config as C
import market
import regime
import uw
from explosion import fill_price          # noqa: E402  the same fill rules


HARD_EXIT = "15:30"          # a 0DTE contract is not held into the close


def minute_of(row):
    """'HH:MM' in Eastern, from whatever timestamp the row carries.

    It used to slice the string, which is what the docstring already claimed
    it did NOT do: UW sends UTC, so "15:30" as a hard exit was cutting entries
    at 11:30 New York, and the whole afternoon of every session was silently
    outside the test.
    """
    return market.et_minute(row.get("time") or "")


def trading_days(end, count):
    """The `count` weekdays ending at `end`, most recent first.

    Sample size is the thing that decides whether a result means anything. Four
    sessions cannot separate a rule from luck any better than "three wins out of
    five" could — and hand-listing twenty dates is how a date list quietly turns
    into a choice about which dates flatter the answer.

    Holidays are not filtered: the screener returns nothing for them and the
    session is skipped with a line saying so, which is honest and costs one
    request.
    """
    day = datetime.date.fromisoformat(end)
    out = []
    while len(out) < count:
        if day.weekday() < 5:
            out.append(day.isoformat())
        day -= datetime.timedelta(days=1)
    return out


def bar_key(minute):
    """The 15m bar a minute belongs to. 10:47 -> 10:45."""
    if not minute or len(minute) < 5:
        return ""
    try:
        h, m = int(minute[:2]), int(minute[3:5])
    except ValueError:
        return ""
    return f"{h:02d}:{(m // 15) * 15:02d}"


def measured_spread(rows):
    """This contract's real quoted width, measured from its own prints.

    Salem does not want a whitelist — UBER was a surprise nobody had on a list,
    and a fixed universe excludes exactly that. But the spread decides whether
    a small target is reachable, so it has to be measured per contract.

    The first attempt read nbbo_bid/nbbo_ask off the DAILY tape and returned a
    median of 200% on every session. 200% is what (ask-bid)/mid gives when the
    bid is zero — and the closing bid of a 0DTE contract that expired worthless
    IS zero. It was reading the quote after the contract was already dead.

    The minute tape carries no NBBO either, but it does carry premium and
    volume split by side. premium_ask_side / (volume_ask_side * 100) is the
    average price paid by buyers lifting the offer in that minute, and the
    bid-side pair is the average received by sellers hitting the bid. The gap
    between them is the spread, measured from prints that actually happened.

    Returns the median across the minutes that traded both sides, or None when
    too few did — never a default that would make a wide contract look
    tradeable.
    """
    widths = []
    for r in rows:
        a, b = r.get("ask_px") or 0, r.get("bid_px") or 0
        if a <= 0 or b <= 0 or a <= b:
            continue
        mid = (a + b) / 2
        widths.append((a - b) / mid * 100)
    if len(widths) < 5:
        return None
    return statistics.median(widths)


def build_gates(ticker, date, args):
    """-> {'HH:MM': signal} for every 15m bar of this session that passed.

    Returns None when the stock's bars cannot be had, so the caller can say
    the gates were skipped rather than silently measure the whole tape again.
    """
    try:
        bars, todays = regime.session_bars(ticker, date)
    except Exception:
        return None
    # 15 bars of level plus one to break it; anything less cannot signal.
    if len(bars) < 20 or not todays:
        return None
    out, reasons = {}, Counter()
    for i in todays:
        sig = regime.signal(bars, i)
        ok, why = regime.gate(sig, market.et_minute(bars[i].get("start_time")),
                              skip=args.skip_windows,
                              min_agree=args.min_agree)
        reasons[why if not ok else "PASS"] += 1
        if ok:
            out[bar_key(market.et_minute(bars[i].get("start_time")))] = sig
    return {"gates": out, "reasons": reasons, "bars": len(todays),
            "context": len(bars)}


def entry_exit(rows, i, take_pct, stop_pct, max_hold, spread_pct, hard_exit,
               slip_pct=0.0, fee=None):
    """One trade: buy at row i, then walk forward minute by minute.

    This endpoint serves NO bid/ask — only trade prices — so the spread cannot
    be measured and must be charged explicitly. `spread_pct` is the quoted
    width as a percentage of the mid; the buy pays half of it and the sale
    gives up the other half. At 0 the run assumes a free round trip, which on a
    0DTE contract is not a conservative assumption, it is a fictional one.

    Everything is computed in mid space: to net +40% AFTER paying to get out,
    the contract has to reach more than the entry times 1.4.

    The order of the checks inside a minute is deliberately pessimistic. A
    minute whose range covers both the target and the stop counts as a stop —
    an OHLC bar cannot say which came first, and assuming the good one is how
    a backtest invents a win rate.

    `slip_pct` charges the assumption the rest of this file cannot test: a
    0DTE contract in a fast move gaps THROUGH its stop, and the fill is worse
    than the level. It matters most to the configuration that looks best —
    a wide stop only pays if the stop actually holds — so it is a parameter
    rather than a silent zero. The target has no equivalent: a limit order
    fills at the limit or not at all.
    """
    half = spread_pct / 200.0
    mid_in = rows[i]["close"] or rows[i]["avg_price"]
    if mid_in <= 0:
        return None
    # Commission, per contract per side, converted to per-share because the
    # tape quotes per share. $0.65 on a $0.95 contract is 0.7% each way — a
    # third of the whole measured edge, and it had never been charged.
    fee_ps = (C.COMMISSION_PER_CONTRACT if fee is None else fee) / 100.0
    cost = mid_in * (1 + half) + fee_ps         # lift the offer, pay the fee
    out_factor = 1 - half                       # hit the bid on the way out
    # the target/stop are levels the MID must reach so that, after the bid
    # side of the spread and the exit fee, the trade nets the stated percent
    target = (cost * (1 + take_pct / 100.0) + fee_ps) / out_factor
    stop = (cost * (1 - stop_pct / 100.0) + fee_ps) / out_factor

    seen = 0
    for r in rows[i + 1:i + 1 + max_hold]:
        if minute_of(r) >= hard_exit:
            break
        seen += 1
        if r["low"] > 0 and r["low"] <= stop:        # both in one bar -> stop
            filled = stop * (1 - slip_pct / 100.0)
            return {"exit": max(filled, r["low"]) * out_factor - fee_ps,
                    "entry": cost, "minutes": seen, "why": "stop"}
        if r["high"] >= target:
            return {"exit": target * out_factor - fee_ps, "entry": cost,
                    "minutes": seen, "why": "take"}

    if not seen:
        return None
    last = rows[i + seen]
    mid_out = last["close"] or last["avg_price"]
    return {"exit": max(max(mid_out, 0.0) * out_factor - fee_ps, 0.0),
            "entry": cost, "minutes": seen, "why": "timeout"}


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
    if name == "window":
        return f"when={value}"
    if name == "agree":
        return f"committee={value}/4"
    if name == "chase":
        return f"chase={'0-0.1' if value <= 0.1 else '0.1-0.2' if value <= 0.2 else '0.2-0.3'} ATR"
    if name == "iv":
        return f"iv={'<50%' if value < 0.5 else '50-100%' if value < 1.0 else '100-200%' if value < 2.0 else '200%+'}"
    if name == "minute_volume":
        return f"minvol={'<10' if value < 10 else '10-50' if value < 50 else '50-200' if value < 200 else '200+'}"
    if name == "ask_share":
        return f"ask={'<40%' if value < 0.4 else '40-60%' if value < 0.6 else '60-80%' if value < 0.8 else '80%+'}"
    if name == "moneyness":
        return f"otm={'<0.5%' if value < 0.5 else '0.5-1.5%' if value < 1.5 else '1.5-3%' if value < 3 else '3%+'}"
    return f"{name}={value}"


FEATURES = ["minute", "price", "minute_volume", "ask_share", "moneyness",
            "iv", "window", "agree", "chase", "gex", "wall"]


def gex_features(sig, levels):
    """Which side of the dealers' book this entry sits on. Two facts, no theory.

    `gex`  — is the stock above or below the gamma flip at the break. Which
             side helps a +40% scalp is exactly what the table is for, so this
             does not encode an expectation either way.
    `wall` — is the relevant wall (call wall for calls, put wall for puts)
             within one ATR AHEAD of price. A wall inside the move the trade
             needs is a place dealers lean against it; beyond one ATR the
             trade has usually paid or died before reaching it.
    """
    out = {"gex": None, "wall": None}
    if not levels or not sig:
        return out
    price, atr = sig.get("close") or 0, sig.get("atr") or 0
    flip = levels.get("gamma_flip")
    if flip and price:
        out["gex"] = "above flip" if price > flip else "below flip"
    up = sig.get("direction") == "call"
    wall = levels.get("call_wall") if up else levels.get("put_wall")
    if wall and price and atr > 0:
        ahead = (wall - price) if up else (price - wall)
        out["wall"] = ("behind" if ahead <= 0 else
                       "within 1 ATR" if ahead <= atr else "clear")
    return out


def features(rows, i, meta):
    """Only what a trader could see at the moment of the buy."""
    r = rows[i]
    price = r["close"] or r["avg_price"]
    sided = (r["ask_volume"] or 0) + (r["bid_volume"] or 0)
    spot, strike = meta.get("stock_price", 0), meta.get("strike", 0)
    moneyness = None
    if spot > 0 and strike > 0:
        gap = (strike - spot) if meta.get("type") == "call" else (spot - strike)
        moneyness = round(gap / spot * 100, 3)
    return {
        "minute": minute_of(r),
        "price": price,
        "iv": r["iv"] or None,
        "minute_volume": r["volume"],
        "ask_share": (r["ask_volume"] / sided) if sided else None,
        "moneyness": moneyness,
    }


def scan_contract(rows, meta, args, spread_pct, gates=None,
                  take=None, stop=None, slip=None, hold=None):
    """Every minute of this session that could have been an entry.

    `gates` maps 'HH:MM' -> the signal that was live in the 15m bar containing
    that minute. When it is given, only minutes inside a bar that passed every
    gate are entries — which is the difference between measuring a strategy and
    measuring the whole tape.
    """
    out = []
    for i in range(len(rows) - 1):
        price = rows[i]["close"] or rows[i]["avg_price"]
        if not (args.min_price <= price <= args.max_price):
            continue
        minute = minute_of(rows[i])
        if minute >= args.hard_exit:
            continue
        if args.max_iv is not None:
            iv = rows[i]["iv"]
            if iv is None or iv > args.max_iv:
                continue
        sig = None
        if gates is not None:
            sig = gates.get(bar_key(minute))
            if not sig or sig["direction"] != meta.get("type"):
                continue
        trade = entry_exit(rows, i, take or args.take, stop or args.stop,
                           hold or args.max_hold, spread_pct, args.hard_exit,
                           slip_pct=args.slip if slip is None else slip)
        if not trade:
            continue
        trade["multiple"] = trade["exit"] / trade["entry"]
        trade["symbol"] = meta["option_symbol"]
        trade.update(features(rows, i, meta))
        if sig:
            trade["window"] = regime.time_window(minute)
            trade["agree"] = sig["agree"]
            trade["chase"] = round(sig["chase_atr"], 2)
            trade["gex"] = sig.get("gex")
            trade["wall"] = sig.get("wall")
        out.append(trade)
    return out


def clock_bias(tapes, args):
    """Does the budget filter also decide WHAT TIME OF DAY we can trade?

    This is not a statistics question, it is an options question. A same-day
    contract is worth more in the morning than in the afternoon at the same
    strike, because the afternoon one has less life left. So "only contracts
    between $0.05 and $2.00" is not just a wallet rule -- late in the day far
    more contracts fall into that band simply because theta has eaten them.

    If that is happening, every measurement in this file describes AFTERNOON
    0DTE trading and calls itself 0DTE trading. And the afternoon is the half
    of the session where decay is fastest, which is a different trade with a
    different edge.

    So this counts, hour by hour, how many minutes were inside the band and
    how many were priced out. It is the difference between choosing to trade
    the afternoon and being pushed there without noticing.
    """
    rows = defaultdict(lambda: [0, 0, 0])
    for _c, tape in tapes:
        for r in tape:
            price = r["close"] or r["avg_price"]
            if not price:
                continue
            hour = (minute_of(r) or "??")[:2] + ":00"
            if price > args.max_price:
                rows[hour][1] += 1
            elif price < args.min_price:
                rows[hour][2] += 1
            else:
                rows[hour][0] += 1
    if not rows:
        return
    print(f"\n  WHY A MINUTE COULD BE AN ENTRY — the budget is also a clock")
    print(f"  (entry premium ${args.min_price}-${args.max_price})\n")
    print(f"    {'hour':>6} {'in band':>9} {'too dear':>9} {'too cheap':>10} "
          f"{'usable':>8}")
    for hour in sorted(rows):
        ok, dear, cheap = rows[hour]
        tot = ok + dear + cheap
        if tot < 20:
            continue
        print(f"    {hour:>6} {ok:>9} {dear:>9} {cheap:>10} "
              f"{ok / tot * 100:>7.0f}%")
    # Split at noon New York, which is only meaningful now that these hours
    # ARE New York. The first version compared UTC hours against "12:00" and
    # found an empty morning, then reported 0% usable and concluded the band
    # excluded the morning entirely. It excluded nothing; the bug was mine.
    morning = [v for h, v in rows.items() if h < "12:00"]
    afternoon = [v for h, v in rows.items() if h >= "12:00"]
    if not morning or not afternoon:
        print("\n    Only one half of the session carried rows — no comparison.")
        return

    def usable(chunk):
        ok = sum(c[0] for c in chunk)
        tot = sum(sum(c) for c in chunk)
        return (ok / tot * 100) if tot else 0.0

    am, pm = usable(morning), usable(afternoon)
    print(f"\n    morning {am:.0f}% usable vs afternoon {pm:.0f}%")
    if pm > am * 1.5:
        print("    The band is a TIME filter, not only a wallet one: the same\n"
              "    strike is dearer in the morning because it still has life\n"
              "    left. Every figure in this file is afternoon 0DTE.")
    elif am > pm * 1.5:
        print("    The band skews toward the morning.")
    else:
        print("    The band does not obviously favour either half.")


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

    # Salem's rule: the profit's size does not matter, not losing does. So the
    # headline is how often a trade ends below what it cost, not the hit rate.
    losers = [o for o in obs if o["multiple"] < 1.0]
    flat = [o for o in obs if 1.0 <= o["multiple"] < 1.02]
    loss_rate = len(losers) / len(obs) * 100
    avg_loss = (statistics.mean(1 - o["multiple"] for o in losers) * 100
                if losers else 0.0)
    print(f"  {len(obs)} trades across {n_contracts} contracts{label}")
    print(f"    ENDED AT A LOSS    : {loss_rate:.1f}%   "
          f"(average loss {avg_loss:.1f}% of the stake)")
    print(f"    ended flat or up   : {100 - loss_rate:.1f}%  "
          f"(of which {len(flat)/len(obs)*100:.1f}% barely moved)")
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
    return {"avg": avg, "hit": hit, "n": len(obs), "contracts": n_contracts,
            "loss_rate": loss_rate}


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


def run_one(date, args, spread_pct):
    print(f"\n{'#'*64}\n# 0DTE session {date}\n{'#'*64}")
    print(f"Buy a contract expiring {date}, sell at +{args.take:.0f}%, "
          f"cut at -{args.stop:.0f}%,")
    print(f"give up after {args.max_hold} minutes, out by {args.hard_exit}.")
    extra = []
    if args.skip_windows:
        extra.append(f"refusing the {', '.join(args.skip_windows)} window(s)")
    if args.min_agree is not None:
        extra.append(f"committee {args.min_agree}/4")
    if args.require_gex:
        extra.append(f"only {args.require_gex}")
    if args.max_iv is not None:
        extra.append(f"iv at or under {args.max_iv:.0%}")
    if extra:
        print(f"Filtered: {', '.join(extra)} — the walk-forward figure is the "
              f"only one that judges this.")
    print(f"Spread: each contract charged its own measured width, half each "
          f"way. Commission ${C.COMMISSION_PER_CONTRACT:.2f}/contract/side.\n"
          f"Entry premium ${args.min_price}-${args.max_price} "
          f"(${args.min_price*100:.0f}-${args.max_price*100:.0f} a contract)\n")
    # expiry_dates, not min_dte/max_dte: the screener measures dte from TODAY,
    # so asking for dte 0 on a past session matched nothing on every date.
    try:
        pool = uw.screen_contracts(is_otm="true", expiry_dates=[date],
                                   min_volume=args.min_volume, type=args.type,
                                   ticker_symbol=args.tickers or None,
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
                  "0DTE contracts above the volume floor.")
        return None

    # NOT filtered by the screener's price. That price is the session's
    # snapshot, so keeping only contracts under $2 there keeps the ones that
    # ENDED the day cheap — which is very close to keeping the ones that lost.
    # The budget is applied minute by minute inside scan_contract instead, on
    # the price at the moment of the buy, which is the only price Salem sees.
    pool = pool[:args.contracts]
    print(f"{len(pool)} contracts, taken in the screener's own order "
          f"(volume). One request each.\n")

    # One set of 15m stock bars per ticker, shared by all its contracts.
    gate_map, gate_note = {}, {}
    if args.gated:
        for t in sorted({c.get("ticker") or
                         (uw.parse_occ(c["option_symbol"]) or {}).get("ticker")
                         for c in pool}):
            if not t:
                continue
            built = build_gates(t, date, args)
            if built:
                gate_map[t] = built["gates"]
                gate_note[t] = built["reasons"]
            else:
                gate_map[t] = None
        passed = sum(len(g) for g in gate_map.values() if g)
        scored = sum(sum(r.values()) for r in gate_note.values())
        print(f"  Gates: {passed} of {scored} of the session's 15m bars "
              f"cleared every rule, across {len(gate_map)} tickers")
        # Dealer positioning for that day, one request per ticker, stamped on
        # every bar that passed so the feature table can split by it. Salem's
        # question is whether a break holds or reverses at once; the gamma
        # flip is the one mechanical answer to that, and it is measured here
        # BEFORE it is allowed anywhere near a live alert.
        with_gex = 0
        for t, gates in gate_map.items():
            if not gates:
                continue
            levels = uw.gex_levels(t, date=date)
            if levels:
                with_gex += 1
            for sig in gates.values():
                sig.update(gex_features(sig, levels))
            if args.require_gex:
                # A ticker with no GEX data is REFUSED, not waved through:
                # "could not check" is not "checked out". Dropping the whole
                # gates entry would say the ticker never signalled, so the
                # refused bars are removed and counted by their reason.
                for key in [k for k, v in gates.items()
                            if v.get("gex") != args.require_gex]:
                    gate_note[t][f"gex not {args.require_gex}"] += 1
                    gate_note[t]["PASS"] -= 1
                    del gates[key]
        print(f"  GEX levels found for {with_gex} of "
              f"{sum(1 for g in gate_map.values() if g)} tickers with a pass")
        merged = Counter()
        for r in gate_note.values():
            merged.update(r)
        for why, n in merged.most_common(6):
            print(f"    {n:4d}  {why}")
        print()

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
            print(f"  row fields: {', '.join(rows[0]['_keys'])}")
            quoted = sum(1 for r in rows if r["bid"] > 0 and r["ask"] > 0)
            print(f"  {len(rows)} minutes, {quoted} carry a bid/ask."
                  + ("" if quoted else " No NBBO is served here, which is why "
                     "the spread is charged as a parameter."))
            shape_shown = True
        tapes.append((c, rows))
        if n % 25 == 0:
            print(f"  {n}/{len(pool)} contracts pulled")

    if failures:
        print(f"\n  {len(failures)} contracts had no intraday data")
    if not tapes:
        print("\nNo usable tapes.")
        return None

    # Each contract charged its OWN measured spread, not a whitelist and not a
    # guess. UBER stays in the population; it just pays what UBER cost.
    spread_of, widths = {}, []
    for c, tape_rows in tapes:
        sp = measured_spread(tape_rows)
        spread_of[c["option_symbol"]] = sp
        if sp is not None:
            widths.append(sp)
    unquoted = len(tapes) - len(widths)
    if widths:
        widths.sort()
        print(f"\n  Measured spreads: median {widths[len(widths)//2]:.1f}%, "
              f"tightest {widths[0]:.1f}%, widest {widths[-1]:.1f}%"
              + (f", {unquoted} with no quote (dropped)" if unquoted else ""))
    tapes = [(c, r) for c, r in tapes
             if spread_of.get(c["option_symbol"]) is not None
             and spread_of[c["option_symbol"]] <= args.max_spread]
    print(f"  {len(tapes)} contracts quoted at or under {args.max_spread:.0f}%")
    if not tapes:
        print("  None tight enough. A target smaller than the round trip is "
              "not a trade.")
        return None

    def ticker_of(c):
        return c.get("ticker") or (uw.parse_occ(c["option_symbol"]) or {}).get("ticker")

    clock_bias(tapes, args)
    everything = [t for c, rows in tapes
                  for t in scan_contract(rows, c, args,
                                         spread_of[c["option_symbol"]])]
    obs = everything
    if args.gated:
        obs = [t for c, rows in tapes
               for t in scan_contract(rows, c, args,
                                      spread_of[c["option_symbol"]],
                                      gates=gate_map.get(ticker_of(c)) or {})]

    if not obs:
        print("\nNo entry cleared the gates in this session. That is the "
              "rule working, not a failure — but it also means this session "
              "says nothing.")
        return None

    print(f"\n{'='*64}")
    if args.gated:
        print("  EVERY MINUTE OF THE TAPE (what the last run measured):")
        summarise(everything, args.take, args.stop)
        print("\n  ONLY ENTRIES THAT CLEARED THE GATES:")
    stats = summarise(obs, args.take, args.stop)

    hold_sweep(tapes, args, spread_of, gate_map, ticker_of)
    pooled, splits = sweep(tapes, args, spread_of, gate_map, ticker_of)
    report_features(obs, stats["avg"])
    return {"date": date, "sweep": pooled, "splits": splits,
            "detail": _split(obs), **stats}


# The 20-session run's best pair, +40/-25, sat exactly at the EDGE of the old
# grid, and a maximum on the boundary usually means the real one is outside it.
# So the grid now runs well past it. Wider is not free: the loss RATE falls but
# each loss costs more, which is why every row reports both.
# Bounded at both ends, and Salem drew the upper one: +100/-50 "مثل المقامرة".
# He is right. A -50% stop is not a stop, it is letting the contract die and
# calling it a plan. The measured gradient says wider keeps helping, but the
# gradient is not the whole story — past some width the stop stops being a
# risk control at all. The band kept here is the one where a stop is still a
# stop: wide enough to sit OUTSIDE the noise that took out 78% of trades at
# -10%, tight enough to still be a decision.
MAX_STOP_PCT = 35.0
# Pairs where the stop is smaller than the target: a trade, in the ordinary
# sense that the thing you can win is bigger than the thing you can lose.
CORE_GRID = [(60, 35), (50, 35), (50, 30), (40, 30), (40, 25), (30, 20),
             (25, 15), (25, 10), (20, 10), (15, 10), (15, 8), (12, 8), (10, 8)]

# The HIT-RATE LADDER: the same -35 stop with the target walked down, into
# territory where the stop is as large as the target or larger.
#
# Salem asked for a 45% hit rate and said he would not accept less. He is owed
# the honest answer rather than an argument, and the honest answer is that 45%
# is easy: a smaller move is reached more often, so lowering the target raises
# the hit rate on the same trades. What rises with it is the bar. Break-even
# is stop/(take+stop), so +20% against a -35% stop needs 63.6% of trades to
# work -- ask for 45% and you get it, along with a requirement of 64%.
#
# These rows are kept SEPARATE because they are not candidates. They exist so
# that the hit rate he asked for appears in the table next to the number it
# has to clear, measured on his own sessions, instead of being described to
# him by me.
HIT_LADDER = [(40, 35), (30, 35), (25, 35), (20, 35), (15, 35)]

GRID = CORE_GRID + HIT_LADDER

# Salem's target, in his words: losses no more than 35% of all trades entered.
TARGET_LOSS_RATE = 35.0


def hold_sweep(tapes, args, spread_of, gate_map, ticker_of):
    """How long to give the trade, at the configured pair.

    This exists because of the one number in the report that is not a pattern
    somebody mined: across every session roughly a third to a half of gated
    trades TIMED OUT. They did not fail -- the clock ran out on them. And the
    median winner took 8 to 10 minutes to get there, against a 15-minute
    limit, so a trade needing 16 is recorded as a failure of the setup when it
    was a failure of the deadline.

    Holding a same-day contract longer is not free: theta is the whole reason
    the deadline exists, and after some point the decay costs more than the
    extra room is worth. Which side wins is measurable, so it is measured.

    This is a POOLED table and carries the pooled table's flaw -- it sees
    every session. Confirm whatever it suggests with a separate run at that
    --max-hold and read the walk-forward figure.
    """
    holds = args.holds
    if not holds:
        return
    print(f"\n{'='*64}")
    print(f"  HOW LONG TO GIVE IT — at +{args.take:.0f}%/-{args.stop:.0f}%")
    print("  (about a third of trades time out; the clock may be the "
          "binding rule)\n")
    print(f"  {'minutes':>8} {'hit':>7} {'timed out':>10} {'lost':>7} "
          f"{'per $1':>9} {'even wt':>9} {'won':>6} {'n':>6}")
    for hold in holds:
        per_session, got_all = [], []
        for c, tape_rows in tapes:
            sp = spread_of.get(c["option_symbol"])
            if sp is None:
                continue
            got_all += scan_contract(tape_rows, c, args, sp,
                                     gates=(gate_map.get(ticker_of(c)) or {})
                                     if args.gated else None, hold=hold)
        if len(got_all) < 20:
            continue
        avg = statistics.mean(o["multiple"] for o in got_all)
        hit = sum(1 for o in got_all if o["why"] == "take") / len(got_all) * 100
        out = sum(1 for o in got_all if o["why"] == "timeout") / len(got_all) * 100
        lost = sum(1 for o in got_all if o["multiple"] < 1.0) / len(got_all) * 100
        mark = "*" if avg > 1.0 else " "
        print(f"  {hold:>8} {hit:>6.1f}% {out:>9.1f}% {lost:>6.1f}% "
              f"${avg:>7.3f}{mark} {'':>9} {'':>6} {len(got_all):>6}")
    print("\n  Pooled, so it sees every session. Confirm a promising row with"
          "\n  its own run at that --max-hold and read WALK-FORWARD there.")


def sweep(tapes, args, spread_of, gate_map, ticker_of):
    """Every take/stop pair, ranked by how rarely it loses.

    Salem's rule is that the size of the profit does not matter and not losing
    does. The arithmetic of that is counter-intuitive and worth stating: taking
    a SMALLER profit with the same stop makes the bar HARDER, not easier.
    Break-even is stop/(take+stop), so +20% against a -25% stop needs 55.6% of
    trades to work where +40% against the same stop needs 38.5%.

    The lever is the stop, not the target. +25/-10 needs 28.6%; +20/-25 needs
    55.6%. Which is why this sweeps both together instead of tuning one.
    """
    print(f"\n{'='*64}")
    print("  Every take/stop pair, ranked by how rarely it ends at a loss")
    print("  (break-even = stop / (take + stop) — the stop is the lever)\n")
    print(f"  {'take':>5} {'stop':>5} {'need':>6} {'lost':>7} {'per $1':>8} "
          f"{'n':>6}")
    rows, pooled, splits = [], {}, {}
    for take, stop in GRID:
        for slip in args.slips:
            got = []
            for c, tape_rows in tapes:
                sp = spread_of.get(c["option_symbol"])
                if sp is None:
                    continue
                got += scan_contract(tape_rows, c, args, sp,
                                     gates=(gate_map.get(ticker_of(c)) or {})
                                     if args.gated else None,
                                     take=take, stop=stop, slip=slip)
            if not got:
                continue
            avg = statistics.mean(o["multiple"] for o in got)
            lost = sum(1 for o in got if o["multiple"] < 1.0) / len(got) * 100
            hit = sum(1 for o in got if o["why"] == "take") / len(got) * 100
            losers = [o for o in got if o["multiple"] < 1.0]
            avg_loss = (statistics.mean(1 - o["multiple"] for o in losers) * 100
                        if losers else 0.0)
            pooled[(take, stop, slip)] = (avg, lost, len(got), hit, avg_loss)
            if slip == args.slips[0]:
                # Kept because the alternative was a full twenty-session
                # re-run for every pair we wanted to ask about. The first run
                # of by_budget answered at +25/-10 — the pair we had already
                # abandoned — because the split was built from the configured
                # pair alone and the default had not moved.
                splits[(take, stop)] = _split(got)
            if slip == args.slips[0] and len(got) >= 20:
                rows.append((lost, take, stop, stop / (take + stop) * 100,
                             avg, len(got)))
    for lost, take, stop, need, avg, n in sorted(rows):
        mark = "  <- makes money" if avg > 1.0 else ""
        print(f"  +{take:>3}% -{stop:>3}% {need:>5.1f}% {lost:>6.1f}% "
              f"${avg:>7.3f} {n:>6}{mark}")
    if not rows:
        print("  Not enough trades at this session to say anything.")
    return pooled, splits


def _split(obs):
    """The two cuts of the cheap-far-strike question, from one list of trades.

    Price is what he pays; distance to the strike is what the stock has to do
    for it to pay back. They are not the same cut and a cheap near strike is
    not a cheap far one.
    """
    out = {"budget": defaultdict(list), "moneyness": defaultdict(list)}
    for o in obs:
        out["budget"][bucket("price", o.get("price"))].append(o)
        out["moneyness"][bucket("moneyness", o.get("moneyness"))].append(o)
    return out


def by_budget(results, args, ranked=None):
    """Cheap or far — split at the configured pair AND at the pair that won.

    Salem likes the cheap far strike that costs little and starts moving the
    moment a wave begins. Every run so far said cheap loses — but every run
    said it at +25/-10, and that stop is inside the noise on a contract quoted
    5% wide. A cheap far strike has enormous gamma and needs room to breathe.

    The first version of this asked only at the configured pair, so a run left
    on the default answered at +25/-10 again — the pair we had abandoned. The
    sweep already trades every pair in the grid, so the pair the pooled table
    ranks first is printed too, whatever the flags said.

    Both cuts matter and they are not the same cut. Price is what he pays;
    distance to the strike is what the stock has to do for it to pay back.
    """
    if not any(r.get("detail") for r in results):
        return
    pairs = [(int(args.take), int(args.stop), "the pair this run used")]
    for take, stop in (ranked or [])[:1]:
        if (take, stop) != pairs[0][:2]:
            pairs.append((take, stop, "the pair the pooled table ranks first"))
    for take, stop, why in pairs:
        _budget_table(results, take, stop, why, pairs[0][:2])


# A bucket needs this many trades within ONE session before that session is
# allowed a vote on it. Below it the session average is one or two contracts
# having a good afternoon.
MIN_SESSION_TRADES = 10


def _budget_table(results, take, stop, why, configured):
    print(f"\n{'='*64}")
    print(f"  +{take}% / -{stop}% BY WHAT THE CONTRACT COST AND HOW FAR OUT")
    print(f"  (the cheap-far-strike question, asked at {why})\n")
    for name, label in (("budget", "cost"), ("moneyness", "distance to strike")):
        buckets, sessions = defaultdict(list), defaultdict(list)
        for r in results:
            src = (r.get("detail") if (take, stop) == configured
                   else (r.get("splits") or {}).get((take, stop)))
            for key, obs in (src or {}).get(name, {}).items():
                buckets[key] += obs
                # The same correction the pooled table needed: this figure
                # weighted a session by how many entries it produced, and the
                # sessions that produced most are the ones the rule liked. A
                # bucket can pool above $1.00 on two good afternoons.
                if len(obs) >= MIN_SESSION_TRADES:
                    sessions[key].append(
                        statistics.mean(o["multiple"] for o in obs))
        if not buckets:
            continue
        print(f"  by {label}")
        print(f"    {'':16s} {'per $1':>8} {'hit':>7} {'lost':>7} {'n':>6} "
              f"{'contracts':>10} {'even wt':>9} {'won':>6}")
        for key, obs in sorted(buckets.items(),
                               key=lambda kv: -statistics.mean(
                                   o["multiple"] for o in kv[1])):
            if len(obs) < 20:
                continue
            avg = statistics.mean(o["multiple"] for o in obs)
            hit = sum(1 for o in obs if o["why"] == "take") / len(obs) * 100
            lost = sum(1 for o in obs if o["multiple"] < 1.0) / len(obs) * 100
            cs = len({o["symbol"] for o in obs})
            per = sessions.get(key) or []
            equal = f"${statistics.mean(per):>7.3f}" if per else "      - "
            votes = f"{sum(1 for a in per if a > 1.0)}/{len(per)}" if per else "  -"
            mark = "  *" if avg > 1.0 else "   "
            thin = "  (thin)" if cs < 10 else ""
            print(f"    {key:16s} ${avg:>7.3f}{mark} {hit:>6.1f}% {lost:>6.1f}% "
                  f"{len(obs):>6} {cs:>10} {equal:>9} {votes:>6}{thin}")
        print("\n    'per $1' pools every trade, so it leans on the busiest "
              "sessions.\n    'even wt' gives each session one vote; 'won' is "
              "how many made money.\n")


def pooled_sweep(results, args):
    """Every take/stop pair pooled across every session, at each slippage.

    A per-session table cannot answer the question. The first twenty-session
    run had +40/-25 winning five sessions and losing four, and the only way to
    see that it pooled to $1.079 while +25/-10 pooled to $0.975 was to weight
    the sessions by hand. The tool should not make its reader do that.

    The slippage columns are the real test of that result. The wide pair only
    wins if the wide stop actually holds, so it is the configuration MOST
    exposed to a 0DTE contract gapping through its stop — the one assumption
    minute bars cannot check. If the ranking survives a 25% slip it is about
    the setup; if it inverts, it was about an assumption.
    """
    keys = sorted({k for r in results for k in r.get("sweep", {})},
                  key=lambda k: (-k[0], -k[1]))
    if not keys:
        return
    slips = args.slips
    print(f"\n{'='*64}")
    print("  POOLED across every session — the table that decides it")
    print("  (each cell is return per $1; columns charge the stop slipping "
          "through)\n")
    head = "  ".join(f"slip {s:>2.0f}%" for s in slips)
    print(f"  {'take':>5} {'stop':>5} {'ifstop':>6} {'real':>6} {'hit':>6} "
          f"{'nolose':>6} {'n':>6}  {head}  {'even wt':>7}  {'won':>5}")
    seen = set()
    out = []
    for take, stop, _ in keys:
        if (take, stop) in seen:
            continue
        seen.add((take, stop))
        cells, n_at_base, per_session = [], 0, []
        hit_at_base = avg_loss_at_base = 0.0
        for slip in slips:
            tot = num = lost = hit_w = loss_w = 0
            for r in results:
                got = r.get("sweep", {}).get((take, stop, slip))
                if got:
                    tot += got[0] * got[2]
                    lost += got[1] * got[2]
                    hit_w += got[3] * got[2]
                    loss_w += (got[4] if len(got) > 4 else 0) * got[2]
                    num += got[2]
            if not num:
                cells.append(None)
                continue
            cells.append(tot / num)
            if slip == slips[0]:
                n_at_base = num
                hit_at_base = hit_w / num
                avg_loss_at_base = loss_w / num
                per_session = [r["sweep"][(take, stop, slip)][0] for r in results
                               if (take, stop, slip) in r.get("sweep", {})
                               and r["sweep"][(take, stop, slip)][2] >= 20]
        if n_at_base < 100:
            continue
        lost_at_base = None
        num = tot = 0
        for r in results:
            got = r.get("sweep", {}).get((take, stop, slips[0]))
            if got:
                tot += got[1] * got[2]
                num += got[2]
        if num:
            lost_at_base = tot / num
        won = sum(1 for a in per_session if a > 1.0)
        equal = (sum(per_session) / len(per_session)) if per_session else None
        out.append((cells[0] or 0, take, stop, n_at_base, cells, lost_at_base,
                    won, len(per_session), equal, hit_at_base,
                    avg_loss_at_base))
    hits, ranked = [], []
    for (_, take, stop, n, cells, lost, won, sessions, equal,
         hit, avg_loss) in sorted(out, reverse=True):
        # Ranked by the equal-weighted figure, not the pooled one: the pooled
        # average weights a session by how many entries it produced, so it
        # hands the verdict to the busiest days.
        ranked.append((equal if equal is not None else 0.0, won, take, stop))
        # stop/(take+stop) assumes every loss is a full stop. It is not: with
        # a 15-minute clock most losers time out somewhere short of the stop,
        # so the real bar is set by the loss actually taken. Printing only the
        # naive figure made +60/-35 look like a failure at 25.6% hit against
        # "36.8% needed" while it returned $1.126.
        need = stop / (take + stop) * 100
        real = (avg_loss / (take + avg_loss) * 100) if avg_loss > 0 else 0.0
        body = "  ".join("     -  " if c is None else
                         f"${c:>6.3f}" + ("*" if c > 1.0 else " ")
                         for c in cells)
        target = ""
        if lost is not None and lost <= TARGET_LOSS_RATE:
            target = "  <= 35% TARGET"
            if cells[0] and cells[0] > 1.0:
                hits.append((take, stop, lost, cells))
        # The pooled figure weights a session by how many entries it produced,
        # and the sessions that produced most are the ones the rule liked — so
        # it flatters a rule that fires more on the days it happens to suit.
        # The equal-weighted average gives every session one vote, and the
        # win count says how many actually made money.
        eq = f"${equal:>6.3f}" if equal is not None else "     - "
        # "did not lose" and "reached the target" are different questions. A
        # trade that times out flat did not lose and did not win, and at a wide
        # stop most of the difference between the two columns is exactly that.
        keep = 100 - (lost if lost is not None else 0)
        print(f"  +{take:>3}% -{stop:>3}% {need:>5.1f}% {real:>5.1f}% "
              f"{hit:>5.1f}% {keep:>5.1f}% {n:>6}  {body}  {eq}  "
              f"{won}/{sessions}{target}")
    print("\n  'ifstop' is stop/(take+stop) — the bar IF every loss were a "
          "full stop.\n    'real' uses the loss actually taken: with a 15-minute "
          "clock most losers\n    time out short of the stop, so the real bar "
          "is lower. Compare 'hit' to\n    'real', not to 'ifstop'.")
    print("\n  * = made money. A row that only makes money in the leftmost "
          "column\n    was measuring the no-slippage assumption, not the trade."
          "\n  'hit' reached the target; 'nolose' did not end below cost — a "
          "trade that\n    times out flat is in the second and not the first. "
          "'even wt' gives every\n    session one vote instead of weighting by "
          "trade count; 'won' counts sessions."
          " A pair whose\n    pooled figure beats its equal-weighted one is "
          "leaning on its busiest days.")
    # The hit rate, answered as asked. Printed from the table rather than
    # asserted, and printed with what that hit rate has to beat.
    best_hit = max(out, key=lambda r: r[9]) if out else None
    if best_hit:
        _c, take, stop, n, cells, _l, _w, _s, equal, hit, avg_loss = best_hit
        need = (avg_loss / (take + avg_loss) * 100) if avg_loss > 0 else 0.0
        print(f"\n  Highest hit rate in this grid: +{take}%/-{stop}% at "
              f"{hit:.1f}%, on {n} trades.")
        print(f"  It needs {need:.1f}% to break even and returns "
              f"${cells[0]:.3f} per $1"
              + ("." if cells[0] > 1.0 else " — it LOSES money."))
        print("  A hit rate is not an edge. Lowering the target raises the\n"
              "  hit rate and raises the bar it has to clear, by the same\n"
              "  arithmetic and usually by more.")
    print(f"\n  Salem's rule: losses no more than {TARGET_LOSS_RATE:.0f}% of "
          "trades entered.")
    if hits:
        take, stop, lost, cells = hits[0]
        survives = cells[-1] and cells[-1] > 1.0
        print(f"  Closest pair that meets it AND makes money: +{take}% / "
              f"-{stop}%, losing {lost:.1f}%.")
        print("  " + ("It survives the worst slippage column, so it is about "
                      "the trade." if survives else
                      "It does NOT survive the worst slippage column — that is "
                      "an assumption, not an edge."))
    else:
        print("  Nothing in this grid met it while also making money. The "
              "widest\n  pairs cut the loss RATE but pay more on each loss.")
    return [(take, stop) for _, _, take, stop in sorted(ranked, reverse=True)]


def walk_forward(results, args, min_train=4):
    """Choose the pair on sessions already seen; score it on the next one.

    This is the honest version of the pooled table, and it exists because that
    table has a flaw worth naming plainly: it ranks all thirteen pairs on all
    nine sessions, picks the winner, and then reports that winner's figure on
    the same nine sessions. Whatever pair happens to suit this sample will
    appear at the top, and its number is guaranteed to be too kind. Every grid
    search does this; most backtests never say so.

    Walk-forward removes the loop. Sessions are put in date order. At each
    step the pair is chosen using ONLY the sessions before it — by equal
    weight, so a busy session cannot buy the decision — and then scored on the
    next session, which had no say in the choice. What comes out is a series
    of returns from decisions that could actually have been made in advance.

    If the walk-forward average is near the pooled one, the edge is about the
    trade. If it collapses, the pooled figure was measuring hindsight. And if
    the chosen pair keeps changing from step to step, there was never a best
    pair to find -- that instability IS the result, so it is printed too.
    """
    dated = sorted((r for r in results if r.get("sweep")),
                   key=lambda r: r["date"])
    if len(dated) <= min_train:
        print(f"\n  Walk-forward needs more than {min_train} sessions with "
              f"trades; this run has {len(dated)}.")
        return
    slip = args.slips[0]

    def per_session(r, take, stop):
        got = r["sweep"].get((take, stop, slip))
        # A session that produced almost nothing at this pair is noise in both
        # directions, and letting it vote was how two-contract days ended up
        # deciding which pair looked best.
        return got[0] if got and got[2] >= 20 else None

    print(f"\n{'='*64}")
    print("  WALK-FORWARD — the pair chosen BEFORE the session it is judged on")
    print("  (each row: trained on every earlier session, scored on this one)\n")
    print(f"  {'session':>12} {'chosen':>12} {'trained on':>11} "
          f"{'that session':>13}")
    oos, chosen_seq = [], []
    for k in range(min_train, len(dated)):
        train, test = dated[:k], dated[k]
        best, best_avg = None, None
        for take, stop in GRID:
            vals = [v for v in (per_session(r, take, stop) for r in train)
                    if v is not None]
            if len(vals) < min_train:
                continue
            avg = statistics.mean(vals)
            if best_avg is None or avg > best_avg:
                best, best_avg = (take, stop), avg
        if best is None:
            continue
        got = per_session(test, *best)
        chosen_seq.append(best)
        mark = ""
        if got is None:
            shown = "    (thin)"
        else:
            oos.append(got)
            shown = f"${got:>7.3f}"
            mark = "  <-" if got > 1.0 else ""
        print(f"  {test['date']:>12} {'+%d/-%d' % best:>12} "
              f"{len(train):>8} sess {shown:>13}{mark}")

    if not oos:
        print("\n  No out-of-sample session carried enough trades to score.")
        return
    avg = statistics.mean(oos)
    won = sum(1 for a in oos if a > 1.0)
    print(f"\n  Out of sample: ${avg:.3f} per $1 across {len(oos)} sessions, "
          f"{won} of {len(oos)} profitable.")
    # The stability of the choice is a result in itself, not a footnote.
    # Counted as transitions rather than distinct values: a pair that settles
    # after one early change is a parameter, while one that keeps swapping is
    # the grid chasing whichever session came last.
    moves = sum(1 for a, b in zip(chosen_seq, chosen_seq[1:]) if a != b)
    print(f"  The chosen pair changed {moves} time(s) over "
          f"{len(chosen_seq)} decisions "
          + ("— it settled, which is what a real parameter does."
             if moves * 3 <= len(chosen_seq) else
             "— it never settled, so there was no best pair to find."))
    if avg <= 1.0:
        print("  VERDICT: chosen in advance, this rule did NOT make money.\n"
              "  The pooled table's figure was the benefit of hindsight.")
    else:
        print("  VERDICT: it survived being chosen in advance. That is the\n"
              "  only figure in this file that was not helped by knowing\n"
              "  the answer first.")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dates", help="comma-separated sessions to test")
    p.add_argument("--date", help="one session (YYYY-MM-DD)")
    p.add_argument("--sessions", type=int,
                   help="test this many weekdays back from --date (or from "
                        "today). Four sessions cannot separate a rule from "
                        "luck; twenty can start to. Costs roughly "
                        "--contracts requests per session.")

    p.add_argument("--stop", type=float, default=10.0, help="stop loss %%")
    p.add_argument("--slip", type=float, default=0.0,
                   help="how far a stop fills BELOW its level, as %% of the "
                        "stop price. A 0DTE contract gaps through its stop; "
                        "0 assumes it never does.")
    p.add_argument("--slips", default="0,10,25",
                   help="slippage levels the pooled table compares")
    p.add_argument("--max-hold", type=int, default=15,
                   help="minutes to give the trade before getting out")
    p.add_argument("--hard-exit", default=HARD_EXIT)
    p.add_argument("--min-price", type=float, default=0.05)
    p.add_argument("--max-price", type=float, default=2.00)
    p.add_argument("--min-volume", type=int, default=200)
    p.add_argument("--contracts", type=int, default=80)
    p.add_argument("--tickers", default="",
                   help="comma-separated universe. Empty means the whole "
                        "market, which is the default: a whitelist would have "
                        "excluded UBER by definition. Liquidity is handled by "
                        "--max-spread, measured per contract.")
    p.add_argument("--max-spread", type=float, default=15.0,
                   help="drop contracts quoted wider than this %% of mid. The "
                        "round trip has to be smaller than the target or the "
                        "trade cannot win.")
    p.add_argument("--no-gates", dest="gated", action="store_false",
                   help="measure every minute of the tape, as the first run "
                        "did, instead of only gated entries")
    p.set_defaults(gated=True)
    p.add_argument("--type", default=None, choices=["call", "put"])
    p.add_argument("--take", type=float, default=25.0, help="take profit %%")
    p.add_argument("--holds", default="10,15,20,30,45",
                   help="hold times the clock table compares, in minutes. "
                        "About a third of gated trades time out and the "
                        "median winner takes 8-10 minutes against a 15-minute "
                        "limit, so the deadline may be costing more than it "
                        "saves. Empty to skip the table.")
    p.add_argument("--min-agree", type=int, default=None,
                   help="override MIN_AGREEMENT (how many of the four reads "
                        "must agree). 4/4 beat 3/4 in four of the five "
                        "sessions that had both — a hypothesis, so only the "
                        "walk-forward figure decides it.")
    p.add_argument("--require-gex", choices=("above flip", "below flip"),
                   default=None,
                   help="only take entries on one side of the gamma flip. "
                        "A ticker with no GEX data is refused, not waved "
                        "through.")
    p.add_argument("--max-iv", type=float, default=None,
                   help="refuse entry minutes above this implied vol (0.5 = "
                        "50%%). iv<50%% beat 50-100%% in both sessions that "
                        "had both, by a wide margin.")
    p.add_argument("--skip-windows", default="",
                   help="comma-separated session windows to refuse "
                        "(open, momentum, midday, trend, gamma). The midday "
                        "returned $0.79-$0.90 in five separate sessions while "
                        "momentum returned $1.14-$1.38 — but that was read OFF "
                        "the sessions, so only the walk-forward figure decides "
                        "whether skipping it is real.")
    args = p.parse_args(argv)
    args.slips = [float(x) for x in args.slips.split(",") if x.strip()]
    args.holds = [int(x) for x in args.holds.split(",") if x.strip()]
    args.skip_windows = tuple(w.strip() for w in args.skip_windows.split(",")
                              if w.strip())
    known = {name for _s, _e, name in C.SESSION_WINDOWS}
    unknown = [w for w in args.skip_windows if w not in known]
    if unknown:
        p.error(f"unknown window(s) {', '.join(unknown)}; "
                f"choose from {', '.join(sorted(known))}")

    if args.sessions:
        end = args.date or datetime.date.today().isoformat()
        dates = trading_days(end, args.sessions)
        print(f"Testing {len(dates)} sessions, {dates[-1]} to {dates[0]}\n")
    else:
        dates = ([d.strip() for d in args.dates.split(",") if d.strip()]
                 if args.dates else [args.date] if args.date else [None])
    results = [r for r in (run_one(d, args, None) for d in dates) if r]
    if not results:
        return 1

    if len(results) > 1:
        print(f"\n{'='*64}\nREPLICATION across {len(results)} sessions")
        for r in results:
            print(f"  {r['date']}: ${r['avg']:.3f}/$1, lost {r['loss_rate']:.1f}%, "
                  f"{r['n']:4d} trades, {r['contracts']} contracts")
        be = break_even(args.take, args.stop)
        total = sum(r["n"] for r in results)
        wins = [r for r in results if r["avg"] > 1.0]
        pooled = (sum(r["avg"] * r["n"] for r in results) / total) if total else 0
        lost = (sum(r["loss_rate"] * r["n"] for r in results) / total) if total else 0
        print(f"\n  Pooled across every session: ${pooled:.3f} per $1, "
              f"{lost:.1f}% of trades ended at a loss, n={total}")
        print(f"  {len(wins)} of {len(results)} sessions made money "
              f"(break-even needs {be:.1f}% hitting +{args.take:.0f}%).")
        if total < 200:
            print(f"  {total} trades is too few to separate a rule from luck. "
                  "Re-run with --sessions 20 before reading anything into it.")
        elif len(wins) < len(results) * 0.6:
            print("  A rule that works on some sessions and not others is a "
                  "coin flip with extra steps.")
        ranked = pooled_sweep(results, args)
        by_budget(results, args, ranked)
        walk_forward(results, args)

    out = C.DATA_DIR / "zero_dte.json"
    try:
        out.write_text(json.dumps(
            [{k: (v if k != "sweep" else
                  {f"{t}/{st}/{sl}": list(cell) for (t, st, sl), cell in v.items()})
              for k, v in r.items() if k not in ("obs", "detail", "splits")}
             for r in results], indent=2))
        print(f"\nWrote {out}")
    except OSError:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
