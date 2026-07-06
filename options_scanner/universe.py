"""Resolve a ticker universe to scan: an index, a full market listing,
an explicit list, or a file."""

import os
from typing import List

import requests

SP500_WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"
NASDAQ_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_LISTED_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"

_REQUEST_TIMEOUT = 30


def get_sp500_tickers() -> List[str]:
    import pandas as pd  # local import: only needed for this path

    tables = pd.read_html(SP500_WIKI_URL)
    df = tables[0]
    return sorted(df["Symbol"].str.replace(".", "-", regex=False).tolist())


def get_all_us_listed_tickers() -> List[str]:
    """Every stock/ETF listed on Nasdaq, NYSE, and NYSE American/Arca.

    This is thousands of symbols; scanning all of them against a free,
    rate-limited data source can take a long time. Prefer a narrower
    universe (--universe sp500 or an explicit list) unless you really
    need full market coverage.
    """
    tickers = set()
    for url in (NASDAQ_LISTED_URL, OTHER_LISTED_URL):
        resp = requests.get(url, timeout=_REQUEST_TIMEOUT)
        resp.raise_for_status()
        lines = resp.text.splitlines()
        header = lines[0].split("|")
        data_lines = lines[1:-1]  # last line is a "File Creation Time" footer

        symbol_idx = header.index("Symbol") if "Symbol" in header else header.index("ACT Symbol")
        test_idx = header.index("Test Issue") if "Test Issue" in header else None

        for line in data_lines:
            row = line.split("|")
            if len(row) <= symbol_idx:
                continue
            if test_idx is not None and len(row) > test_idx and row[test_idx] == "Y":
                continue
            symbol = row[symbol_idx].strip()
            if symbol and "$" not in symbol and "." not in symbol:
                tickers.add(symbol)
    return sorted(tickers)


def load_tickers_from_file(path: str) -> List[str]:
    with open(path) as f:
        return [line.strip().upper() for line in f if line.strip() and not line.startswith("#")]


def resolve_universe(spec: str) -> List[str]:
    key = spec.strip().lower()
    if key == "sp500":
        return get_sp500_tickers()
    if key == "all":
        return get_all_us_listed_tickers()
    if "," in spec:
        return [t.strip().upper() for t in spec.split(",") if t.strip()]
    if os.path.exists(spec):
        return load_tickers_from_file(spec)
    # fall back to treating it as a single ticker
    return [spec.strip().upper()]
