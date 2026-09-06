"""Paper trading, scored by the same code that produced the backtest.

The point of a paper month is to find out whether the measured edge survives
contact with live data. That only means something if the live result is
computed the same way the backtest computed it — so this imports
zero_dte.entry_exit rather than reimplementing the exit. Same tie-breaking (a
minute containing both target and stop counts as a stop), same spread charged
half each way, same hard exit before the close. A second implementation would
drift, and the first disagreement would be impossible to attribute.

The existing journal.csv records what was alerted and leaves `outcome` blank
for Salem to fill in. Nobody fills in a spreadsheet for a month. This closes
its own positions from the contract's own tape.

The baseline it is measured against, from 796 gated trades over 9 sessions:

    reaches +40%   43.2%
    does not lose  58.8%
    per $1         $1.033 with every session weighted equally

If the live numbers land near those, the backtest was measuring something real.
If they land far below, the backtest was measuring its own assumptions — and
that is worth knowing before any capital is involved.
"""
import argparse
import datetime
import json
import statistics
import sys

import venv_boot

venv_boot.ensure(["requests"])

import config as C
import market
import uw
from zero_dte import entry_exit, measured_spread, minute_of

PAPER_FILE = C.DATA_DIR / "paper.json"


def _load():
    try:
        return json.loads(PAPER_FILE.read_text())
    except (OSError, ValueError):
        return {"open": [], "closed": []}


def _save(book):
    PAPER_FILE.write_text(json.dumps(book, indent=2, ensure_ascii=False))


def rule_for(dte):
    """The take/stop pair config says applies, so paper and live never differ."""
    for max_dte, take, stop, _note in C.EXIT_RULES:
        if (dte or 0) <= max_dte:
            return take, abs(stop)
    return 40, 30


def record(payload, tier=None):
    """Open a paper position from an alert. -> the position, or None.

    Takes the first priced tier unless one is named. Refuses a contract it
    cannot price, rather than recording an entry it could never score.
    """
    pick = tier
    if pick is None:
        pick = next((t for t in payload.get("tiers") or []
                     if t.get("option_symbol") and t.get("ask")), None)
    if not pick:
        return None
    take, stop = rule_for(pick.get("dte"))
    book = _load()
    if any(p["option_symbol"] == pick["option_symbol"] and p["open"]
           for p in book["open"]):
        return None                     # already holding it on paper
    pos = {
        "ticker": payload.get("ticker", ""),
        "option_symbol": pick["option_symbol"],
        "strike": pick.get("strike"), "type": pick.get("type"),
        "expiry": pick.get("expiry"), "dte": pick.get("dte"),
        "tier": pick.get("tier", ""),
        "entry_date": datetime.date.today().isoformat(),
        "entry_minute": _now_et_minute(),
        "entry_ask": pick.get("ask"),
        "cost": pick.get("cost"),
        "take": take, "stop": stop,
        "score": payload.get("score"),
        "reasoning": [l["text"] for l in
                      (payload.get("reasoning") or {}).get("links", [])],
        "open": True,
    }
    book["open"].append(pos)
    _save(book)
    return pos


def mark(verbose=True):
    """Advance every open position on its own tape and close what triggered.

    A position is scored from the minute it was opened, not from the start of
    the session — entering at 14:05 and being judged on the 09:30 bar would
    score a trade nobody took.
    """
    book = _load()
    still_open, closed_now = [], []
    for pos in book["open"]:
        if not pos.get("open"):
            continue
        try:
            rows = uw.contract_intraday(pos["option_symbol"],
                                        date=pos["entry_date"])
        except uw.UWError as e:
            if verbose:
                print(f"  {pos['option_symbol']}: no tape ({e}) — held")
            still_open.append(pos)
            continue
        i = _index_of(rows, pos["entry_minute"])
        if i is None:
            still_open.append(pos)
            continue
        spread = measured_spread(rows)
        if spread is None:
            spread = 5.0                # the run's median; flagged on the row
            pos["spread_assumed"] = True
        trade = entry_exit(rows, i, pos["take"], pos["stop"], C.PAPER_MAX_HOLD,
                           spread, C.PAPER_HARD_EXIT)
        if trade is None:
            still_open.append(pos)
            continue
        pos.update({
            "open": False, "exit_price": round(trade["exit"], 4),
            "entry_price": round(trade["entry"], 4),
            "multiple": round(trade["exit"] / trade["entry"], 4),
            "why": trade["why"], "minutes": trade["minutes"],
            "spread_pct": round(spread, 1),
            "closed_at": datetime.datetime.now().isoformat(timespec="seconds"),
        })
        closed_now.append(pos)
        if verbose:
            pct = (pos["multiple"] - 1) * 100
            print(f"  CLOSED {pos['ticker']} {pos['option_symbol']} "
                  f"{pos['why']} {pct:+.1f}% after {pos['minutes']}m")
    book["open"] = still_open
    book["closed"].extend(closed_now)
    _save(book)
    return closed_now


def summary(book=None):
    """The running record, in the same terms as the backtest."""
    book = book or _load()
    done = book["closed"]
    if not done:
        return {"n": 0, "open": len(book["open"])}
    hit = sum(1 for p in done if p["why"] == "take") / len(done) * 100
    lost = sum(1 for p in done if p["multiple"] < 1.0) / len(done) * 100
    avg = statistics.mean(p["multiple"] for p in done)
    return {"n": len(done), "open": len(book["open"]), "hit": hit,
            "lost": lost, "avg": avg,
            "tickers": len({p["ticker"] for p in done})}


def report():
    s = summary()
    print(f"\n{'='*60}\nPAPER RECORD\n{'='*60}")
    if not s["n"]:
        print(f"  no closed positions yet ({s['open']} open)")
        return 0
    print(f"  {s['n']} closed, {s['open']} open, {s['tickers']} tickers\n")
    print(f"  reaches target : {s['hit']:.1f}%   "
          f"(backtest said {C.PAPER_BASELINE['hit']:.1f}%)")
    print(f"  ended at a loss: {s['lost']:.1f}%   "
          f"(backtest said {C.PAPER_BASELINE['lost']:.1f}%)")
    print(f"  per $1 staked  : ${s['avg']:.3f}   "
          f"(backtest said ${C.PAPER_BASELINE['avg']:.3f} equal-weighted)")
    if s["n"] < C.PAPER_MIN_TRADES:
        print(f"\n  {s['n']} trades. Below {C.PAPER_MIN_TRADES} this says "
              "nothing either way —\n  the backtest's own 5-of-9 sessions came "
              "from far more than this.")
    else:
        gap = s["avg"] - C.PAPER_BASELINE["avg"]
        print(f"\n  Live is {gap:+.3f} against the backtest. "
              + ("Consistent with it."
                 if abs(gap) < 0.05 else
                 "That gap is larger than the edge itself — the backtest was "
                 "measuring\n  its own assumptions, not the trade."))
    return 0


def _now_et_minute():
    return market.now_et().strftime("%H:%M")


def _index_of(rows, minute):
    """The first tape row at or after the minute the position was opened."""
    for i, r in enumerate(rows):
        if minute_of(r) >= minute:
            return i
    return None


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("action", choices=["mark", "report", "positions"],
                   help="mark: advance open positions; report: the record")
    args = p.parse_args(argv)
    if args.action == "mark":
        closed = mark()
        print(f"{len(closed)} closed")
        return report()
    if args.action == "positions":
        for pos in _load()["open"]:
            print(f"  {pos['ticker']:6s} {pos['option_symbol']}  "
                  f"opened {pos['entry_minute']} ET at ${pos['entry_ask']}  "
                  f"+{pos['take']}/-{pos['stop']}")
        return 0
    return report()


if __name__ == "__main__":
    sys.exit(main())
