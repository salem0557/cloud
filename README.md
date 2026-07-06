# US Options Screener

A command-line agent that scans US-listed stock options and surfaces the
contracts that best match a standard set of liquidity/pricing criteria:

- **Bid** / **Ask** (and the bid-ask spread)
- **Volume**
- **Open Interest**
- **Implied Volatility (IV)**
- **Delta**
- **Theta**

Data comes from Yahoo Finance (free, unofficial, delayed ~15-20 min).
Delta and Theta are **not** provided by Yahoo, so they are computed locally
with the Black-Scholes model from each contract's implied volatility, the
underlying's spot price, strike, and time to expiry.

## Install

```bash
pip install -r requirements.txt
```

## Usage

```bash
# Scan the S&P 500 (default universe), show top 25 by volume
python3 main.py

# Scan specific tickers, calls only, wider expiry window
python3 main.py --universe AAPL,MSFT,TSLA,NVDA --option-type call --min-dte 14 --max-dte 60

# Scan the entire US market (thousands of tickers - can take a long time
# against a free, rate-limited data source; consider --max-tickers for a
# quick test first)
python3 main.py --universe all --max-tickers 200 -v

# Save every matching contract, not just the top N shown on screen
python3 main.py --universe sp500 --top 20 --csv results.csv

# Generate a sortable/searchable HTML report - open results.html in any
# browser afterwards, no server needed (works like a lightweight spreadsheet)
python3 main.py --universe sp500 --html results.html
```

`--universe` accepts:
- `sp500` (default) - S&P 500 constituents, scraped from Wikipedia
- `all` - every stock/ETF listed on Nasdaq/NYSE/NYSE American
- a comma-separated list of tickers, e.g. `AAPL,MSFT`
- a path to a text file with one ticker per line

## Default filter thresholds

These are common, configurable screening conventions - not investment
advice - meant to surface liquid, reasonably priced, near-the-money
contracts a few weeks to two months out:

| Filter | Default | Flag |
|---|---|---|
| Days to expiry | 7-45 | `--min-dte` / `--max-dte` |
| Min volume | 100 | `--min-volume` |
| Min open interest | 500 | `--min-open-interest` |
| Max bid/ask spread | 10% of mid price | `--max-spread-pct` |
| IV range | 15%-100% | `--iv-min` / `--iv-max` |
| \|Delta\| range | 0.30-0.70 | `--delta-min` / `--delta-max` |
| Max theta burn | 5% of mid price/day | `--max-theta-pct` (pass `-1` to disable) |

Run `python3 main.py --help` for the full list of options, including
`--sort-by`, `--max-workers`, and `--request-delay` (throttling to avoid
getting rate-limited by Yahoo).

## Tests

```bash
python3 -m unittest discover -v tests
```

Tests cover the Black-Scholes Greeks math and the filter logic; they don't
require network access. Actually scanning the market does require outbound
HTTPS access to `query2.finance.yahoo.com` (and, for `--universe sp500` /
`all`, to Wikipedia / nasdaqtrader.com) - some sandboxed environments block
this, in which case the CLI runs but reports zero results.

## Disclaimer

This is a screening tool, not investment advice. Yahoo Finance data is
unofficial and delayed; verify prices with your broker before trading.
