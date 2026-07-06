from dataclasses import dataclass
from typing import Optional, Tuple

@dataclass
class ScreenerConfig:
    """Filter thresholds for the options screener."""
    
    option_types: Tuple[str, ...] = ("call", "put")
    min_dte: int = 7
    max_dte: int = 45
    
    # 1. تطبيق شرط (Volume >= 30)
    min_volume: int = 30
    
    # 2. تطبيق شرط (Open Interest >= 200)
    min_open_interest: int = 200
    
    max_bid_ask_spread_pct: float = 0.40
    
    iv_min: float = 0.15
    # 3. تطبيق شرط (IV < 0.60)
    iv_max: float = 0.60 
    
    delta_min: float = 0.30
    delta_max: float = 0.90
    
    max_theta_pct_of_price: Optional[float] = 0.05
    risk_free_rate: float = 0.045
    dividend_yield: float = 0.0
    require_positive_bid: bool = True
