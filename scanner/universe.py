"""Build the list of all US-listed common stocks (NYSE + Nasdaq + AMEX).

Source: the official Nasdaq Trader symbol directory files, refreshed daily.
ETFs, test issues, warrants and units are excluded; only plain alphabetic
tickers are kept (these are the common shares yfinance handles reliably).
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

# Top cryptocurrencies by market cap, as Yahoo Finance symbols. Curated
# instead of fetched: Yahoo appends a CoinMarketCap id to coins whose ticker
# collides with another asset (e.g. Uniswap is UNI7083-USD), so a naive
# SYMBOL-USD list would silently hit the wrong asset. Unknown symbols simply
# return no data and are skipped, which is safe. Extend via CRYPTO_EXTRA.
CRYPTO_SYMBOLS = [
    # majors
    "BTC-USD", "ETH-USD", "XRP-USD", "BNB-USD", "SOL-USD", "DOGE-USD",
    "ADA-USD", "TRX-USD", "AVAX-USD", "LINK-USD", "XLM-USD", "SHIB-USD",
    "DOT-USD", "HBAR-USD", "BCH-USD", "LTC-USD", "NEAR-USD", "ICP-USD",
    "AAVE-USD", "ETC-USD", "XMR-USD", "VET-USD", "ATOM-USD", "ALGO-USD",
    "FIL-USD", "OP-USD", "FET-USD", "INJ-USD", "LDO-USD", "RUNE-USD",
    "QNT-USD", "EGLD-USD", "FLOW-USD", "XTZ-USD", "CRV-USD", "MKR-USD",
    "SNX-USD", "COMP-USD", "YFI-USD", "SUSHI-USD", "1INCH-USD", "CAKE-USD",
    "THETA-USD", "KAVA-USD", "MINA-USD", "AXS-USD", "DYDX-USD", "AR-USD",
    "ROSE-USD", "CELO-USD", "ANKR-USD", "GALA-USD", "SAND-USD", "MANA-USD",
    "ENJ-USD", "CHZ-USD", "ZEC-USD", "DASH-USD", "EOS-USD", "NEO-USD",
    "KSM-USD", "ZIL-USD", "BAT-USD", "IOTA-USD", "UMA-USD", "BAND-USD",
    "COTI-USD", "OCEAN-USD", "STORJ-USD", "SKL-USD", "GLM-USD", "LRC-USD",
    # tickers that collide with other assets: Yahoo id-suffixed forms
    "TON11419-USD",   # Toncoin
    "UNI7083-USD",    # Uniswap
    "GRT6719-USD",    # The Graph
    "PEPE24478-USD",  # Pepe
    "SUI20947-USD",   # Sui
    "APT21794-USD",   # Aptos
    "ARB11841-USD",   # Arbitrum
    "IMX10603-USD",   # Immutable
    "STX4847-USD",    # Stacks
    "SEI23149-USD",   # Sei
    "TIA22861-USD",   # Celestia
    "TAO22974-USD",   # Bittensor
    "BONK23095-USD",  # Bonk
    "FLOKI10804-USD", # Floki
    "MNT27075-USD",   # Mantle
    "BEAM28298-USD",  # Beam
]


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


def get_crypto_universe() -> list[str]:
    """Top coins (Yahoo symbols) plus any user-added CRYPTO_EXTRA symbols."""
    extra = [s if s.endswith("-USD") else f"{s}-USD" for s in config.CRYPTO_EXTRA]
    return sorted(set(CRYPTO_SYMBOLS + extra))


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
