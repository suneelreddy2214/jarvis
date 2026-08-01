"""Select strategies for current regime using learned weights (additive catalog)."""

from __future__ import annotations

from typing import Optional

from quantx.analysis.learning import StrategyLearner
from quantx.analysis.regime import FAMILY_REGIMES, MarketRegime, RegimeSnapshot
from quantx.analysis.strategies import STRATEGIES, list_strategies
from quantx.core.models import TradeType


# Default core + strong additive picks per product when learning is cold
DEFAULT_BY_PRODUCT: dict[str, list[str]] = {
    "SWING": [
        "swing_trend",
        "breakout",
        "ema_cross_9_21",
        "supertrend",
        "donchian_breakout",
        "smc_bos_fvg",
        "relative_strength",
    ],
    "INTRADAY": [
        "intraday_mean_reversion",
        "rsi_momentum",
        "vwap_bias",
        "opening_range_proxy",
        "bollinger_reversion",
    ],
    "FUTURES": ["futures_trend", "supertrend", "donchian_breakout", "adx_trend_strength"],
    "OPTIONS": [
        "options_directional",
        "volatility_regime",
        "oi_pcr_bias",
        "options_straddle_bias",
    ],
}


def select_strategies_for_regime(
    regime: RegimeSnapshot,
    trade_type: TradeType | str,
    learner: Optional[StrategyLearner] = None,
    limit: int = 3,
) -> list[str]:
    """Pick top strategies for product + regime, ranked by learned weight."""
    tt = trade_type.value if isinstance(trade_type, TradeType) else str(trade_type)
    preferred_families = set(regime.preferred_families)
    weights = learner.weights() if learner else {}

    candidates: list[tuple[float, str]] = []
    for meta in list_strategies():
        if meta["trade_type"] != tt and not (
            tt == "SWING" and meta["trade_type"] in ("SWING",)
        ):
            # Allow SWING catalog for SWING; exact match otherwise
            if meta["trade_type"] != tt:
                continue
        fam = meta.get("family") or "core"
        name = meta["name"]
        # Family fit
        family_ok = fam in preferred_families or fam == "core"
        if not family_ok and regime.regime != MarketRegime.RISK_OFF:
            # still allow defaults
            if name not in DEFAULT_BY_PRODUCT.get(tt, []):
                continue
        w = float(weights.get(name, 1.0))
        if fam in preferred_families:
            w *= 1.25
        if name in DEFAULT_BY_PRODUCT.get(tt, []):
            w *= 1.1
        # Skip very weak learned strategies
        if w < 0.35 and weights.get(name) is not None:
            continue
        candidates.append((w, name))

    if not candidates:
        return list(DEFAULT_BY_PRODUCT.get(tt, ["swing_trend"]))[:limit]

    candidates.sort(key=lambda x: -x[0])
    # unique preserve order
    out: list[str] = []
    for _, name in candidates:
        if name not in out:
            out.append(name)
        if len(out) >= limit:
            break
    return out
