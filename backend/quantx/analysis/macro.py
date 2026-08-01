"""Macro / sentiment context (indices, VIX, USDINR proxies)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class MacroContext:
    nifty_bias: str = "neutral"
    vix_level: Optional[float] = None
    vix_regime: str = "unknown"
    usdinr_bias: str = "neutral"
    global_risk: str = "neutral"
    summary: str = ""
    avoid_new_risk: bool = False
    details: dict[str, Any] = field(default_factory=dict)


class MacroAnalyzer:
    def analyze(
        self,
        nifty_change_pct: Optional[float] = None,
        india_vix: Optional[float] = None,
        usdinr_change_pct: Optional[float] = None,
        sgx_nifty_change_pct: Optional[float] = None,
    ) -> MacroContext:
        ctx = MacroContext()
        parts: list[str] = []

        if nifty_change_pct is not None:
            ctx.details["nifty_change_pct"] = nifty_change_pct
            if nifty_change_pct >= 0.5:
                ctx.nifty_bias = "bullish"
            elif nifty_change_pct <= -0.5:
                ctx.nifty_bias = "bearish"
            parts.append(f"Nifty {nifty_change_pct:+.2f}%")

        if sgx_nifty_change_pct is not None:
            ctx.details["sgx_nifty_change_pct"] = sgx_nifty_change_pct
            parts.append(f"SGX/GIFT Nifty {sgx_nifty_change_pct:+.2f}%")

        if india_vix is not None:
            ctx.vix_level = india_vix
            ctx.details["india_vix"] = india_vix
            if india_vix >= 25:
                ctx.vix_regime = "elevated"
                ctx.avoid_new_risk = True
                parts.append(f"India VIX {india_vix:.1f} high — avoid fresh risk")
            elif india_vix >= 20:
                # Caution zone: size down via scoring, but do not hard-block every setup
                ctx.vix_regime = "elevated"
                parts.append(f"India VIX {india_vix:.1f} elevated — trade selectively")
            elif india_vix >= 15:
                ctx.vix_regime = "moderate"
                parts.append(f"India VIX {india_vix:.1f} moderate")
            else:
                ctx.vix_regime = "low"
                parts.append(f"India VIX {india_vix:.1f} calm")

        if usdinr_change_pct is not None:
            ctx.details["usdinr_change_pct"] = usdinr_change_pct
            if usdinr_change_pct >= 0.3:
                ctx.usdinr_bias = "inr_weak"
                parts.append(f"USDINR +{usdinr_change_pct:.2f}% (INR weak)")
            elif usdinr_change_pct <= -0.3:
                ctx.usdinr_bias = "inr_strong"
                parts.append(f"USDINR {usdinr_change_pct:.2f}% (INR firm)")

        # Hard risk-off only when trend is down AND vol is clearly elevated
        if ctx.nifty_bias == "bearish" and ctx.vix_regime == "elevated" and (india_vix or 0) >= 22:
            ctx.global_risk = "risk_off"
            ctx.avoid_new_risk = True
        elif ctx.nifty_bias == "bullish" and ctx.vix_regime == "low":
            ctx.global_risk = "risk_on"

        ctx.summary = "; ".join(parts) if parts else "Macro data limited — defaulting to neutral risk posture."
        return ctx
