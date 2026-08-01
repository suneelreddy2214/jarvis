"""
AI market opportunity hunter.

Screens a broad NSE universe for opportunistic symbols, then processes
winners through regime-selected strategies / trading styles.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from quantx.analysis.fno import DEFAULT_FO_UNIVERSE, is_index
from quantx.analysis.learning import StrategyLearner
from quantx.analysis.option_chain import SEARCH_UNIVERSE
from quantx.analysis.regime import RegimeDetector
from quantx.analysis.selector import select_strategies_for_regime
from quantx.analysis.strategies import STRATEGIES
from quantx.analysis.technical import TechnicalAnalyzer
from quantx.analysis.trading_styles import TRADING_STYLES, resolve_style_ids, style_strategy_jobs
from quantx.core.engine import QuantXEngine
from quantx.core.models import TradeRecommendation, TradeType
from quantx.data.market_data import MarketDataService
from quantx.portfolio.db import Database

logger = logging.getLogger(__name__)

# Broad hunt universe (cash + F&O underlyings)
HUNT_UNIVERSE: list[str] = sorted(
    set(SEARCH_UNIVERSE)
    | set(DEFAULT_FO_UNIVERSE)
    | {
        "BEL",
        "HAL",
        "IRCTC",
        "PFC",
        "RECLTD",
        "M&M",
        "HEROMOTOCO",
        "EICHERMOT",
        "INDUSINDBK",
        "TECHM",
        "HCLTECH",
        "BPCL",
        "IOC",
        "GRASIM",
        "CIPLA",
        "DRREDDY",
        "DIVISLAB",
        "BAJAJFINSV",
        "SBILIFE",
        "HDFCLIFE",
        "APOLLOHOSP",
        "DLF",
        "GODREJCP",
        "BRITANNIA",
        "PIDILITIND",
        "HAVELLS",
        "SIEMENS",
        "ABB",
        "TRENT",
        "ZOMATO",
        "PAYTM",
        "NYKAA",
        "POLICYBZR",
    }
)


@dataclass
class HuntCandidate:
    symbol: str
    score: float
    change_pct: float
    volume_ratio: float
    adx: float
    rsi: float
    atr_pct: float
    reasons: list[str] = field(default_factory=list)
    is_index: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "score": round(self.score, 1),
            "change_pct": round(self.change_pct, 2),
            "volume_ratio": round(self.volume_ratio, 2),
            "adx": round(self.adx, 1),
            "rsi": round(self.rsi, 1),
            "atr_pct": round(self.atr_pct, 2),
            "reasons": self.reasons,
            "is_index": self.is_index,
        }


class OpportunityHunter:
    """Hunt market → rank opportunities → process with strategies/styles."""

    def __init__(
        self,
        engine: Optional[QuantXEngine] = None,
        market_data: Optional[MarketDataService] = None,
        db: Optional[Database] = None,
    ):
        self.engine = engine or QuantXEngine()
        self.data = market_data or getattr(self.engine, "data", None) or MarketDataService()
        self.ta = TechnicalAnalyzer()
        self.db = db or Database()
        self.regime = RegimeDetector()

    def _score_symbol(self, symbol: str, exchange: str = "NSE") -> Optional[HuntCandidate]:
        try:
            df = self.data.get_ohlc(symbol, exchange, period="3mo", interval="1d")
            if df is None or len(df) < 30:
                return None
            snap = self.ta.analyze(df)
            c = float(df["close"].iloc[-1])
            prev = float(df["close"].iloc[-2]) if len(df) > 1 else c
            change_pct = (c - prev) / prev * 100 if prev else 0.0
            atr_pct = float(snap.atr / c * 100) if c else 0.0
            ret5 = float(c / df["close"].iloc[-6] - 1) * 100 if len(df) >= 6 else 0.0
            ret20 = float(c / df["close"].iloc[-21] - 1) * 100 if len(df) >= 21 else 0.0

            score = 40.0
            reasons: list[str] = []
            trend_val = snap.trend.value if hasattr(snap.trend, "value") else str(snap.trend)

            # Momentum / trend opportunity
            if snap.adx >= 22 and trend_val in ("BULLISH", "BEARISH"):
                score += min(18.0, snap.adx * 0.45)
                reasons.append(f"trend {trend_val} ADX {snap.adx:.0f}")
            if snap.momentum in ("up", "strong_up", "down", "strong_down"):
                score += 8
                reasons.append(f"momentum {snap.momentum}")
            if abs(ret5) >= 2.0:
                score += min(10.0, abs(ret5))
                reasons.append(f"5d {ret5:+.1f}%")
            if abs(ret20) >= 5.0:
                score += min(8.0, abs(ret20) * 0.4)
                reasons.append(f"20d {ret20:+.1f}%")

            # Volume expansion / event proxy
            if snap.volume_ratio >= 1.3:
                score += min(12.0, (snap.volume_ratio - 1.0) * 10)
                reasons.append(f"vol x{snap.volume_ratio:.1f}")

            # Mean-reversion / value dip opportunity
            if snap.rsi <= 32:
                score += 10
                reasons.append(f"oversold RSI {snap.rsi:.0f}")
            elif snap.rsi >= 68:
                score += 6
                reasons.append(f"overbought RSI {snap.rsi:.0f}")

            # Breakout stretch vs 20d range
            hi = float(df["high"].iloc[-21:-1].max()) if len(df) >= 22 else c
            lo = float(df["low"].iloc[-21:-1].min()) if len(df) >= 22 else c
            if c >= hi * 0.998:
                score += 10
                reasons.append("near 20d high breakout")
            elif c <= lo * 1.002:
                score += 10
                reasons.append("near 20d low breakdown")

            # Volatility tradeable (not dead)
            if 0.8 <= atr_pct <= 4.5:
                score += 4
            elif atr_pct > 4.5:
                score += 2
                reasons.append("high ATR")

            # Prefer liquid names slightly (indexes/large caps already in universe)
            if is_index(symbol):
                score += 3

            if not reasons:
                reasons.append("baseline screen")

            return HuntCandidate(
                symbol=symbol.upper(),
                score=score,
                change_pct=change_pct,
                volume_ratio=float(snap.volume_ratio),
                adx=float(snap.adx),
                rsi=float(snap.rsi),
                atr_pct=atr_pct,
                reasons=reasons[:5],
                is_index=is_index(symbol),
            )
        except Exception as e:  # noqa: BLE001
            logger.debug("Hunt score failed for %s: %s", symbol, e)
            return None

    def hunt(
        self,
        *,
        universe: Optional[list[str]] = None,
        top_n: int = 10,
        exchange: str = "NSE",
        include_indices: bool = True,
        seed_symbols: Optional[list[str]] = None,
        min_score: float = 55.0,
    ) -> list[HuntCandidate]:
        """Screen market universe and return top opportunity candidates."""
        uni = list(universe or HUNT_UNIVERSE)
        if not include_indices:
            uni = [s for s in uni if not is_index(s)]
        # Always consider user seeds
        for s in seed_symbols or []:
            s = s.strip().upper()
            if s and s not in uni:
                uni.append(s)

        scored: list[HuntCandidate] = []
        for sym in uni:
            cand = self._score_symbol(sym, exchange)
            if cand and cand.score >= min_score:
                scored.append(cand)
        scored.sort(key=lambda c: -c.score)

        # Ensure seeds appear if they barely missed — boost inclusion
        out = scored[:top_n]
        have = {c.symbol for c in out}
        for s in seed_symbols or []:
            s = s.strip().upper()
            if not s or s in have:
                continue
            cand = next((c for c in scored if c.symbol == s), None)
            if cand is None:
                cand = self._score_symbol(s, exchange)
            if cand:
                out.append(cand)
                have.add(s)
        out.sort(key=lambda c: -c.score)
        return out[: max(top_n, len(seed_symbols or []))]

    def _strategies_for_products(
        self,
        trade_types: list[str],
        style_ids: Optional[list[str]] = None,
        limit_per_product: int = 3,
    ) -> dict[str, list[str]]:
        learner = StrategyLearner(self.db)
        # Regime from NIFTY when possible
        try:
            nifty_df = self.data.get_ohlc("NIFTY", "NSE", period="3mo")
            snap = self.ta.analyze(nifty_df)
        except Exception:  # noqa: BLE001
            snap = None
        regime = self.regime.detect(snap=snap)
        picked: dict[str, list[str]] = {}

        if style_ids:
            jobs = style_strategy_jobs(
                style_ids,
                trade_type_filter=trade_types,
                limit_per_style=limit_per_product,
            )
            for job in jobs:
                tt = job["trade_type"]
                if tt not in trade_types:
                    continue
                picked.setdefault(tt, [])
                if job["strategy"] not in picked[tt]:
                    picked[tt].append(job["strategy"])
                picked[tt] = picked[tt][: max(limit_per_product, 2)]
            # Ensure every requested product has at least a fallback if styles miss it
            for tt in trade_types:
                if tt in picked and picked[tt]:
                    continue
                if tt == "FUTURES":
                    picked[tt] = ["futures_trend", "futures_momentum"][:limit_per_product]
                elif tt == "OPTIONS":
                    picked[tt] = ["options_directional"][:limit_per_product]
                elif tt == "INTRADAY":
                    picked[tt] = ["intraday_momentum", "scalping_micro"][:limit_per_product]
                else:
                    picked[tt] = ["swing_trend", "breakout"][:limit_per_product]
            return picked

        for tt in trade_types:
            names = select_strategies_for_regime(regime, tt, learner=learner, limit=limit_per_product)
            if not names:
                # cold fallback
                if tt == "FUTURES":
                    names = ["futures_trend", "futures_momentum"]
                elif tt == "OPTIONS":
                    names = ["options_directional"]
                elif tt == "INTRADAY":
                    names = ["intraday_momentum", "intraday_mean_reversion"]
                else:
                    names = ["swing_trend", "breakout", "algo_ensemble"]
            # Hunt always mixes a core opportunistic model so strong trends aren't ignored in sideways regimes
            extras = {
                "SWING": ["swing_trend", "breakout", "algo_ensemble", "growth_momentum"],
                "INTRADAY": ["intraday_momentum", "scalping_micro"],
                "FUTURES": ["futures_trend", "futures_momentum"],
                "OPTIONS": ["options_directional"],
            }.get(tt, [])
            merged: list[str] = []
            for n in list(names) + extras:
                if n in STRATEGIES and n not in merged:
                    merged.append(n)
                if len(merged) >= max(limit_per_product, 2):
                    break
            picked[tt] = merged
        return picked

    def process_symbols(
        self,
        symbols: list[str],
        *,
        exchange: str = "NSE",
        trade_types: Optional[list[str]] = None,
        style_ids: Optional[list[str]] = None,
        strategies_per_product: int = 3,
    ) -> tuple[list[TradeRecommendation], dict[str, list[str]], dict[str, Any]]:
        """Run hunted symbols through strategies / trading models."""
        types = trade_types or ["SWING"]
        strat_map = self._strategies_for_products(types, style_ids=style_ids, limit_per_product=strategies_per_product)
        try:
            nifty_df = self.data.get_ohlc("NIFTY", "NSE", period="3mo")
            snap = self.ta.analyze(nifty_df)
            regime = self.regime.detect(snap=snap)
            regime_dict = regime.as_dict()
        except Exception:  # noqa: BLE001
            regime_dict = {"regime": "UNKNOWN", "summary": "regime unavailable", "preferred_families": []}

        recs: list[TradeRecommendation] = []
        for tt, strat_names in strat_map.items():
            # Index symbols only for F&O products when hunting mixed lists
            if tt in ("FUTURES", "OPTIONS"):
                syms = [s for s in symbols if is_index(s) or s in DEFAULT_FO_UNIVERSE or s in symbols]
                # Prefer FO-capable: indices + default FO names + any hunted that are in FO universe
                fo_ok = set(DEFAULT_FO_UNIVERSE) | {s for s in symbols if is_index(s)}
                syms = [s for s in symbols if s in fo_ok] or list(DEFAULT_FO_UNIVERSE)[:4]
            else:
                syms = [s for s in symbols if not is_index(s)] or symbols

            for strat in strat_names:
                try:
                    batch = self.engine.scan_watchlist(syms, exchange, TradeType(tt), strategy=strat)
                    recs.extend(batch)
                except Exception as e:  # noqa: BLE001
                    logger.exception("Process failed %s/%s: %s", tt, strat, e)

        # Dedupe symbol+trade_type keep best confidence
        best: dict[tuple[str, str], TradeRecommendation] = {}
        for r in recs:
            key = (r.symbol, r.trade_type.value)
            prev = best.get(key)
            if prev is None or (r.valid and not prev.valid) or (
                r.valid == prev.valid and r.scores.confidence > prev.scores.confidence
            ):
                best[key] = r
        out = list(best.values())
        out.sort(key=lambda r: (not r.valid, -r.scores.confidence))
        return out, strat_map, regime_dict

    def hunt_and_process(
        self,
        *,
        seed_symbols: Optional[list[str]] = None,
        top_n: int = 8,
        exchange: str = "NSE",
        enable_fno: bool = True,
        trade_type: str = "SWING",
        trade_types: Optional[list[str]] = None,
        style_ids: Optional[list[str]] = None,
        min_score: float = 52.0,
        strategies_per_product: int = 2,
    ) -> dict[str, Any]:
        """Full pipeline: hunt market → process with strategies/styles."""
        hunted = self.hunt(
            top_n=top_n,
            exchange=exchange,
            seed_symbols=seed_symbols,
            min_score=min_score,
            include_indices=enable_fno or trade_type in ("FUTURES", "OPTIONS", "ALL")
            or bool(trade_types and any(t in ("FUTURES", "OPTIONS") for t in trade_types)),
        )
        symbols = [c.symbol for c in hunted]
        if not symbols and seed_symbols:
            symbols = [s.strip().upper() for s in seed_symbols if s.strip()]

        if trade_types:
            types = [str(t).upper() for t in trade_types]
        elif enable_fno or trade_type == "ALL":
            types = ["SWING", "INTRADAY", "FUTURES", "OPTIONS"]
        elif trade_type == "SWING":
            types = ["SWING", "INTRADAY"]
        else:
            types = [trade_type]

        # Keep products within the operator multi-select (don't force F&O on Stocks-only)
        if style_ids:
            style_ids = [s.id for s in resolve_style_ids(style_ids)]
            allowed = set(types)
            types = [t for t in ["SWING", "INTRADAY", "FUTURES", "OPTIONS"] if t in allowed]
            if not types:
                types = list(allowed) or ["SWING"]

        recs, strat_map, regime = self.process_symbols(
            symbols,
            exchange=exchange,
            trade_types=types,
            style_ids=style_ids,
            strategies_per_product=strategies_per_product,
        )

        # Never return empty strategy map — fallback core models
        if not strat_map or not any(strat_map.values()):
            strat_map = {}
            for tt in types:
                if tt == "FUTURES":
                    strat_map[tt] = ["futures_trend"]
                elif tt == "OPTIONS":
                    strat_map[tt] = ["options_directional"]
                elif tt == "INTRADAY":
                    strat_map[tt] = ["intraday_momentum"]
                else:
                    strat_map[tt] = ["swing_trend"]
            recs2, _, _ = self.process_symbols(
                symbols,
                exchange=exchange,
                trade_types=types,
                style_ids=None,
                strategies_per_product=1,
            )
            if recs2:
                recs = recs2

        styles_used = []
        if style_ids:
            styles_used = [
                {
                    "id": st.id,
                    "name": st.name,
                    "holding_period": st.holding_period,
                    "ai_suitability": st.ai_suitability,
                }
                for st in resolve_style_ids(style_ids)
            ]
        else:
            used = {n for names in strat_map.values() for n in names}
            for st in TRADING_STYLES:
                if any(x in used for x in st.strategies):
                    styles_used.append(
                        {
                            "id": st.id,
                            "name": st.name,
                            "holding_period": st.holding_period,
                            "ai_suitability": st.ai_suitability,
                        }
                    )

        return {
            "ok": True,
            "mode": "market_hunt",
            "hunted": [c.as_dict() for c in hunted],
            "hunted_symbols": symbols,
            "universe_size": len(HUNT_UNIVERSE),
            "strategies_used": strat_map,
            "styles_touched": styles_used[:12],
            "trade_types": types,
            "regime": regime,
            "count": len(recs),
            "valid_count": sum(1 for r in recs if r.valid),
            "enable_fno": enable_fno,
            "recommendations": [r.model_dump(mode="json") for r in recs],
            "message": (
                f"Hunted {len(symbols)} symbols from {len(HUNT_UNIVERSE)} · "
                f"processed with {sum(len(v) for v in strat_map.values())} strategies · "
                f"{sum(1 for r in recs if r.valid)}/{len(recs)} actionable"
            ),
        }
