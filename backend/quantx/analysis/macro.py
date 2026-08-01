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
    size_multiplier: float = 1.0  # scale position size in elevated / complacent vol
    require_extra_confirmation: bool = False
    caution_flags: list[str] = field(default_factory=list)
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
            if india_vix >= 20:
                ctx.vix_regime = "elevated"
                ctx.avoid_new_risk = True
                ctx.size_multiplier = 0.0
                ctx.caution_flags.append("vix_elevated")
                parts.append(f"India VIX {india_vix:.1f} elevated — avoid fresh risk")
            elif india_vix >= 18:
                ctx.vix_regime = "elevated"
                ctx.size_multiplier = 0.5
                ctx.require_extra_confirmation = True
                ctx.caution_flags.append("vix_caution")
                parts.append(f"India VIX {india_vix:.1f} caution — size down 50%")
            elif india_vix >= 15:
                ctx.vix_regime = "moderate"
                ctx.size_multiplier = 0.75
                parts.append(f"India VIX {india_vix:.1f} moderate")
            else:
                # Complacency zone: calm VIX often precedes sharp swings — do NOT full-size
                ctx.vix_regime = "complacent"
                ctx.size_multiplier = min(ctx.size_multiplier, 0.65)
                ctx.require_extra_confirmation = True
                ctx.caution_flags.append("vix_complacency")
                parts.append(
                    f"India VIX {india_vix:.1f} calm (complacency) — size down, demand confluence"
                )

        if usdinr_change_pct is not None:
            ctx.details["usdinr_change_pct"] = usdinr_change_pct
            if usdinr_change_pct >= 0.3:
                ctx.usdinr_bias = "inr_weak"
                ctx.caution_flags.append("inr_weak")
                ctx.require_extra_confirmation = True
                ctx.size_multiplier = min(ctx.size_multiplier, 0.7)
                parts.append(
                    f"USDINR +{usdinr_change_pct:.2f}% (INR weak) — FX pressure, size down equities"
                )
            elif usdinr_change_pct <= -0.3:
                ctx.usdinr_bias = "inr_strong"
                ctx.caution_flags.append("inr_strong")
                # Firm INR with a weak tape can still hurt exporters / risk appetite
                if ctx.nifty_bias == "bearish":
                    ctx.size_multiplier = min(ctx.size_multiplier, 0.6)
                    ctx.require_extra_confirmation = True
                    parts.append(
                        f"USDINR {usdinr_change_pct:.2f}% (INR firm) + weak Nifty — caution"
                    )
                else:
                    ctx.size_multiplier = min(ctx.size_multiplier, 0.85)
                    parts.append(
                        f"USDINR {usdinr_change_pct:.2f}% (INR firm) — mild size caution"
                    )

        # Combined macro risk-off rules
        if ctx.nifty_bias == "bearish" and (india_vix or 0) >= 18:
            ctx.global_risk = "risk_off"
            ctx.avoid_new_risk = True
            ctx.size_multiplier = 0.0
            if "avoid fresh risk" not in " ".join(parts).lower():
                parts.append("Bearish Nifty + elevated vol — avoid fresh risk")
        elif ctx.nifty_bias == "bearish" and ctx.usdinr_bias == "inr_weak":
            ctx.global_risk = "risk_off"
            ctx.avoid_new_risk = True
            ctx.size_multiplier = 0.0
            parts.append("Bearish Nifty + weak INR — avoid fresh risk")
        elif ctx.nifty_bias == "bearish":
            ctx.global_risk = "risk_off"
            ctx.size_multiplier = min(ctx.size_multiplier, 0.5)
            ctx.require_extra_confirmation = True
        elif ctx.nifty_bias == "bullish" and ctx.vix_regime == "complacent":
            # Do not treat calm VIX as all-clear risk-on
            ctx.global_risk = "cautious"
            ctx.require_extra_confirmation = True
        elif ctx.nifty_bias == "bullish" and ctx.vix_regime in ("moderate", "low"):
            ctx.global_risk = "risk_on"

        ctx.summary = "; ".join(parts) if parts else "Macro data limited — defaulting to neutral risk posture."
        return ctx
