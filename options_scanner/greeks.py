"""Black-Scholes delta/theta, computed locally since Yahoo Finance's
option chain does not include Greeks."""

import math
from dataclasses import dataclass


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


@dataclass
class Greeks:
    delta: float
    theta: float  # per calendar day


def black_scholes_greeks(
    spot: float,
    strike: float,
    time_to_expiry_years: float,
    risk_free_rate: float,
    volatility: float,
    option_type: str,
    dividend_yield: float = 0.0,
) -> Greeks:
    if spot <= 0 or strike <= 0 or time_to_expiry_years <= 0 or volatility <= 0:
        return Greeks(delta=0.0, theta=0.0)

    S, K, T, r, q, sigma = spot, strike, time_to_expiry_years, risk_free_rate, dividend_yield, volatility
    sqrt_t = math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma ** 2) * T) / (sigma * sqrt_t)
    d2 = d1 - sigma * sqrt_t

    is_call = option_type.lower().startswith("c")
    if is_call:
        delta = math.exp(-q * T) * _norm_cdf(d1)
        theta_year = (
            -S * math.exp(-q * T) * _norm_pdf(d1) * sigma / (2 * sqrt_t)
            - r * K * math.exp(-r * T) * _norm_cdf(d2)
            + q * S * math.exp(-q * T) * _norm_cdf(d1)
        )
    else:
        delta = math.exp(-q * T) * (_norm_cdf(d1) - 1.0)
        theta_year = (
            -S * math.exp(-q * T) * _norm_pdf(d1) * sigma / (2 * sqrt_t)
            + r * K * math.exp(-r * T) * _norm_cdf(-d2)
            - q * S * math.exp(-q * T) * _norm_cdf(-d1)
        )

    return Greeks(delta=delta, theta=theta_year / 365.0)
