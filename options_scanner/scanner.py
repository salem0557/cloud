import logging
import random
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from typing import List

from .config import ScreenerConfig
from .filters import OptionContract, passes_filters
from .greeks import black_scholes_greeks

logger = logging.getLogger(__name__)


def _dte(expiry_str: str) -> int:
    expiry = datetime.strptime(expiry_str, "%Y-%m-%d").date()
    return (expiry - date.today()).days


def _scan_ticker(ticker: str, cfg: ScreenerConfig, request_delay: float) -> List[OptionContract]:
    import yfinance as yf  # local import: keeps this module importable without the dep

    results: List[OptionContract] = []
    tk = yf.Ticker(ticker)

    try:
        spot = float(tk.fast_info["last_price"])
    except Exception as exc:
        logger.warning("skip %s: no spot price (%s)", ticker, exc)
        return results
    if not spot or spot <= 0:
        logger.warning("skip %s: invalid spot price", ticker)
        return results

    try:
        expirations = tk.options
    except Exception as exc:
        logger.warning("skip %s: no expirations (%s)", ticker, exc)
        return results

    for expiry in expirations:
        dte = _dte(expiry)
        if dte < cfg.min_dte or dte > cfg.max_dte:
            continue

        try:
            time.sleep(request_delay + random.uniform(0, request_delay))
            chain = tk.option_chain(expiry)
        except Exception as exc:
            logger.warning("skip %s %s: chain fetch failed (%s)", ticker, expiry, exc)
            continue

        for option_type, df in (("call", chain.calls), ("put", chain.puts)):
            if option_type not in cfg.option_types:
                continue
            for row in df.itertuples(index=False):
                bid = float(getattr(row, "bid", 0) or 0)
                ask = float(getattr(row, "ask", 0) or 0)
                volume = int(getattr(row, "volume", 0) or 0)
                open_interest = int(getattr(row, "openInterest", 0) or 0)
                iv = float(getattr(row, "impliedVolatility", 0) or 0)
                strike = float(getattr(row, "strike", 0) or 0)
                contract_symbol = getattr(row, "contractSymbol", "")

                greeks = black_scholes_greeks(
                    spot=spot,
                    strike=strike,
                    time_to_expiry_years=max(dte, 1) / 365.0,
                    risk_free_rate=cfg.risk_free_rate,
                    volatility=iv,
                    option_type=option_type,
                    dividend_yield=cfg.dividend_yield,
                )

                contract = OptionContract(
                    ticker=ticker,
                    contract_symbol=contract_symbol,
                    option_type=option_type,
                    expiry=datetime.strptime(expiry, "%Y-%m-%d").date(),
                    dte=dte,
                    strike=strike,
                    spot=spot,
                    bid=bid,
                    ask=ask,
                    volume=volume,
                    open_interest=open_interest,
                    iv=iv,
                    delta=greeks.delta,
                    theta=greeks.theta,
                )
                if passes_filters(contract, cfg):
                    results.append(contract)

    return results


def scan_universe(
    tickers: List[str],
    cfg: ScreenerConfig,
    max_workers: int = 8,
    request_delay: float = 0.25,
    progress_every: int = 25,
) -> List[OptionContract]:
    all_results: List[OptionContract] = []
    total = len(tickers)
    done = 0

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_scan_ticker, t, cfg, request_delay): t for t in tickers}
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                all_results.extend(future.result())
            except Exception as exc:
                logger.warning("ticker %s failed: %s", ticker, exc)
            done += 1
            if done % progress_every == 0 or done == total:
                logger.info("scanned %d/%d tickers, %d matches so far", done, total, len(all_results))

    return all_results
