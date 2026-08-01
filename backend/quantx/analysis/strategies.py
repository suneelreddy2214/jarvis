"""Pluggable trading strategies for QuantX."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from quantx.analysis.technical import IndicatorSnapshot, TechnicalAnalyzer
from quantx.core.models import MarketDirection, Side, TradeType


@dataclass
class StrategySignal:
    side: Optional[Side]
    trade_type: TradeType
    confidence_boost: float
    reason: str
    tags: list[str]


class Strategy(ABC):
    name: str
    trade_type: TradeType
    family: str = "core"
    phase: int = 1
    description: str = ""

    @abstractmethod
    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        ...


class SwingTrendStrategy(Strategy):
    """EMA stack + ADX + SuperTrend swing continuation."""

    name = "swing_trend"
    trade_type = TradeType.SWING
    family = "trend"
    description = "EMA stack + SuperTrend swing continuation"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        tags: list[str] = []
        boost = 0.0
        side: Optional[Side] = None

        if snap.trend == MarketDirection.BULLISH and snap.momentum in ("up", "strong_up"):
            if snap.ema_9 > snap.ema_21 > snap.ema_50 and snap.supertrend_dir == 1:
                side = Side.BUY
                tags += ["ema_stack", "supertrend_long"]
                boost += 5
        elif snap.trend == MarketDirection.BEARISH and snap.momentum in ("down", "strong_down"):
            if snap.ema_9 < snap.ema_21 < snap.ema_50 and snap.supertrend_dir == -1:
                side = Side.SELL
                tags += ["ema_stack", "supertrend_short"]
                boost += 5

        if snap.adx >= 25:
            tags.append("adx_trend")
            boost += 4
        if snap.volume_ratio >= 1.2:
            tags.append("volume")
            boost += 3

        reason = (
            f"SwingTrend {side.value if side else 'FLAT'}: "
            f"trend={snap.trend.value} mom={snap.momentum} adx={snap.adx:.1f}"
        )
        return StrategySignal(side=side, trade_type=self.trade_type, confidence_boost=boost, reason=reason, tags=tags)


class IntradayMeanReversionStrategy(Strategy):
    """Fade RSI extremes only when ADX is weak (range day)."""

    name = "intraday_mean_reversion"
    trade_type = TradeType.INTRADAY
    family = "mean_reversion"
    description = "RSI + Bollinger fade on weak ADX"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        tags: list[str] = []
        boost = 0.0
        side: Optional[Side] = None

        if snap.adx < 20 and snap.rsi <= 30 and snap.close <= snap.bb_lower:
            side = Side.BUY
            tags += ["rsi_oversold", "below_bb", "weak_adx"]
            boost += 8
        elif snap.adx < 20 and snap.rsi >= 70 and snap.close >= snap.bb_upper:
            side = Side.SELL
            tags += ["rsi_overbought", "above_bb", "weak_adx"]
            boost += 8

        if snap.volume_ratio >= 1.1:
            tags.append("volume")
            boost += 2

        reason = (
            f"IntradayMR {side.value if side else 'FLAT'}: "
            f"rsi={snap.rsi:.1f} adx={snap.adx:.1f}"
        )
        return StrategySignal(side=side, trade_type=self.trade_type, confidence_boost=boost, reason=reason, tags=tags)


class BreakoutStrategy(Strategy):
    """Close above/below 20-day range with volume expansion."""

    name = "breakout"
    trade_type = TradeType.SWING
    family = "breakout"
    description = "20-day range breakout with volume"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        tags: list[str] = []
        boost = 0.0
        side: Optional[Side] = None

        if len(df) < 25:
            return StrategySignal(None, self.trade_type, 0, "insufficient bars", [])

        window = df.iloc[-21:-1]
        high20 = float(window["high"].max())
        low20 = float(window["low"].min())

        if snap.close > high20 and snap.volume_ratio >= 1.5 and snap.supertrend_dir == 1:
            side = Side.BUY
            tags += ["20d_high_break", "vol_expand"]
            boost += 10
        elif snap.close < low20 and snap.volume_ratio >= 1.5 and snap.supertrend_dir == -1:
            side = Side.SELL
            tags += ["20d_low_break", "vol_expand"]
            boost += 10

        reason = (
            f"Breakout {side.value if side else 'FLAT'}: "
            f"close={snap.close:.2f} hi20={high20:.2f} lo20={low20:.2f}"
        )
        return StrategySignal(side=side, trade_type=self.trade_type, confidence_boost=boost, reason=reason, tags=tags)


class FuturesTrendStrategy(Strategy):
    """Index / stock futures continuation on EMA + SuperTrend."""

    name = "futures_trend"
    trade_type = TradeType.FUTURES
    family = "trend"
    description = "Futures trend following with EMA + SuperTrend"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        tags: list[str] = []
        boost = 0.0
        side: Optional[Side] = None

        if snap.trend == MarketDirection.BULLISH and snap.momentum in ("up", "strong_up"):
            if snap.ema_9 > snap.ema_21 and snap.supertrend_dir == 1:
                side = Side.BUY
                tags += ["fut_long", "ema_supertrend"]
                boost += 6
        elif snap.trend == MarketDirection.BEARISH and snap.momentum in ("down", "strong_down"):
            if snap.ema_9 < snap.ema_21 and snap.supertrend_dir == -1:
                side = Side.SELL
                tags += ["fut_short", "ema_supertrend"]
                boost += 6
        if side is None and snap.adx >= 22:
            if snap.trend == MarketDirection.BULLISH and snap.supertrend_dir == 1:
                side = Side.BUY
                tags += ["fut_long", "adx_trend"]
                boost += 4
            elif snap.trend == MarketDirection.BEARISH and snap.supertrend_dir == -1:
                side = Side.SELL
                tags += ["fut_short", "adx_trend"]
                boost += 4

        if snap.volume_ratio >= 1.1:
            tags.append("volume")
            boost += 2

        reason = (
            f"FuturesTrend {side.value if side else 'FLAT'}: "
            f"trend={snap.trend.value} mom={snap.momentum} adx={snap.adx:.1f}"
        )
        return StrategySignal(
            side=side, trade_type=self.trade_type, confidence_boost=boost, reason=reason, tags=tags
        )


class OptionsDirectionalStrategy(Strategy):
    """Buy CE on bullish trend / Buy PE on bearish — long premium only."""

    name = "options_directional"
    trade_type = TradeType.OPTIONS
    family = "options_buy"
    description = "Long call/put directional option buying"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        tags: list[str] = []
        boost = 0.0
        side: Optional[Side] = None

        if snap.trend == MarketDirection.BULLISH and snap.momentum in ("up", "strong_up", "neutral"):
            if snap.supertrend_dir == 1 and snap.ema_9 >= snap.ema_21 * 0.998:
                side = Side.BUY
                tags += ["long_ce", "trend_up"]
                boost += 6
        elif snap.trend == MarketDirection.BEARISH and snap.momentum in ("down", "strong_down", "neutral"):
            if snap.supertrend_dir == -1 and snap.ema_9 <= snap.ema_21 * 1.002:
                side = Side.SELL
                tags += ["long_pe", "trend_down"]
                boost += 6

        if snap.adx >= 20:
            tags.append("adx_ok")
            boost += 3
        if snap.volume_ratio >= 1.05:
            tags.append("volume")
            boost += 2

        reason = (
            f"OptionsDirectional {side.value if side else 'FLAT'}: "
            f"trend={snap.trend.value} mom={snap.momentum}"
        )
        return StrategySignal(
            side=side, trade_type=self.trade_type, confidence_boost=boost, reason=reason, tags=tags
        )


STRATEGIES: dict[str, Strategy] = {
    "swing_trend": SwingTrendStrategy(),
    "intraday_mean_reversion": IntradayMeanReversionStrategy(),
    "breakout": BreakoutStrategy(),
    "futures_trend": FuturesTrendStrategy(),
    "options_directional": OptionsDirectionalStrategy(),
}


def register_strategy(strategy: Strategy) -> None:
    """Register an additional strategy without replacing existing names."""
    if strategy.name in STRATEGIES:
        return
    STRATEGIES[strategy.name] = strategy


def get_strategy(name: str) -> Strategy:
    key = name.strip().lower()
    if key not in STRATEGIES:
        raise KeyError(f"Unknown strategy '{name}'. Choose from: {list(STRATEGIES)}")
    return STRATEGIES[key]


def list_strategies() -> list[dict]:
    return [
        {
            "name": s.name,
            "trade_type": s.trade_type.value,
            "class": s.__class__.__name__,
            "family": getattr(s, "family", "core"),
            "phase": getattr(s, "phase", 1),
            "description": getattr(s, "description", ""),
        }
        for s in STRATEGIES.values()
    ]

