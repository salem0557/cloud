from dataclasses import dataclass
from datetime import date
from typing import Optional

from .config import ScreenerConfig


@dataclass
class OptionContract:
    ticker: str
    contract_symbol: str
    option_type: str  # "call" or "put"
    expiry: date
    dte: int
    strike: float
    spot: float
    bid: float
    ask: float
    volume: int
    open_interest: int
    iv: float
    delta: float
    theta: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0 if (self.bid or self.ask) else 0.0

    @property
    def spread_pct(self) -> Optional[float]:
        if self.mid <= 0:
            return None
        return (self.ask - self.bid) / self.mid

    @property
    def theta_pct(self) -> Optional[float]:
        if self.mid <= 0:
            return None
        return abs(self.theta) / self.mid


def passes_filters(c: OptionContract, cfg: ScreenerConfig) -> bool:
    if cfg.require_positive_bid and c.bid <= 0:
        return False
    if not (cfg.min_dte <= c.dte <= cfg.max_dte):
        return False
    if c.volume < cfg.min_volume:
        return False
    if c.open_interest < cfg.min_open_interest:
        return False

    spread = c.spread_pct
    if spread is None or spread > cfg.max_bid_ask_spread_pct:
        return False

    if not (cfg.iv_min <= c.iv <= cfg.iv_max):
        return False

    if not (cfg.delta_min <= abs(c.delta) <= cfg.delta_max):
        return False

    if cfg.max_theta_pct_of_price is not None:
        theta_pct = c.theta_pct
        if theta_pct is None or theta_pct > cfg.max_theta_pct_of_price:
            return False

    return True
