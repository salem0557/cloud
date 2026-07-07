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
| Underlying RSI (oversold) | RSI(14) <= 30 | `--rsi-period` / `--rsi-oversold-max` (pass `-1` to disable) |

The RSI filter runs on the *underlying stock* (daily closes, Wilder's
smoothing - the standard RSI most platforms use), before its option chain
is even fetched: tickers that aren't currently oversold are skipped
entirely, and the underlying's RSI at scan time is shown as its own
column on every contract that ticker produces.

Run `python3 main.py --help` for the full list of options, including
`--sort-by`, `--max-workers`, and `--request-delay` (throttling to avoid
getting rate-limited by Yahoo).

## Live web dashboard (deploy to Railway)

`app.py` wraps the same scanner in a small Flask app that rescans in a
background loop and serves the latest results as a self-refreshing page -
useful if you want a shareable link instead of running the CLI yourself
each time.

**Deploy:**
1. Push this repo to your own GitHub account (or use this one directly).
2. On [railway.app](https://railway.app): **New Project -> Deploy from GitHub repo** -> select this repo/branch.
3. Railway auto-detects Python and uses `Procfile` / `railway.json` to run `python3 app.py`. No extra setup needed.
4. **In the service's Settings, set a Cron Schedule of `Hourly M-F`** (`0 * * * MON-FRI`). This is what makes the shut-down-outside-market-hours behavior below actually work - see the next section.
5. Once deployed, open the public URL Railway gives you (Settings -> Networking -> Generate Domain). Share that link with anyone.

**What it does:** the first load shows a "scanning..." page that
auto-refreshes every 10s; once the first scan cycle completes, the page
shows results and refreshes itself every 60s. The scan loop keeps running
continuously in the background (no artificial delay between cycles by
default), so the page stays close to current while the app is running.

**Automatic shutdown outside market hours (cost saving):** the app checks
whether it's currently within regular US market hours (9:30-16:00
America/New_York, Mon-Fri) before every scan cycle. Outside that window it
exits the whole process with code 0 instead of idling. Combined with
Railway's `Hourly M-F` Cron Schedule and `restartPolicyType: ON_FAILURE`
(already set in `railway.json`, so Railway won't auto-restart a clean
exit), the net effect is:
- Every hour, Mon-Fri, Railway tries to (re)start the service.
- If the market is closed at that moment, the app exits again within a
  second or two - negligible cost.
- If the market is open, the app stays up, scanning and serving, until it
  detects the close and shuts itself down.
- **Trade-off you explicitly chose:** the link is unreachable outside
  market hours (no fallback snapshot). It typically comes online within
  an hour of the 9:30 ET open, aligned to whichever hourly tick lands
  first after that - use `Customize` on the Cron Schedule (e.g. a 15-minute
  cadence) if you want it to come online closer to 9:30 sharp; off-hour
  triggers still cost almost nothing since the app exits immediately.
- To go back to a normal always-on dashboard instead, set the
  `SHUTDOWN_OUTSIDE_MARKET_HOURS=false` environment variable and remove
  the Cron Schedule.

**Configuration** (Railway -> your service -> Variables): all the same
knobs as the CLI, as environment variables - `UNIVERSE`, `OPTION_TYPE`,
`MIN_DTE`, `MAX_DTE`, `MIN_VOLUME`, `MIN_OPEN_INTEREST`, `MAX_SPREAD_PCT`,
`IV_MIN`, `IV_MAX`, `DELTA_MIN`, `DELTA_MAX`, `MAX_THETA_PCT`, `RSI_PERIOD`,
`RSI_OVERSOLD_MAX`, `TOP_N`, `MAX_WORKERS`, `REQUEST_DELAY`, `MAX_TICKERS`, `MIN_CYCLE_SECONDS`,
`SHUTDOWN_OUTSIDE_MARKET_HOURS`.

**Careful before you set-and-forget this:**
- Scanning `sp500` (or `all`) back-to-back is a lot of load against a
  free, unofficial API - Yahoo may rate-limit or temporarily block the
  server's IP. If that happens, raise `REQUEST_DELAY` and/or set
  `MIN_CYCLE_SECONDS` to a few hundred seconds, or shrink `UNIVERSE` to a
  smaller ticker list.
- The market-hours check ignores exchange holidays (Thanksgiving,
  Christmas, etc.) - the app may briefly wake up and find nothing useful
  to do on those days, then shut back down within one cycle.
- To ship an update after the first deploy, just push to the branch
  Railway is tracking - it redeploys automatically.

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
