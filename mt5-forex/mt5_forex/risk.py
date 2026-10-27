"""Risk sizing and validation helpers for MT5 forex.

All functions are pure where practical, so they can be unit-tested without MT5.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import floor, isfinite


@dataclass(frozen=True)
class VolumeSpec:
    minimum: float
    maximum: float
    step: float


@dataclass(frozen=True)
class RiskQuote:
    account_equity: float
    risk_percent: float
    loss_per_lot: float
    volume_spec: VolumeSpec


def volume_floor(volume: float, spec: VolumeSpec) -> float:
    if spec.step <= 0 or spec.minimum <= 0 or spec.maximum < spec.minimum:
        raise ValueError("Invalid broker volume specification")
    if not isfinite(volume) or volume <= 0:
        return 0.0
    # Avoid rounding up: volume must never exceed the calculated risk budget.
    steps = floor((volume + 1e-12) / spec.step)
    result = round(steps * spec.step, 8)
    if result < spec.minimum:
        return 0.0
    return min(result, spec.maximum)


def calculate_volume(quote: RiskQuote) -> tuple[float, float]:
    """Return (volume, estimated stop loss in account currency).

    loss_per_lot must be the positive loss returned by MT5 order_calc_profit
    for one lot from entry to stop. If the broker minimum breaches the risk
    budget the function returns (0, 0), refusing the trade.
    """
    values = (
        quote.account_equity, quote.risk_percent, quote.loss_per_lot,
        quote.volume_spec.minimum, quote.volume_spec.maximum,
        quote.volume_spec.step,
    )
    if not all(isfinite(v) for v in values):
        raise ValueError("Risk values must be finite")
    if quote.account_equity <= 0 or not 0 < quote.risk_percent <= 0.5:
        raise ValueError("risk_percent must be greater than 0 and at most 0.5")
    if quote.loss_per_lot <= 0:
        raise ValueError("loss_per_lot must be positive")

    budget = quote.account_equity * quote.risk_percent / 100.0
    volume = volume_floor(budget / quote.loss_per_lot, quote.volume_spec)
    if volume == 0:
        return 0.0, 0.0
    estimated_loss = volume * quote.loss_per_lot
    if estimated_loss > budget + max(1e-8, budget * 1e-9):
        # Defensive invariant. Never round volume up to satisfy a minimum.
        return 0.0, 0.0
    return volume, estimated_loss


def within_daily_loss_limit(start_equity: float, current_equity: float,
                            max_loss_percent: float) -> bool:
    """True only while drawdown from day-start equity is below the loss limit."""
    if (not all(isfinite(v) for v in
                (start_equity, current_equity, max_loss_percent))
            or start_equity <= 0 or current_equity < 0
            or not 0 < max_loss_percent < 100):
        return False
    loss = max(0.0, start_equity - current_equity)
    return loss < start_equity * max_loss_percent / 100.0
