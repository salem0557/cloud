from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass
class ScreenerConfig:
    """Filter thresholds for the options screener.

    Defaults follow common retail-trader screening conventions:
    liquid, moderately priced, near-the-money contracts within a
    few weeks to a couple of months of expiry.
    """

    option_types: Tuple[str, ...] = ("call", "put")
    min_dte: int = 7
    max_dte: int = 45

    min_volume: int = 100
    min_open_interest: int = 500

    max_bid_ask_spread_pct: float = 0.10  # (ask - bid) / mid

    iv_min: float = 0.15
    iv_max: float = 1.00

    delta_min: float = 0.30  # compared against abs(delta)
    delta_max: float = 0.70

    # |theta| / mid_price per day; None disables this filter
    max_theta_pct_of_price: Optional[float] = 0.05

    risk_free_rate: float = 0.045
    dividend_yield: float = 0.0

    require_positive_bid: bool = True

    # Underlying-stock RSI pre-filter: only scan tickers whose daily RSI is
    # at or below rsi_oversold_max (i.e. currently oversold). Set
    # rsi_oversold_max to None to disable this filter entirely.
    rsi_period: int = 14
    rsi_oversold_max: Optional[float] = 30.0
