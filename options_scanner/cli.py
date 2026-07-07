import argparse
import csv
import logging
import sys
from typing import List, Sequence

from .config import ScreenerConfig
from .filters import OptionContract
from .report import write_html
from .scanner import scan_universe
from .universe import resolve_universe

SORT_KEYS = {
    "bid": lambda c: c.bid,
    "ask": lambda c: c.ask,
    "volume": lambda c: c.volume,
    "open_interest": lambda c: c.open_interest,
    "iv": lambda c: c.iv,
    "delta": lambda c: abs(c.delta),
    "theta": lambda c: abs(c.theta),
    "spread_pct": lambda c: c.spread_pct or 0,
    "rsi": lambda c: c.rsi if c.rsi is not None else 0,
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="US options market screener")
    p.add_argument(
        "--universe",
        default="sp500",
        help="sp500 | all | comma,separated,tickers | path/to/file.txt (default: sp500)",
    )
    p.add_argument("--option-type", choices=["call", "put", "both"], default="both")
    p.add_argument("--min-dte", type=int, default=7, help="minimum days to expiry")
    p.add_argument("--max-dte", type=int, default=45, help="maximum days to expiry")
    p.add_argument("--min-volume", type=int, default=100)
    p.add_argument("--min-open-interest", type=int, default=500)
    p.add_argument("--max-spread-pct", type=float, default=0.10, help="(ask-bid)/mid ceiling, e.g. 0.10 = 10%%")
    p.add_argument("--iv-min", type=float, default=0.15, help="e.g. 0.15 = 15%%")
    p.add_argument("--iv-max", type=float, default=1.00)
    p.add_argument("--delta-min", type=float, default=0.30, help="compared against abs(delta)")
    p.add_argument("--delta-max", type=float, default=0.70)
    p.add_argument(
        "--max-theta-pct", type=float, default=0.05,
        help="max |theta|/mid_price per day; pass -1 to disable this filter",
    )
    p.add_argument("--risk-free-rate", type=float, default=0.045)
    p.add_argument("--rsi-period", type=int, default=14, help="RSI lookback period (daily bars)")
    p.add_argument(
        "--rsi-oversold-max", type=float, default=30,
        help="only scan tickers whose RSI is <= this (oversold); pass -1 to disable the RSI filter",
    )
    p.add_argument("--top", type=int, default=25, help="how many contracts to display")
    p.add_argument("--sort-by", choices=list(SORT_KEYS), default="volume")
    p.add_argument("--max-workers", type=int, default=8)
    p.add_argument("--request-delay", type=float, default=0.25, help="base delay (s) between chain requests per worker")
    p.add_argument("--max-tickers", type=int, default=None, help="cap universe size, useful for a quick test run")
    p.add_argument("--csv", dest="csv_path", default=None, help="write all matching contracts to this CSV path")
    p.add_argument(
        "--html", dest="html_path", default=None,
        help="write all matching contracts to a self-contained, sortable/searchable HTML report "
             "(open the file directly in a browser, no server needed)",
    )
    p.add_argument("-v", "--verbose", action="store_true")
    return p


def main(argv: Sequence[str] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    option_types = ("call", "put") if args.option_type == "both" else (args.option_type,)
    max_theta = None if args.max_theta_pct < 0 else args.max_theta_pct
    rsi_oversold_max = None if args.rsi_oversold_max < 0 else args.rsi_oversold_max

    cfg = ScreenerConfig(
        option_types=option_types,
        min_dte=args.min_dte,
        max_dte=args.max_dte,
        min_volume=args.min_volume,
        min_open_interest=args.min_open_interest,
        max_bid_ask_spread_pct=args.max_spread_pct,
        iv_min=args.iv_min,
        iv_max=args.iv_max,
        delta_min=args.delta_min,
        delta_max=args.delta_max,
        max_theta_pct_of_price=max_theta,
        risk_free_rate=args.risk_free_rate,
        rsi_period=args.rsi_period,
        rsi_oversold_max=rsi_oversold_max,
    )

    print(f"Resolving universe '{args.universe}'...", file=sys.stderr)
    tickers = resolve_universe(args.universe)
    if args.max_tickers:
        tickers = tickers[: args.max_tickers]
    print(f"Scanning {len(tickers)} ticker(s)...", file=sys.stderr)

    results = scan_universe(
        tickers, cfg, max_workers=args.max_workers, request_delay=args.request_delay
    )
    results.sort(key=SORT_KEYS[args.sort_by], reverse=True)
    top = results[: args.top]

    print_table(top)
    print(f"\n{len(results)} contract(s) matched all filters (showing top {len(top)}).", file=sys.stderr)

    if args.csv_path:
        write_csv(results, args.csv_path)
        print(f"Full results written to {args.csv_path}", file=sys.stderr)

    if args.html_path:
        write_html(results, args.html_path)
        print(f"HTML report written to {args.html_path} (open it in a browser)", file=sys.stderr)

    return 0


def print_table(contracts: List[OptionContract]) -> None:
    headers = ["Ticker", "Type", "Expiry", "DTE", "Strike", "Bid", "Ask",
               "Volume", "OpenInt", "IV", "Delta", "Theta", "RSI"]
    rows = [
        [
            c.ticker, c.option_type, c.expiry.isoformat(), c.dte, f"{c.strike:g}",
            f"{c.bid:.2f}", f"{c.ask:.2f}", c.volume, c.open_interest,
            f"{c.iv:.2%}", f"{c.delta:.3f}", f"{c.theta:.3f}",
            f"{c.rsi:.1f}" if c.rsi is not None else "-",
        ]
        for c in contracts
    ]

    try:
        from tabulate import tabulate

        print(tabulate(rows, headers=headers, tablefmt="github"))
        return
    except ImportError:
        pass

    all_rows = [headers] + rows
    widths = [max(len(str(row[i])) for row in all_rows) for i in range(len(headers))]
    print(" | ".join(h.ljust(w) for h, w in zip(headers, widths)))
    print("-+-".join("-" * w for w in widths))
    for row in rows:
        print(" | ".join(str(v).ljust(w) for v, w in zip(row, widths)))


def write_csv(contracts: List[OptionContract], path: str) -> None:
    headers = [
        "ticker", "contract_symbol", "option_type", "expiry", "dte", "strike",
        "spot", "bid", "ask", "mid", "spread_pct", "volume", "open_interest",
        "iv", "delta", "theta", "rsi",
    ]
    with open(path, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(headers)
        for c in contracts:
            writer.writerow([
                c.ticker, c.contract_symbol, c.option_type, c.expiry.isoformat(), c.dte,
                c.strike, c.spot, c.bid, c.ask, c.mid, c.spread_pct, c.volume,
                c.open_interest, c.iv, c.delta, c.theta, c.rsi,
            ])
