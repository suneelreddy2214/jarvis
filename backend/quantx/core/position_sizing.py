"""
Position sizing based on capital, ATR, and risk %.

Quantity = Risk Amount / (Stop Distance)
where Risk Amount = Capital * risk_per_trade_pct / 100
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from quantx.core.config import Settings, get_settings
from quantx.core.models import Side


@dataclass
class PositionSizeResult:
    quantity: int
    capital_at_risk: float
    stop_distance: float
    risk_pct: float
    method: str
    notes: str = ""


class PositionSizer:
    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()

    def calculate(
        self,
        capital: float,
        entry: float,
        stop_loss: float,
        side: Side,
        atr: Optional[float] = None,
        lot_size: int = 1,
        max_quantity: Optional[int] = None,
        broker_margin_pct: float = 100.0,
    ) -> PositionSizeResult:
        """
        Calculate position size so that loss at stop ≈ risk_per_trade_pct of capital.
        Never exceeds 1% risk (configurable).
        """
        risk_pct = self.settings.risk.max_risk_per_trade_pct
        risk_amount = capital * (risk_pct / 100.0)

        stop_distance = abs(entry - stop_loss)
        if stop_distance <= 0:
            return PositionSizeResult(
                quantity=0,
                capital_at_risk=0.0,
                stop_distance=0.0,
                risk_pct=0.0,
                method=self.settings.position_sizing.method,
                notes="Invalid stop distance — trade rejected",
            )

        # ATR-aware: ensure stop is at least atr_multiplier * ATR away when ATR provided
        method = self.settings.position_sizing.method
        notes = ""
        if method == "atr" and atr and atr > 0:
            min_stop = atr * self.settings.position_sizing.atr_multiplier
            if stop_distance < min_stop * 0.5:
                notes = f"Stop unusually tight vs ATR({atr:.2f}); size reduced for safety"
                # Use wider effective stop for sizing to avoid oversized positions
                stop_distance = max(stop_distance, min_stop * 0.5)

        raw_qty = risk_amount / stop_distance
        quantity = int(raw_qty // lot_size) * lot_size

        # Margin constraint (for futures/options leverage)
        if broker_margin_pct < 100 and entry > 0:
            max_by_margin = int((capital * (broker_margin_pct / 100)) / (entry * lot_size)) * lot_size
            if max_by_margin < quantity:
                quantity = max(0, max_by_margin)
                notes = (notes + "; " if notes else "") + "Capped by broker margin"

        if max_quantity is not None:
            quantity = min(quantity, max_quantity)

        if quantity <= 0:
            return PositionSizeResult(
                quantity=0,
                capital_at_risk=0.0,
                stop_distance=stop_distance,
                risk_pct=0.0,
                method=method,
                notes=notes or "Quantity rounds to zero — skip trade",
            )

        capital_at_risk = quantity * stop_distance
        actual_risk_pct = (capital_at_risk / capital * 100) if capital else 0.0

        # Hard cap: never exceed configured risk %
        if actual_risk_pct > risk_pct + 0.01:
            quantity = int((risk_amount / stop_distance) // lot_size) * lot_size
            capital_at_risk = quantity * stop_distance
            actual_risk_pct = (capital_at_risk / capital * 100) if capital else 0.0

        return PositionSizeResult(
            quantity=quantity,
            capital_at_risk=round(capital_at_risk, 2),
            stop_distance=round(stop_distance, 4),
            risk_pct=round(actual_risk_pct, 4),
            method=method,
            notes=notes,
        )
