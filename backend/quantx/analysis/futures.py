"""Futures basis / cost-of-carry heuristics."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class FuturesSummary:
    basis: Optional[float]
    basis_pct: Optional[float]
    cost_of_carry_signal: str
    summary: str


class FuturesAnalyzer:
    def analyze(self, spot: float, futures: Optional[float], days_to_expiry: int = 30) -> FuturesSummary:
        if futures is None or spot <= 0:
            return FuturesSummary(
                basis=None,
                basis_pct=None,
                cost_of_carry_signal="unavailable",
                summary="Futures price unavailable.",
            )
        basis = futures - spot
        basis_pct = basis / spot * 100
        annualized = basis_pct * (365 / max(days_to_expiry, 1))

        if basis_pct > 0.8:
            signal = "contango_rich"
            summary = f"Futures premium {basis_pct:.2f}% (ann. ~{annualized:.1f}%) — rich contango."
        elif basis_pct < -0.3:
            signal = "backwardation"
            summary = f"Futures discount {basis_pct:.2f}% — backwardation / bearish pressure."
        else:
            signal = "fair"
            summary = f"Basis {basis_pct:.2f}% near fair carry."

        return FuturesSummary(
            basis=round(basis, 2),
            basis_pct=round(basis_pct, 3),
            cost_of_carry_signal=signal,
            summary=summary,
        )
