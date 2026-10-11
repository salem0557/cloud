"""Order cost model, from the settings' fee table (Abyan's published schedule).

Commission is charged per order with a minimum, so a fill that completes an
order in pieces pays the order's commission once: each piece is charged the
difference between the commission on the cumulative quantity and on what was
already charged.
"""
from __future__ import annotations


def commission(fees: dict, qty: int, premium: float) -> float:
    if qty <= 0:
        return 0.0
    if premium <= fees["low_premium_threshold"]:
        per, minimum = fees["low_premium_per_contract"], fees["low_premium_min_per_order"]
    else:
        per, minimum = fees["per_contract"], fees["min_per_order"]
    return max(per * qty, minimum)


def order_fees(fees: dict, qty: int, premium: float) -> float:
    """Commission + VAT on it + per-contract third-party fees, USD."""
    if qty <= 0:
        return 0.0
    comm = commission(fees, qty, premium)
    vat = comm * fees.get("vat_pct", 0.0) / 100
    third = fees.get("third_party_per_contract", 0.0) * qty
    return round(comm + vat + third, 4)


def incremental_fees(fees: dict, prev_qty: int, new_qty: int, premium: float) -> float:
    return round(order_fees(fees, new_qty, premium) - order_fees(fees, prev_qty, premium), 4)
