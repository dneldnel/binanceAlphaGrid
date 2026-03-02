from __future__ import annotations

from statistics import pstdev


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def bps_change(current: float, reference: float) -> float:
    if reference == 0:
        return 0.0
    return (current - reference) / reference * 10000.0


def rolling_volatility_bps(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    returns = []
    for previous, current in zip(values, values[1:]):
        if previous <= 0:
            continue
        returns.append(((current - previous) / previous) * 10000.0)
    if len(returns) < 2:
        return abs(returns[0]) if returns else 0.0
    return float(pstdev(returns))


def fmt_price(value: float) -> str:
    if value >= 1000:
        return f"{value:,.2f}"
    if value >= 1:
        return f"{value:,.4f}"
    if value >= 0.01:
        return f"{value:,.6f}"
    return f"{value:,.8f}"
