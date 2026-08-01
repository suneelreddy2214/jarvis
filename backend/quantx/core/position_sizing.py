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
        quantity_as_lots: bool = False,
        risk_scale: float = 1.0,
    ) -> PositionSizeResult:
        """
        Calculate position size so that loss at stop ≈ risk_per_trade_pct of capital.

        When quantity_as_lots=True (F&O), quantity is number of lots and
        lot_size is the contract multiplier (e.g. NIFTY=25).
        risk_scale < 1 reduces size in elevated-volatility regimes.
        """
        scale = max(0.0, min(1.0, float(risk_scale)))
        risk_pct = self.settings.risk.max_risk_per_trade_pct * scale
        risk_amount = capital * (risk_pct / 100.0)

        stop_distance = abs(entry - stop_loss)
        if stop_distance <= 0 or scale <= 0:
            return PositionSizeResult(
                quantity=0,
                capital_at_risk=0.0,
                stop_distance=0.0,
                risk_pct=0.0,
                method=self.settings.position_sizing.method,
                notes="Invalid stop / risk scale — trade rejected",
            )

        method = self.settings.position_sizing.method
        notes = ""
        if scale < 0.999:
            notes = f"Size scaled {scale:.0%} for macro/vol"
        if method == "atr" and atr and atr > 0 and not quantity_as_lots:
            min_mult = float(getattr(self.settings.position_sizing, "min_stop_atr_mult", 2.0) or 2.0)
            min_stop = atr * min_mult
            if stop_distance < min_stop * 0.95:
                return PositionSizeResult(
                    quantity=0,
                    capital_at_risk=0.0,
                    stop_distance=stop_distance,
                    risk_pct=0.0,
                    method=method,
                    notes=(
                        f"Stop ₹{stop_distance:.2f} too tight vs {min_mult:.1f}×ATR "
                        f"(₹{min_stop:.2f}) — trade rejected"
                    ),
                )

        mult = max(1, int(lot_size))

        if quantity_as_lots:
            risk_per_lot = stop_distance * mult
            quantity = int(risk_amount // risk_per_lot) if risk_per_lot > 0 else 0
            if entry > 0 and broker_margin_pct > 0:
                # broker_margin_pct is % of notional blocked (12 for futures, 100 for long options)
                frac = broker_margin_pct / 100.0
                max_notional = capital / frac if frac > 0 else 0.0
                max_lots = int(max_notional // (entry * mult)) if entry * mult > 0 else 0
                if max_lots < quantity:
                    quantity = max(0, max_lots)
                    notes = (notes + "; " if notes else "") + "Capped by F&O margin"
            if max_quantity is not None:
                quantity = min(quantity, max_quantity)
            capital_at_risk = quantity * risk_per_lot
        else:
            raw_qty = risk_amount / stop_distance
            quantity = int(raw_qty // mult) * mult

            if broker_margin_pct < 100 and entry > 0:
                max_by_margin = int((capital * (broker_margin_pct / 100)) / (entry * mult)) * mult
                if max_by_margin < quantity:
                    quantity = max(0, max_by_margin)
                    notes = (notes + "; " if notes else "") + "Capped by broker margin"

            if max_quantity is not None:
                quantity = min(quantity, max_quantity)
            capital_at_risk = quantity * stop_distance

        if quantity <= 0:
            return PositionSizeResult(
                quantity=0,
                capital_at_risk=0.0,
                stop_distance=stop_distance,
                risk_pct=0.0,
                method=method,
                notes=notes or "Quantity rounds to zero — skip trade",
            )

        actual_risk_pct = (capital_at_risk / capital * 100) if capital else 0.0
        max_allowed = self.settings.risk.max_risk_per_trade_pct

        if actual_risk_pct > max_allowed + 0.01:
            if quantity_as_lots:
                risk_per_lot = stop_distance * mult
                quantity = int((capital * max_allowed / 100.0) // risk_per_lot) if risk_per_lot > 0 else 0
                capital_at_risk = quantity * risk_per_lot
            else:
                quantity = int(((capital * max_allowed / 100.0) / stop_distance) // mult) * mult
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
