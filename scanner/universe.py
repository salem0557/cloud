"""Full US-listed common-stock symbol list (NYSE + Nasdaq + AMEX), used by
stocks_module.py to scan the whole market instead of a fixed watchlist --
the module then narrows it down with a price-range filter.

Source: the official Nasdaq Trader symbol directory files. ETFs, test
issues, warrants and units are excluded; only plain alphabetic tickers are
kept (these are the common shares yfinance handles reliably). Refreshed at
most once a day via an on-disk cache since the list barely changes.
"""
import io
import json
import logging
import time

import pandas as pd
import requests

from . import config

log = logging.getLogger(__name__)

NASDAQ_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/nasdaqlisted.txt"
OTHER_URL = "https://www.nasdaqtrader.com/dynamic/SymDir/otherlisted.txt"


def _fetch_file(url: str) -> pd.DataFrame:
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    df = pd.read_csv(io.StringIO(resp.text), sep="|")
    # Last line is a "File Creation Time" footer
    return df[~df.iloc[:, 0].astype(str).str.startswith("File Creation")]


def _clean(symbols: pd.Series) -> list[str]:
    out = []
    for sym in symbols.dropna().astype(str):
        sym = sym.strip().upper()
        # Plain alphabetic tickers only: skips warrants/units/preferred
        # (suffixed with $, ., ^ etc.) which are noise for this scanner.
        if sym.isalpha() and 1 <= len(sym) <= 5:
            out.append(sym)
    return out


def fetch_universe() -> list[str]:
    """Download the full US symbol list. Raises on network failure."""
    nasdaq = _fetch_file(NASDAQ_URL)
    other = _fetch_file(OTHER_URL)

    nasdaq = nasdaq[(nasdaq["Test Issue"] == "N") & (nasdaq["ETF"] == "N")]
    other = other[(other["Test Issue"] == "N") & (other["ETF"] == "N")]

    symbols = sorted(set(_clean(nasdaq["Symbol"]) + _clean(other["ACT Symbol"])))
    log.info("Universe: %d symbols", len(symbols))
    return symbols


def get_universe() -> list[str]:
    """Return the symbol list, using a daily on-disk cache."""
    try:
        with open(config.UNIVERSE_CACHE) as f:
            cache = json.load(f)
        age_hours = (time.time() - cache["fetched_at"]) / 3600
        if age_hours < config.UNIVERSE_MAX_AGE_HOURS and cache["symbols"]:
            return cache["symbols"]
    except (OSError, KeyError, ValueError):
        pass

    try:
        symbols = fetch_universe()
        with open(config.UNIVERSE_CACHE, "w") as f:
            json.dump({"fetched_at": time.time(), "symbols": symbols}, f)
        return symbols
    except Exception:
        log.exception("Universe refresh failed; falling back to stale cache")
        try:
            with open(config.UNIVERSE_CACHE) as f:
                return json.load(f)["symbols"]
        except (OSError, KeyError, ValueError):
            raise RuntimeError("No universe available (network down, no cache)")
