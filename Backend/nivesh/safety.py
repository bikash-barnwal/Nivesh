"""Server-side safety checks shared by paper and future broker execution."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RiskLimits:
    max_order_value: float = 100000.0
    max_position_value: float = 500000.0
    allow_live_orders: bool = False


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reasons: tuple[str, ...]


def check_order(
    price: float, quantity: int, current_position_value: float, limits: RiskLimits, live: bool = False
) -> RiskDecision:
    reasons = []
    value = price * quantity
    if price <= 0 or quantity <= 0:
        reasons.append("price and quantity must be positive")
    if value > limits.max_order_value:
        reasons.append("order exceeds max_order_value")
    if current_position_value + value > limits.max_position_value:
        reasons.append("position exceeds max_position_value")
    if live and not limits.allow_live_orders:
        reasons.append("live orders are disabled")
    return RiskDecision(not reasons, tuple(reasons))
