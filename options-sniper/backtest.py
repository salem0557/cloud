"""Replay the 15m breakout rule over history and measure what it actually did.

Purpose: give the analyst layer something real to anchor on. A confidence
figure invented by a model is noise; a hit rate measured over hundreds of past
setups is a base rate. This produces the second.

What it tests and what it does not:

  tested      the technical signal end to end — the level, the ATR-based target
              and stop, the volume filter, the late-entry filter, and how the
              outcome varies by setup shape and time of day. That is 30 of the
              100 points plus every price level the alert prints.

  not tested  option P&L. Reconstructing what a contract's bid/ask was at a
              past 15m bar needs historical chain snapshots that a trial plan
              does not serve. Results are stock-level: did price reach the
              target before the stop.

  not tested  the flow, catalyst and liquidity components, for the same reason.

So a 58% hit rate here means the *stock* reached target 58% of the time. The
contract's return depends on delta, gamma and theta on top of that.

Lookback is discovered, not assumed: UW's documented Startup tier serves 90
days, and a trial serves less. The run reports the coverage it actually got.
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
import history
import technical

# Liquid, heavily-optioned names. Not a recommendation list — a sample the
# breakout rule can be measured on.
UNIVERSE = [
    "SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "AMD", "TSLA", "META", "AMZN",
    "GOOGL", "NFLX", "AVGO", "MU", "INTC", "COIN", "PLTR", "SOFI", "UBER",
    "BABA", "DIS", "BA", "JPM", "XOM", "SMCI", "MARA", "RIOT", "GME", "F", "T",
]

MAX_HOLD_BARS = 16          # 4 hours on a 15m frame; a breakout that has not
                            # resolved by then is not what the rule is about


def _age_days(iso_date):
    try:
        d = datetime.date.fromisoformat(iso_date)
    except ValueError:
        return 0
    return (datetime.date.today() - d).days


# ── Attribute buckets — the keys the analyst looks a setup up by ─
def bucket_volume(v):
    return "1.5-2x" if v < 2 else ("2-3x" if v < 3 else "3x+")


def bucket_distance(d):
    return "0-0.5atr" if d < 0.5 else ("0.5-1atr" if d < 1 else "1atr+")


def bucket_hour(end_time):
    """Session third, in real ET — opening and closing hours behave differently."""
    return history.session_third(end_time)


def setup_key(tech, direction):
    return "|".join((direction, bucket_volume(tech["volume_ratio"]),
                     bucket_distance(tech["break_distance_atr"]),
                     "closed" if tech["closed_beyond"] else "wick",
                     bucket_hour(tech.get("bar_time", ""))))


# ── Replay ──────────────────────────────────────────────────────
def simulate(bars, direction):
    """Walk forward bar by bar. Entry is the OPEN of the bar after a closed bar
    confirms, so nothing uses information the live scanner would not have."""
    trades = []
    need = max(C.CANDLES_LOOKBACK, C.ATR_PERIOD + 2)
    for i in range(need, len(bars) - MAX_HOLD_BARS - 1):
        tech = technical.analyse(bars[:i + 1], direction)
        if not technical.confirms(tech):
            continue
        entry = bars[i + 1]["open"]
        if entry <= 0:
            continue
        target, stop = tech["target"], tech["stop"]
        outcome, held, mfe, mae = "timeout", MAX_HOLD_BARS, 0.0, 0.0
        for j in range(i + 1, min(i + 1 + MAX_HOLD_BARS, len(bars))):
            b = bars[j]
            if direction == "call":
                mfe = max(mfe, b["high"] - entry)
                mae = min(mae, b["low"] - entry)
                hit_t, hit_s = b["high"] >= target, b["low"] <= stop
            else:
                mfe = max(mfe, entry - b["low"])
                mae = min(mae, entry - b["high"])
                hit_t, hit_s = b["low"] <= target, b["high"] >= stop
            if hit_t or hit_s:
                # both inside one bar: assume the stop, the conservative read
                outcome = "stop" if hit_s else "target"
                held = j - i
                break
        trades.append({
            "key": setup_key(tech, direction), "outcome": outcome, "bars_held": held,
            "mfe_atr": round(mfe / tech["atr"], 2) if tech["atr"] else 0,
            "mae_atr": round(mae / tech["atr"], 2) if tech["atr"] else 0,
            "volume_ratio": tech["volume_ratio"],
            "break_atr": tech["break_distance_atr"],
        })
    return trades


def summarise(trades):
    if not trades:
        return {"count": 0}
    wins = sum(1 for t in trades if t["outcome"] == "target")
    stops = sum(1 for t in trades if t["outcome"] == "stop")
    timeouts = len(trades) - wins - stops
    # expectancy in ATR: target is TARGET_ATR_MULT away, stop STOP_ATR_MULT
    exp = (wins * C.TARGET_ATR_MULT - stops * C.STOP_ATR_MULT) / len(trades)
    return {
        "count": len(trades),
        "hit_rate": round(wins / len(trades) * 100, 1),
        "stop_rate": round(stops / len(trades) * 100, 1),
        "timeout_rate": round(timeouts / len(trades) * 100, 1),
        "expectancy_atr": round(exp, 3),
        "median_bars_to_resolve": statistics.median(t["bars_held"] for t in trades),
        "median_mfe_atr": round(statistics.median(t["mfe_atr"] for t in trades), 2),
        "median_mae_atr": round(statistics.median(t["mae_atr"] for t in trades), 2),
    }


def main(days, tickers, out_path, interval, source):
    print(f"Backtest: {len(tickers)} tickers, {interval} bars, "
          f"requesting {days} days from {source}\n")
    all_trades, coverage = [], {}
    for ticker in tickers:
        try:
            bars = history.fetch(ticker, interval=interval, days=days, source=source)
        except history.HistoryError as e:
            print(f"  {ticker:6} —      {e}")
            continue
        if len(bars) < C.CANDLES_LOOKBACK + MAX_HOLD_BARS + 5:
            print(f"  {ticker:6} {len(bars):>6} bars — too few, skipped")
            continue
        span = f"{bars[0]['start_time'][:10]} → {bars[-1]['start_time'][:10]}"
        trades = simulate(bars, "call") + simulate(bars, "put")
        coverage[ticker] = {"bars": len(bars), "span": span, "setups": len(trades)}
        all_trades += trades
        print(f"  {ticker:6} {len(bars):>6} bars  {span}  {len(trades):>4} setups")

    if not all_trades:
        print("\nNo setups found. Either the plan serves too little history, "
              "or the filters are too tight for this sample.")
        return 1

    oldest = min(c["span"][:10] for c in coverage.values())
    actual_days = _age_days(oldest)
    print(f"\n{'═' * 62}\nCoverage: {actual_days} days (oldest bar {oldest})")
    if actual_days < days * 0.8:
        why = ("Yahoo caps intraday history by interval — try --interval 1h "
               "for a much longer sample") if source == "yahoo" else \
              ("a subscription lookback limit — UW documents 90 days on the "
               "Startup tier and less on a trial")
        print(f"NOTE: asked for {days} days, got {actual_days}. This is {why}. "
              "Not a bug.")

    overall = summarise(all_trades)
    print(f"\nOverall — {overall['count']} setups")
    print(f"  reached target : {overall['hit_rate']}%")
    print(f"  hit stop       : {overall['stop_rate']}%")
    print(f"  timed out      : {overall['timeout_rate']}%")
    print(f"  expectancy     : {overall['expectancy_atr']} ATR per setup")
    print(f"  median MFE/MAE : +{overall['median_mfe_atr']} / {overall['median_mae_atr']} ATR")

    by_key = defaultdict(list)
    for t in all_trades:
        by_key[t["key"]].append(t)
    rates = {k: summarise(v) for k, v in by_key.items()
             if len(v) >= C.BASE_RATE_MIN_SAMPLE}

    print(f"\nSetup types with >= {C.BASE_RATE_MIN_SAMPLE} samples: {len(rates)}")
    if not rates:
        print("  None yet — the analyst will fall back to the overall rate and "
              "say so. Widen --tickers or --days for per-setup rates.")
    for k, s in sorted(rates.items(), key=lambda kv: -kv[1]["hit_rate"])[:10]:
        print(f"  {s['hit_rate']:>5}%  n={s['count']:>4}  {k}")

    payload = {
        "generated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "interval": interval, "source": source,
        "days_requested": days, "days_covered": actual_days,
        "rules": {"target_atr": C.TARGET_ATR_MULT, "stop_atr": C.STOP_ATR_MULT,
                  "volume_spike": C.VOLUME_SPIKE_RATIO,
                  "min_remaining_atr": C.MIN_REMAINING_ATR,
                  "max_hold_bars": MAX_HOLD_BARS},
        "overall": overall, "by_setup": rates, "coverage": coverage,
    }
    out_path.write_text(json.dumps(payload, indent=2))
    print(f"\nWrote {out_path}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=365,
                    help="history to request; the plan may serve less")
    ap.add_argument("--tickers", help="comma-separated, default a liquid sample")
    ap.add_argument("--out", default=None)
    ap.add_argument("--interval", default="15m",
                    choices=["15m", "30m", "1h", "1d"],
                    help="15m is the live strategy; 1h reaches much further back")
    ap.add_argument("--source", default="yahoo", choices=["yahoo", "uw", "auto"],
                    help="yahoo is free and deeper; uw costs quota")
    a = ap.parse_args()
    tick = [t.strip().upper() for t in a.tickers.split(",")] if a.tickers else UNIVERSE
    out = __import__("pathlib").Path(a.out) if a.out else C.BACKTEST_FILE
    try:
        sys.exit(main(a.days, tick, out, a.interval, a.source))
    except history.HistoryError as e:
        print("history error:", e, file=sys.stderr)
        sys.exit(2)
