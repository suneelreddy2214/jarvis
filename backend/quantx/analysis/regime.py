"""Market regime detection — trending / sideways / volatile / risk-off."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from quantx.analysis.technical import IndicatorSnapshot
from quantx.core.models import MarketDirection


class MarketRegime(str, Enum):
    TRENDING_UP = "TRENDING_UP"
    TRENDING_DOWN = "TRENDING_DOWN"
    SIDEWAYS = "SIDEWAYS"
    VOLATILE = "VOLATILE"
    RISK_OFF = "RISK_OFF"
    LOW_VOLUME = "LOW_VOLUME"


@dataclass
class RegimeSnapshot:
    regime: MarketRegime
    confidence: float
    adx: float
    atr_pct: float
    vix: Optional[float]
    volume_ratio: float
    summary: str
    preferred_families: list[str]

    def as_dict(self) -> dict:
        return {
            "regime": self.regime.value,
            "confidence": round(self.confidence, 1),
            "adx": round(self.adx, 2),
            "atr_pct": round(self.atr_pct, 3),
            "vix": self.vix,
            "volume_ratio": round(self.volume_ratio, 2),
            "summary": self.summary,
            "preferred_families": self.preferred_families,
        }


# Strategy family → preferred regimes
FAMILY_REGIMES: dict[str, list[MarketRegime]] = {
    "trend": [MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN],
    "momentum": [MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN, MarketRegime.VOLATILE],
    "breakout": [MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN, MarketRegime.VOLATILE],
    "mean_reversion": [MarketRegime.SIDEWAYS, MarketRegime.LOW_VOLUME],
    "price_action": [MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN, MarketRegime.SIDEWAYS],
    "smc": [MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN, MarketRegime.VOLATILE],
    "options_buy": [MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN, MarketRegime.VOLATILE],
    "options_sell": [MarketRegime.SIDEWAYS, MarketRegime.LOW_VOLUME],
    "volatility": [MarketRegime.VOLATILE, MarketRegime.RISK_OFF],
    "relative_strength": [MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN],
    "pairs": [MarketRegime.SIDEWAYS, MarketRegime.LOW_VOLUME],
    "fundamental": [MarketRegime.TRENDING_UP, MarketRegime.SIDEWAYS],
    "scalping": [MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN, MarketRegime.VOLATILE],
    "arbitrage": [MarketRegime.SIDEWAYS, MarketRegime.LOW_VOLUME, MarketRegime.VOLATILE],
    "event": [MarketRegime.VOLATILE, MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN],
    "news": [MarketRegime.VOLATILE, MarketRegime.TRENDING_UP, MarketRegime.TRENDING_DOWN],
    "quant": [
        MarketRegime.TRENDING_UP,
        MarketRegime.TRENDING_DOWN,
        MarketRegime.SIDEWAYS,
        MarketRegime.VOLATILE,
    ],
    "investing": [MarketRegime.TRENDING_UP, MarketRegime.SIDEWAYS, MarketRegime.LOW_VOLUME],
}


class RegimeDetector:
    def detect(
        self,
        snap: Optional[IndicatorSnapshot] = None,
        india_vix: Optional[float] = None,
        avoid_new_risk: bool = False,
    ) -> RegimeSnapshot:
        adx = float(snap.adx) if snap else 15.0
        atr_pct = float(snap.atr / snap.close * 100) if snap and snap.close else 1.0
        vol_ratio = float(snap.volume_ratio) if snap else 1.0
        trend = snap.trend if snap else MarketDirection.NEUTRAL

        if avoid_new_risk or (india_vix is not None and india_vix >= 22):
            regime = MarketRegime.RISK_OFF
            families = ["options_sell", "mean_reversion", "volatility", "pairs"]
            conf = 75.0 if india_vix and india_vix >= 22 else 70.0
            summary = f"Risk-off regime (VIX={india_vix}). Prefer hedges / reduced size."
        elif vol_ratio < 0.7 and adx < 18:
            regime = MarketRegime.LOW_VOLUME
            families = ["mean_reversion", "pairs", "options_sell"]
            conf = 65.0
            summary = "Low-volume regime — avoid aggressive breakouts."
        elif atr_pct >= 2.5 or (india_vix is not None and india_vix >= 18):
            regime = MarketRegime.VOLATILE
            families = ["options_buy", "breakout", "momentum", "smc", "volatility", "scalping", "news", "event"]
            conf = 70.0
            summary = f"Volatile regime (ATR {atr_pct:.2f}%). Favor defined-risk / options."
        elif adx >= 25 and trend == MarketDirection.BULLISH:
            regime = MarketRegime.TRENDING_UP
            families = [
                "trend",
                "momentum",
                "breakout",
                "relative_strength",
                "options_buy",
                "smc",
                "quant",
                "investing",
            ]
            conf = min(90.0, 55 + adx)
            summary = f"Trending up (ADX {adx:.1f}) — trend & momentum favored."
        elif adx >= 25 and trend == MarketDirection.BEARISH:
            regime = MarketRegime.TRENDING_DOWN
            families = ["trend", "momentum", "breakout", "options_buy", "smc", "quant"]
            conf = min(90.0, 55 + adx)
            summary = f"Trending down (ADX {adx:.1f}) — shorts / puts favored."
        elif adx < 20 or trend in (MarketDirection.RANGE_BOUND, MarketDirection.NEUTRAL):
            regime = MarketRegime.SIDEWAYS
            families = ["mean_reversion", "price_action", "options_sell", "pairs", "arbitrage", "investing", "quant"]
            conf = 68.0
            summary = f"Sideways / range (ADX {adx:.1f}) — mean reversion favored."
        else:
            regime = MarketRegime.SIDEWAYS
            families = ["mean_reversion", "price_action", "trend", "investing"]
            conf = 55.0
            summary = "Mixed regime — balanced strategy mix."

        return RegimeSnapshot(
            regime=regime,
            confidence=conf,
            adx=adx,
            atr_pct=atr_pct,
            vix=india_vix,
            volume_ratio=vol_ratio,
            summary=summary,
            preferred_families=families,
        )
