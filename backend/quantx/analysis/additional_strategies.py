"""
Additional strategies (additive catalog).

Does NOT replace core strategies in strategies.py.
Registers EMA crossovers, Supertrend, Donchian, momentum, ORB/VWAP proxies,
price action, SMC lite, OI/vol heuristics, relative strength, pairs, etc.
"""

from __future__ import annotations

from typing import Callable, Optional

import numpy as np
import pandas as pd

from quantx.analysis.strategies import Strategy, StrategySignal, register_strategy
from quantx.analysis.technical import IndicatorSnapshot
from quantx.core.models import MarketDirection, Side, TradeType


def _sig(
    side: Optional[Side],
    trade_type: TradeType,
    boost: float,
    reason: str,
    tags: list[str],
) -> StrategySignal:
    return StrategySignal(
        side=side,
        trade_type=trade_type,
        confidence_boost=boost,
        reason=reason,
        tags=tags,
    )


class EmaCrossover921(Strategy):
    name = "ema_cross_9_21"
    trade_type = TradeType.SWING
    family = "trend"
    phase = 1
    description = "EMA 9 / 21 crossover trend following"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        side = None
        tags: list[str] = []
        boost = 0.0
        if len(df) < 30:
            return _sig(None, self.trade_type, 0, "insufficient bars", [])
        e9 = df["close"].ewm(span=9, adjust=False).mean()
        e21 = df["close"].ewm(span=21, adjust=False).mean()
        if e9.iloc[-2] <= e21.iloc[-2] and e9.iloc[-1] > e21.iloc[-1]:
            side = Side.BUY
            tags += ["ema9_cross_up_21"]
            boost = 8
        elif e9.iloc[-2] >= e21.iloc[-2] and e9.iloc[-1] < e21.iloc[-1]:
            side = Side.SELL
            tags += ["ema9_cross_down_21"]
            boost = 8
        elif snap.ema_9 > snap.ema_21 and snap.supertrend_dir == 1 and snap.adx >= 20:
            side = Side.BUY
            tags += ["ema9_above_21"]
            boost = 4
        elif snap.ema_9 < snap.ema_21 and snap.supertrend_dir == -1 and snap.adx >= 20:
            side = Side.SELL
            tags += ["ema9_below_21"]
            boost = 4
        return _sig(side, self.trade_type, boost, f"EMA9/21 {side.value if side else 'FLAT'}", tags)


class EmaCrossover2050(Strategy):
    name = "ema_cross_20_50"
    trade_type = TradeType.SWING
    family = "trend"
    phase = 1
    description = "EMA 20 / 50 crossover"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        side = None
        tags: list[str] = []
        boost = 0.0
        if len(df) < 60:
            return _sig(None, self.trade_type, 0, "insufficient bars", [])
        e20 = df["close"].ewm(span=20, adjust=False).mean()
        e50 = df["close"].ewm(span=50, adjust=False).mean()
        if e20.iloc[-2] <= e50.iloc[-2] and e20.iloc[-1] > e50.iloc[-1]:
            side, tags, boost = Side.BUY, ["ema20_cross_up_50"], 9
        elif e20.iloc[-2] >= e50.iloc[-2] and e20.iloc[-1] < e50.iloc[-1]:
            side, tags, boost = Side.SELL, ["ema20_cross_down_50"], 9
        elif snap.sma_20 > snap.sma_50 and snap.adx >= 22:
            side, tags, boost = Side.BUY, ["sma20_above_50"], 3
        elif snap.sma_20 < snap.sma_50 and snap.adx >= 22:
            side, tags, boost = Side.SELL, ["sma20_below_50"], 3
        return _sig(side, self.trade_type, boost, f"EMA20/50 {side.value if side else 'FLAT'}", tags)


class EmaCrossover50200(Strategy):
    name = "ema_cross_50_200"
    trade_type = TradeType.SWING
    family = "trend"
    phase = 1
    description = "EMA 50 / 200 golden/death cross"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        side = None
        tags: list[str] = []
        boost = 0.0
        if snap.ema_50 > snap.ema_200 and snap.trend == MarketDirection.BULLISH:
            side, tags, boost = Side.BUY, ["golden_cross_bias"], 5
        elif snap.ema_50 < snap.ema_200 and snap.trend == MarketDirection.BEARISH:
            side, tags, boost = Side.SELL, ["death_cross_bias"], 5
        return _sig(side, self.trade_type, boost, f"EMA50/200 {side.value if side else 'FLAT'}", tags)


class SupertrendOnlyStrategy(Strategy):
    name = "supertrend"
    trade_type = TradeType.SWING
    family = "trend"
    phase = 1
    description = "Price vs Supertrend with ATR context"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        side = None
        tags: list[str] = []
        boost = 0.0
        if snap.close > snap.supertrend and snap.supertrend_dir == 1:
            side, tags, boost = Side.BUY, ["price_above_st"], 6
        elif snap.close < snap.supertrend and snap.supertrend_dir == -1:
            side, tags, boost = Side.SELL, ["price_below_st"], 6
        if snap.adx >= 25:
            boost += 3
            tags.append("adx_ok")
        return _sig(side, self.trade_type, boost, f"Supertrend {side.value if side else 'FLAT'}", tags)


class DonchianBreakoutStrategy(Strategy):
    name = "donchian_breakout"
    trade_type = TradeType.SWING
    family = "breakout"
    phase = 1
    description = "Donchian 20-day high break / 10-day low exit style entry"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        if len(df) < 25:
            return _sig(None, self.trade_type, 0, "insufficient bars", [])
        hi20 = float(df["high"].iloc[-21:-1].max())
        lo10 = float(df["low"].iloc[-11:-1].min())
        side = None
        tags: list[str] = []
        boost = 0.0
        if snap.close > hi20:
            side, tags, boost = Side.BUY, ["donchian_20_high"], 9
        elif snap.close < lo10:
            side, tags, boost = Side.SELL, ["donchian_10_low"], 7
        return _sig(
            side,
            self.trade_type,
            boost,
            f"Donchian {side.value if side else 'FLAT'} hi20={hi20:.1f} lo10={lo10:.1f}",
            tags,
        )


class RsiMomentumStrategy(Strategy):
    name = "rsi_momentum"
    trade_type = TradeType.INTRADAY
    family = "momentum"
    phase = 1
    description = "RSI momentum: buy >60 / sell <40"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        side = None
        tags: list[str] = []
        boost = 0.0
        if snap.rsi >= 60 and snap.macd_hist > 0:
            side, tags, boost = Side.BUY, ["rsi_gt_60"], 7
        elif snap.rsi <= 40 and snap.macd_hist < 0:
            side, tags, boost = Side.SELL, ["rsi_lt_40"], 7
        return _sig(side, self.trade_type, boost, f"RSI Mom {side.value if side else 'FLAT'} rsi={snap.rsi:.1f}", tags)


class MacdMomentumStrategy(Strategy):
    name = "macd_momentum"
    trade_type = TradeType.SWING
    family = "momentum"
    phase = 1
    description = "MACD line cross signal line"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        if len(df) < 40:
            return _sig(None, self.trade_type, 0, "insufficient bars", [])
        close = df["close"]
        ema12 = close.ewm(span=12, adjust=False).mean()
        ema26 = close.ewm(span=26, adjust=False).mean()
        macd = ema12 - ema26
        sig = macd.ewm(span=9, adjust=False).mean()
        side = None
        tags: list[str] = []
        boost = 0.0
        if macd.iloc[-2] <= sig.iloc[-2] and macd.iloc[-1] > sig.iloc[-1]:
            side, tags, boost = Side.BUY, ["macd_cross_up"], 8
        elif macd.iloc[-2] >= sig.iloc[-2] and macd.iloc[-1] < sig.iloc[-1]:
            side, tags, boost = Side.SELL, ["macd_cross_down"], 8
        elif snap.macd_hist > 0 and snap.macd > snap.macd_signal:
            side, tags, boost = Side.BUY, ["macd_bull"], 3
        elif snap.macd_hist < 0 and snap.macd < snap.macd_signal:
            side, tags, boost = Side.SELL, ["macd_bear"], 3
        return _sig(side, self.trade_type, boost, f"MACD {side.value if side else 'FLAT'}", tags)


class AdxTrendFilterStrategy(Strategy):
    name = "adx_trend_strength"
    trade_type = TradeType.SWING
    family = "momentum"
    phase = 1
    description = "Trade only when ADX > 25 with trend direction"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        if snap.adx < 25:
            return _sig(None, self.trade_type, 0, f"ADX {snap.adx:.1f} < 25 — skip sideways", ["adx_weak"])
        side = None
        tags = ["adx_gt_25"]
        boost = 5.0
        if snap.trend == MarketDirection.BULLISH and snap.supertrend_dir == 1:
            side = Side.BUY
        elif snap.trend == MarketDirection.BEARISH and snap.supertrend_dir == -1:
            side = Side.SELL
        return _sig(side, self.trade_type, boost, f"ADX filter {side.value if side else 'FLAT'}", tags)


class VolumeBreakoutStrategy(Strategy):
    name = "volume_breakout"
    trade_type = TradeType.SWING
    family = "breakout"
    phase = 1
    description = "Price breakout with huge volume"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        if len(df) < 25 or snap.volume_ratio < 1.8:
            return _sig(None, self.trade_type, 0, "no volume expansion", [])
        hi20 = float(df["high"].iloc[-21:-1].max())
        lo20 = float(df["low"].iloc[-21:-1].min())
        side = None
        tags = ["volume_spike"]
        boost = 8.0
        if snap.close > hi20:
            side = Side.BUY
            tags.append("vol_break_high")
        elif snap.close < lo20:
            side = Side.SELL
            tags.append("vol_break_low")
        else:
            return _sig(None, self.trade_type, 0, "volume up but no range break", tags)
        return _sig(side, self.trade_type, boost, f"VolBreakout {side.value}", tags)


class VwapBiasStrategy(Strategy):
    name = "vwap_bias"
    trade_type = TradeType.INTRADAY
    family = "breakout"
    phase = 1
    description = "Daily VWAP bias proxy (above/below)"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        vwap = snap.vwap if snap.vwap is not None else snap.sma_20
        side = None
        tags: list[str] = []
        boost = 0.0
        if snap.close > vwap and snap.rsi >= 52:
            side, tags, boost = Side.BUY, ["above_vwap"], 5
        elif snap.close < vwap and snap.rsi <= 48:
            side, tags, boost = Side.SELL, ["below_vwap"], 5
        return _sig(side, self.trade_type, boost, f"VWAP bias {side.value if side else 'FLAT'}", tags)


class OpeningRangeProxyStrategy(Strategy):
    name = "opening_range_proxy"
    trade_type = TradeType.INTRADAY
    family = "breakout"
    phase = 1
    description = "ORB proxy using prior bar open range break (daily data)"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        if len(df) < 3:
            return _sig(None, self.trade_type, 0, "insufficient bars", [])
        prev = df.iloc[-2]
        side = None
        tags: list[str] = []
        boost = 0.0
        if snap.close > float(prev["high"]) and snap.volume_ratio >= 1.1:
            side, tags, boost = Side.BUY, ["orb_proxy_high"], 6
        elif snap.close < float(prev["low"]) and snap.volume_ratio >= 1.1:
            side, tags, boost = Side.SELL, ["orb_proxy_low"], 6
        return _sig(side, self.trade_type, boost, f"ORB proxy {side.value if side else 'FLAT'}", tags)


class BollingerReversionStrategy(Strategy):
    name = "bollinger_reversion"
    trade_type = TradeType.INTRADAY
    family = "mean_reversion"
    phase = 1
    description = "Buy lower BB / sell upper BB"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        side = None
        tags: list[str] = []
        boost = 0.0
        if snap.close <= snap.bb_lower and snap.rsi < 35:
            side, tags, boost = Side.BUY, ["bb_lower"], 7
        elif snap.close >= snap.bb_upper and snap.rsi > 65:
            side, tags, boost = Side.SELL, ["bb_upper"], 7
        return _sig(side, self.trade_type, boost, f"BB rev {side.value if side else 'FLAT'}", tags)


class RsiOversoldStrategy(Strategy):
    name = "rsi_oversold"
    trade_type = TradeType.SWING
    family = "mean_reversion"
    phase = 1
    description = "RSI <30 buy / RSI >70 sell"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        side = None
        tags: list[str] = []
        boost = 0.0
        if snap.rsi < 30:
            side, tags, boost = Side.BUY, ["rsi_lt_30"], 8
        elif snap.rsi > 70:
            side, tags, boost = Side.SELL, ["rsi_gt_70"], 8
        return _sig(side, self.trade_type, boost, f"RSI OS {side.value if side else 'FLAT'}", tags)


class EngulfingCandleStrategy(Strategy):
    name = "engulfing_candle"
    trade_type = TradeType.SWING
    family = "price_action"
    phase = 1
    description = "Bullish/bearish engulfing candle"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        if len(df) < 3:
            return _sig(None, self.trade_type, 0, "insufficient bars", [])
        a, b = df.iloc[-2], df.iloc[-1]
        side = None
        tags: list[str] = []
        boost = 0.0
        bull = float(b["close"]) > float(b["open"]) and float(a["close"]) < float(a["open"])
        bear = float(b["close"]) < float(b["open"]) and float(a["close"]) > float(a["open"])
        if bull and float(b["close"]) >= float(a["open"]) and float(b["open"]) <= float(a["close"]):
            side, tags, boost = Side.BUY, ["bullish_engulfing"], 7
        elif bear and float(b["open"]) >= float(a["close"]) and float(b["close"]) <= float(a["open"]):
            side, tags, boost = Side.SELL, ["bearish_engulfing"], 7
        return _sig(side, self.trade_type, boost, f"Engulfing {side.value if side else 'FLAT'}", tags)


class PinBarStrategy(Strategy):
    name = "pin_bar"
    trade_type = TradeType.SWING
    family = "price_action"
    phase = 1
    description = "Pin bar / rejection wick"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        if len(df) < 2:
            return _sig(None, self.trade_type, 0, "insufficient bars", [])
        b = df.iloc[-1]
        o, h, l, c = float(b["open"]), float(b["high"]), float(b["low"]), float(b["close"])
        body = abs(c - o)
        rng = max(h - l, 1e-9)
        upper = h - max(o, c)
        lower = min(o, c) - l
        side = None
        tags: list[str] = []
        boost = 0.0
        if lower > body * 2 and lower > upper and body / rng < 0.35:
            side, tags, boost = Side.BUY, ["bullish_pin"], 6
        elif upper > body * 2 and upper > lower and body / rng < 0.35:
            side, tags, boost = Side.SELL, ["bearish_pin"], 6
        return _sig(side, self.trade_type, boost, f"PinBar {side.value if side else 'FLAT'}", tags)


class SupportResistanceStrategy(Strategy):
    name = "support_resistance"
    trade_type = TradeType.SWING
    family = "price_action"
    phase = 1
    description = "Pivot S1/R1 bounce or break"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        side = None
        tags: list[str] = []
        boost = 0.0
        if snap.close <= snap.s1 * 1.005 and snap.rsi < 45:
            side, tags, boost = Side.BUY, ["near_s1"], 5
        elif snap.close >= snap.r1 * 0.995 and snap.rsi > 55:
            side, tags, boost = Side.SELL, ["near_r1"], 5
        return _sig(side, self.trade_type, boost, f"S/R {side.value if side else 'FLAT'}", tags)


class SmcBosFvgLiteStrategy(Strategy):
    name = "smc_bos_fvg"
    trade_type = TradeType.SWING
    family = "smc"
    phase = 1
    description = "SMC lite: break of structure + fair value gap proxy"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        if len(df) < 30:
            return _sig(None, self.trade_type, 0, "insufficient bars", [])
        swing_high = float(df["high"].iloc[-15:-3].max())
        swing_low = float(df["low"].iloc[-15:-3].min())
        # 3-candle FVG proxy
        c0, c1, c2 = df.iloc[-3], df.iloc[-2], df.iloc[-1]
        bull_fvg = float(c0["high"]) < float(c2["low"])
        bear_fvg = float(c0["low"]) > float(c2["high"])
        side = None
        tags: list[str] = []
        boost = 0.0
        if snap.close > swing_high and (bull_fvg or snap.supertrend_dir == 1):
            side, tags, boost = Side.BUY, ["bos_up", "fvg_proxy"], 8
        elif snap.close < swing_low and (bear_fvg or snap.supertrend_dir == -1):
            side, tags, boost = Side.SELL, ["bos_down", "fvg_proxy"], 8
        return _sig(side, self.trade_type, boost, f"SMC lite {side.value if side else 'FLAT'}", tags)


class VolatilityVixStrategy(Strategy):
    name = "volatility_regime"
    trade_type = TradeType.OPTIONS
    family = "volatility"
    phase = 1
    description = "High ATR/volatility → directional options bias"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        atr_pct = (snap.atr / snap.close * 100) if snap.close else 0
        side = None
        tags = ["vol_regime"]
        boost = 0.0
        if atr_pct >= 1.5 and snap.trend == MarketDirection.BULLISH:
            side, tags, boost = Side.BUY, ["high_vol_long_ce"], 6
        elif atr_pct >= 1.5 and snap.trend == MarketDirection.BEARISH:
            side, tags, boost = Side.SELL, ["high_vol_long_pe"], 6
        return _sig(side, self.trade_type, boost, f"VolRegime atr%={atr_pct:.2f} {side.value if side else 'FLAT'}", tags)


class OptionsStraddleBiasStrategy(Strategy):
    name = "options_straddle_bias"
    trade_type = TradeType.OPTIONS
    family = "options_buy"
    phase = 2
    description = "Straddle bias when volatility expanding (paper: pick stronger side)"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        atr_pct = (snap.atr / snap.close * 100) if snap.close else 0
        if atr_pct < 1.8 or snap.adx < 18:
            return _sig(None, self.trade_type, 0, "vol not elevated for straddle bias", [])
        # Paper: express as directional option on stronger side
        if snap.momentum in ("strong_up", "up") or snap.trend == MarketDirection.BULLISH:
            return _sig(Side.BUY, self.trade_type, 5, "Straddle bias → CE leg", ["straddle", "ce_leg"])
        if snap.momentum in ("strong_down", "down") or snap.trend == MarketDirection.BEARISH:
            return _sig(Side.SELL, self.trade_type, 5, "Straddle bias → PE leg", ["straddle", "pe_leg"])
        return _sig(Side.BUY, self.trade_type, 3, "Straddle bias neutral → CE default", ["straddle"])


class RelativeStrengthStrategy(Strategy):
    name = "relative_strength"
    trade_type = TradeType.SWING
    family = "relative_strength"
    phase = 1
    description = "Stock outperforming its recent mean (RS proxy)"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        if len(df) < 40:
            return _sig(None, self.trade_type, 0, "insufficient bars", [])
        ret20 = float(df["close"].iloc[-1] / df["close"].iloc[-21] - 1)
        ret5 = float(df["close"].iloc[-1] / df["close"].iloc[-6] - 1)
        side = None
        tags: list[str] = []
        boost = 0.0
        if ret20 > 0.04 and ret5 > 0 and snap.ema_9 > snap.ema_21:
            side, tags, boost = Side.BUY, ["rs_outperform"], 6
        elif ret20 < -0.04 and ret5 < 0 and snap.ema_9 < snap.ema_21:
            side, tags, boost = Side.SELL, ["rs_underperform"], 6
        return _sig(side, self.trade_type, boost, f"RS proxy 20d={ret20*100:.1f}%", tags)


class PairRatioZscoreStrategy(Strategy):
    name = "pair_ratio_zscore"
    trade_type = TradeType.SWING
    family = "pairs"
    phase = 2
    description = "Single-symbol z-score mean reversion (pair-style proxy)"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        if len(df) < 40:
            return _sig(None, self.trade_type, 0, "insufficient bars", [])
        close = df["close"].astype(float)
        mu = float(close.iloc[-20:].mean())
        sd = float(close.iloc[-20:].std() or 0)
        if sd <= 0:
            return _sig(None, self.trade_type, 0, "zero variance", [])
        z = (float(close.iloc[-1]) - mu) / sd
        side = None
        tags = [f"z={z:.2f}"]
        boost = 0.0
        if z <= -1.5:
            side, boost = Side.BUY, 6
            tags.append("z_oversold")
        elif z >= 1.5:
            side, boost = Side.SELL, 6
            tags.append("z_overbought")
        return _sig(side, self.trade_type, boost, f"Z-score MR z={z:.2f}", tags)


class OiPcrBiasStrategy(Strategy):
    name = "oi_pcr_bias"
    trade_type = TradeType.OPTIONS
    family = "options_buy"
    phase = 1
    description = "OI/PCR style bias from momentum + volume proxy (no live chain)"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        # Without live OI chain, approximate put-heavy vs call-heavy via momentum extremes
        side = None
        tags = ["oi_proxy"]
        boost = 0.0
        if snap.rsi >= 65 and snap.volume_ratio >= 1.2 and snap.trend == MarketDirection.BULLISH:
            side, boost = Side.BUY, 5
            tags.append("long_buildup_proxy")
        elif snap.rsi <= 35 and snap.volume_ratio >= 1.2 and snap.trend == MarketDirection.BEARISH:
            side, boost = Side.SELL, 5
            tags.append("short_buildup_proxy")
        return _sig(side, self.trade_type, boost, f"OI/PCR proxy {side.value if side else 'FLAT'}", tags)


ADDITIONAL_STRATEGIES: list[Strategy] = [
    EmaCrossover921(),
    EmaCrossover2050(),
    EmaCrossover50200(),
    SupertrendOnlyStrategy(),
    DonchianBreakoutStrategy(),
    RsiMomentumStrategy(),
    MacdMomentumStrategy(),
    AdxTrendFilterStrategy(),
    VolumeBreakoutStrategy(),
    VwapBiasStrategy(),
    OpeningRangeProxyStrategy(),
    BollingerReversionStrategy(),
    RsiOversoldStrategy(),
    EngulfingCandleStrategy(),
    PinBarStrategy(),
    SupportResistanceStrategy(),
    SmcBosFvgLiteStrategy(),
    VolatilityVixStrategy(),
    OptionsStraddleBiasStrategy(),
    RelativeStrengthStrategy(),
    PairRatioZscoreStrategy(),
    OiPcrBiasStrategy(),
]


def register_all(register: Callable[[Strategy], None] = register_strategy) -> int:
    n = 0
    for s in ADDITIONAL_STRATEGIES:
        before = s.name
        register(s)
        n += 1
        _ = before
    return n


# Auto-register on import (additive)
register_all()
