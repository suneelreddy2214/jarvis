"""
Trading styles catalog + additive style strategies + self-training.

Maps institutional/retail styles (scalping → investing) onto QuantX strategies
and trains adaptive weights via historical backtests + paper feedback.
Does NOT replace core strategies — registers additively.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

import pandas as pd

from quantx.analysis.backtest import Backtester
from quantx.analysis.learning import StrategyLearner
from quantx.analysis.strategies import STRATEGIES, Strategy, StrategySignal, list_strategies, register_strategy
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


# ---------------------------------------------------------------------------
# Style strategies (additive)
# ---------------------------------------------------------------------------


class ScalpingMicroStrategy(Strategy):
    """Ultra-short momentum vs VWAP — paper proxy for seconds/minutes scalp."""

    name = "scalping_micro"
    trade_type = TradeType.INTRADAY
    family = "scalping"
    phase = 2
    description = "Scalping: last-bar momentum vs VWAP (very high turnover)"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        if len(df) < 20:
            return _sig(None, self.trade_type, 0, "insufficient bars", [])
        ret = float(df["close"].iloc[-1] / df["close"].iloc[-2] - 1) if df["close"].iloc[-2] else 0.0
        side = None
        boost = 0.0
        tags = ["scalp"]
        if ret > 0.002 and snap.close >= snap.vwap and snap.volume_ratio >= 1.15:
            side, boost = Side.BUY, 7
            tags.append("scalp_long")
        elif ret < -0.002 and snap.close <= snap.vwap and snap.volume_ratio >= 1.15:
            side, boost = Side.SELL, 7
            tags.append("scalp_short")
        return _sig(side, self.trade_type, boost, f"Scalp {side.value if side else 'FLAT'} ret={ret*100:.2f}%", tags)


class IntradayMomentumStrategy(Strategy):
    name = "intraday_momentum"
    trade_type = TradeType.INTRADAY
    family = "momentum"
    phase = 2
    description = "Intraday momentum: RSI + MACD + volume expansion"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        side = None
        boost = 0.0
        tags = ["intraday_mom"]
        if snap.momentum in ("up", "strong_up") and snap.rsi >= 55 and snap.macd_hist > 0 and snap.volume_ratio >= 1.1:
            side, boost = Side.BUY, 8
        elif snap.momentum in ("down", "strong_down") and snap.rsi <= 45 and snap.macd_hist < 0 and snap.volume_ratio >= 1.1:
            side, boost = Side.SELL, 8
        return _sig(side, self.trade_type, boost, f"IntradayMom {side.value if side else 'FLAT'}", tags)


class PositionHoldStrategy(Strategy):
    name = "position_hold"
    trade_type = TradeType.SWING
    family = "investing"
    phase = 2
    description = "Position trading: multi-week EMA50/200 alignment"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        side = None
        boost = 0.0
        tags = ["position"]
        if snap.ema_50 > snap.ema_200 and snap.close > snap.ema_50 and snap.adx >= 18:
            side, boost = Side.BUY, 6
            tags.append("position_long")
        elif snap.ema_50 < snap.ema_200 and snap.close < snap.ema_50 and snap.adx >= 18:
            side, boost = Side.SELL, 6
            tags.append("position_short")
        return _sig(side, self.trade_type, boost, f"Position {side.value if side else 'FLAT'}", tags)


class FuturesMomentumStrategy(Strategy):
    name = "futures_momentum"
    trade_type = TradeType.FUTURES
    family = "momentum"
    phase = 2
    description = "Futures momentum: ADX + SuperTrend continuation"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        if snap.adx < 20:
            return _sig(None, self.trade_type, 0, f"FutMom FLAT ADX {snap.adx:.1f}", ["adx_weak"])
        side = None
        boost = 0.0
        tags = ["fut_mom"]
        if snap.supertrend_dir == 1 and snap.momentum in ("up", "strong_up", "neutral") and snap.trend == MarketDirection.BULLISH:
            side, boost = Side.BUY, 7
        elif snap.supertrend_dir == -1 and snap.momentum in ("down", "strong_down", "neutral") and snap.trend == MarketDirection.BEARISH:
            side, boost = Side.SELL, 7
        return _sig(side, self.trade_type, boost, f"FutMom {side.value if side else 'FLAT'}", tags)


class FuturesBreakoutStrategy(Strategy):
    name = "futures_breakout"
    trade_type = TradeType.FUTURES
    family = "breakout"
    phase = 2
    description = "Futures breakout of 20-bar range with volume"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        if len(df) < 25:
            return _sig(None, self.trade_type, 0, "insufficient bars", [])
        hi = float(df["high"].iloc[-21:-1].max())
        lo = float(df["low"].iloc[-21:-1].min())
        c = float(df["close"].iloc[-1])
        side = None
        boost = 0.0
        tags = ["fut_breakout"]
        if c > hi and snap.volume_ratio >= 1.2:
            side, boost = Side.BUY, 8
        elif c < lo and snap.volume_ratio >= 1.2:
            side, boost = Side.SELL, 8
        return _sig(side, self.trade_type, boost, f"FutBreakout {side.value if side else 'FLAT'}", tags)


class BasisArbitrageStrategy(Strategy):
    """Paper cash-and-carry proxy: fade rich/cheap futures basis using spot ATR."""

    name = "basis_arbitrage"
    trade_type = TradeType.FUTURES
    family = "arbitrage"
    phase = 2
    description = "Arbitrage proxy: fade extreme futures basis vs ATR"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        # Synthetic basis ~ 0.15% contango; fade when price stretches vs VWAP/ATR
        stretch = (snap.close - snap.vwap) / snap.atr if snap.atr else 0.0
        side = None
        boost = 0.0
        tags = ["arb", "basis"]
        if stretch > 1.2:
            side, boost = Side.SELL, 5  # rich → short fut proxy
            tags.append("rich_basis")
        elif stretch < -1.2:
            side, boost = Side.BUY, 5
            tags.append("cheap_basis")
        return _sig(side, self.trade_type, boost, f"BasisArb {side.value if side else 'FLAT'} z={stretch:.2f}", tags)


class EventGapStrategy(Strategy):
    name = "event_gap"
    trade_type = TradeType.SWING
    family = "event"
    phase = 2
    description = "Event-driven proxy: gap + volume expansion"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        if len(df) < 5:
            return _sig(None, self.trade_type, 0, "insufficient bars", [])
        prev_close = float(df["close"].iloc[-2])
        open_ = float(df["open"].iloc[-1]) if "open" in df.columns else float(df["close"].iloc[-1])
        gap = (open_ - prev_close) / prev_close if prev_close else 0.0
        side = None
        boost = 0.0
        tags = ["event", "gap"]
        if gap >= 0.015 and snap.volume_ratio >= 1.4 and snap.trend != MarketDirection.BEARISH:
            side, boost = Side.BUY, 7
            tags.append("gap_up")
        elif gap <= -0.015 and snap.volume_ratio >= 1.4 and snap.trend != MarketDirection.BULLISH:
            side, boost = Side.SELL, 7
            tags.append("gap_down")
        return _sig(side, self.trade_type, boost, f"EventGap {side.value if side else 'FLAT'} gap={gap*100:.1f}%", tags)


class NewsVolumeSpikeStrategy(Strategy):
    name = "news_volume_spike"
    trade_type = TradeType.INTRADAY
    family = "news"
    phase = 2
    description = "News proxy: abnormal volume spike with directional close"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        side = None
        boost = 0.0
        tags = ["news", "vol_spike"]
        if snap.volume_ratio < 1.8:
            return _sig(None, self.trade_type, 0, "no volume spike", tags)
        if snap.close >= snap.vwap and snap.momentum in ("up", "strong_up", "neutral"):
            side, boost = Side.BUY, 6
        elif snap.close <= snap.vwap and snap.momentum in ("down", "strong_down", "neutral"):
            side, boost = Side.SELL, 6
        return _sig(side, self.trade_type, boost, f"NewsVol {side.value if side else 'FLAT'} xr={snap.volume_ratio:.1f}", tags)


class QuantZscoreStrategy(Strategy):
    name = "quant_zscore"
    trade_type = TradeType.SWING
    family = "quant"
    phase = 2
    description = "Quantitative mean-reversion via 20d z-score"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        if len(df) < 30:
            return _sig(None, self.trade_type, 0, "insufficient bars", [])
        c = df["close"].astype(float)
        mu = float(c.iloc[-20:].mean())
        sd = float(c.iloc[-20:].std(ddof=0) or 0)
        if sd <= 0:
            return _sig(None, self.trade_type, 0, "zero variance", [])
        z = (float(c.iloc[-1]) - mu) / sd
        side = None
        boost = 0.0
        tags = ["quant", f"z={z:.2f}"]
        if z <= -1.5:
            side, boost = Side.BUY, 7
        elif z >= 1.5:
            side, boost = Side.SELL, 7
        return _sig(side, self.trade_type, boost, f"QuantZ {side.value if side else 'FLAT'} z={z:.2f}", tags)


class AlgoEnsembleStrategy(Strategy):
    name = "algo_ensemble"
    trade_type = TradeType.SWING
    family = "quant"
    phase = 2
    description = "Algorithmic ensemble: trend + momentum + volume vote"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        score = 0
        tags = ["algo", "ensemble"]
        if snap.trend == MarketDirection.BULLISH:
            score += 1
        elif snap.trend == MarketDirection.BEARISH:
            score -= 1
        if snap.momentum in ("up", "strong_up"):
            score += 1
        elif snap.momentum in ("down", "strong_down"):
            score -= 1
        if snap.supertrend_dir == 1:
            score += 1
        elif snap.supertrend_dir == -1:
            score -= 1
        if snap.volume_ratio >= 1.1:
            score += 1 if score >= 0 else -1
        side = None
        boost = 0.0
        if score >= 3:
            side, boost = Side.BUY, 6 + min(3, score)
        elif score <= -3:
            side, boost = Side.SELL, 6 + min(3, abs(score))
        return _sig(side, self.trade_type, boost, f"AlgoEnsemble {side.value if side else 'FLAT'} score={score}", tags)


class LongTermTrendStrategy(Strategy):
    name = "long_term_trend"
    trade_type = TradeType.SWING
    family = "investing"
    phase = 2
    description = "Long-term investing: price vs EMA200 with ADX filter"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        side = None
        boost = 0.0
        tags = ["long_term"]
        if snap.close > snap.ema_200 and snap.ema_50 > snap.ema_200:
            side, boost = Side.BUY, 5
        elif snap.close < snap.ema_200 and snap.ema_50 < snap.ema_200:
            side, boost = Side.SELL, 4
        return _sig(side, self.trade_type, boost, f"LongTerm {side.value if side else 'FLAT'}", tags)


class DividendQualityStrategy(Strategy):
    name = "dividend_quality"
    trade_type = TradeType.SWING
    family = "investing"
    phase = 2
    description = "Dividend investing proxy: low-vol uptrend above EMA200"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        atr_pct = (snap.atr / snap.close * 100) if snap.close else 99
        side = None
        boost = 0.0
        tags = ["dividend", "quality"]
        if snap.close > snap.ema_200 and atr_pct <= 2.2 and snap.trend != MarketDirection.BEARISH:
            side, boost = Side.BUY, 5
        return _sig(side, self.trade_type, boost, f"DividendQual {side.value if side else 'FLAT'} atr%={atr_pct:.1f}", tags)


class ValueMeanReversionStrategy(Strategy):
    name = "value_mean_reversion"
    trade_type = TradeType.SWING
    family = "investing"
    phase = 2
    description = "Value investing proxy: deep RSI oversold into EMA200 support"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        side = None
        boost = 0.0
        tags = ["value"]
        if snap.rsi <= 30 and snap.close >= snap.ema_200 * 0.92 and snap.close <= snap.ema_200 * 1.02:
            side, boost = Side.BUY, 7
            tags.append("value_dip")
        elif snap.rsi >= 75 and snap.close >= snap.ema_200 * 1.15:
            side, boost = Side.SELL, 4
            tags.append("value_stretch")
        return _sig(side, self.trade_type, boost, f"ValueMR {side.value if side else 'FLAT'}", tags)


class GrowthMomentumStrategy(Strategy):
    name = "growth_momentum"
    trade_type = TradeType.SWING
    family = "investing"
    phase = 2
    description = "Growth investing: strong relative momentum + trend stack"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        if len(df) < 40:
            return _sig(None, self.trade_type, 0, "insufficient bars", [])
        ret20 = float(df["close"].iloc[-1] / df["close"].iloc[-21] - 1)
        side = None
        boost = 0.0
        tags = ["growth", f"ret20={ret20*100:.1f}"]
        if ret20 >= 0.06 and snap.ema_9 > snap.ema_21 > snap.ema_50 and snap.volume_ratio >= 1.05:
            side, boost = Side.BUY, 8
        elif ret20 <= -0.06 and snap.ema_9 < snap.ema_21 < snap.ema_50:
            side, boost = Side.SELL, 5
        return _sig(side, self.trade_type, boost, f"GrowthMom {side.value if side else 'FLAT'}", tags)


class EtfRotationStrategy(Strategy):
    name = "etf_rotation"
    trade_type = TradeType.SWING
    family = "investing"
    phase = 2
    description = "ETF investing: trend-follow rotation via EMA20/50"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        side = None
        boost = 0.0
        tags = ["etf"]
        if snap.sma_20 > snap.sma_50 and snap.close > snap.sma_20:
            side, boost = Side.BUY, 5
        elif snap.sma_20 < snap.sma_50 and snap.close < snap.sma_20:
            side, boost = Side.SELL, 4
        return _sig(side, self.trade_type, boost, f"ETFRot {side.value if side else 'FLAT'}", tags)


class MutualFundProxyStrategy(Strategy):
    name = "mutual_fund_proxy"
    trade_type = TradeType.SWING
    family = "investing"
    phase = 2
    description = "Mutual fund proxy: slow SMA50/200 SIP-style bias"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        side = None
        boost = 0.0
        tags = ["mf", "sip"]
        if snap.ema_50 > snap.ema_200 * 0.995 and snap.close > snap.ema_200:
            side, boost = Side.BUY, 4
        return _sig(side, self.trade_type, boost, f"MFProxy {side.value if side else 'FLAT'}", tags)


class OptionsIncomeBiasStrategy(Strategy):
    name = "options_income_bias"
    trade_type = TradeType.OPTIONS
    family = "options_sell"
    phase = 2
    description = "Options trading: range-bound premium bias (defined-risk long hedge proxy)"

    def evaluate(self, snap: IndicatorSnapshot, df: pd.DataFrame) -> StrategySignal:
        # Paper only buys premium; in range use mild CE/PE based on mean-reversion lean
        side = None
        boost = 0.0
        tags = ["options_income"]
        if snap.adx < 20 and snap.rsi <= 40:
            side, boost = Side.BUY, 4  # long CE on oversold range
            tags.append("range_ce")
        elif snap.adx < 20 and snap.rsi >= 60:
            side, boost = Side.SELL, 4  # maps to PE via option_kind_for_side
            tags.append("range_pe")
        return _sig(side, self.trade_type, boost, f"OptIncome {side.value if side else 'FLAT'}", tags)


STYLE_STRATEGIES: list[Strategy] = [
    ScalpingMicroStrategy(),
    IntradayMomentumStrategy(),
    PositionHoldStrategy(),
    FuturesMomentumStrategy(),
    FuturesBreakoutStrategy(),
    BasisArbitrageStrategy(),
    EventGapStrategy(),
    NewsVolumeSpikeStrategy(),
    QuantZscoreStrategy(),
    AlgoEnsembleStrategy(),
    LongTermTrendStrategy(),
    DividendQualityStrategy(),
    ValueMeanReversionStrategy(),
    GrowthMomentumStrategy(),
    EtfRotationStrategy(),
    MutualFundProxyStrategy(),
    OptionsIncomeBiasStrategy(),
]


def register_style_strategies(register: Callable[[Strategy], None] = register_strategy) -> int:
    n = 0
    for s in STYLE_STRATEGIES:
        register(s)
        n += 1
    return n


register_style_strategies()


# ---------------------------------------------------------------------------
# Catalog (user table)
# ---------------------------------------------------------------------------


@dataclass
class TradingStyle:
    id: str
    name: str
    holding_period: str
    risk: str
    instruments: list[str]
    ai_suitability: int  # 1–5 stars
    strategies: list[str]
    trade_types: list[str]
    family: str
    notes: str = ""

    def as_dict(self) -> dict[str, Any]:
        available = [s for s in self.strategies if s in STRATEGIES]
        return {
            "id": self.id,
            "name": self.name,
            "holding_period": self.holding_period,
            "risk": self.risk,
            "instruments": self.instruments,
            "ai_suitability": self.ai_suitability,
            "ai_stars": "⭐" * self.ai_suitability,
            "strategies": self.strategies,
            "strategies_available": available,
            "strategy_count": len(available),
            "trade_types": self.trade_types,
            "family": self.family,
            "notes": self.notes,
            "trainable": len(available) > 0,
        }


TRADING_STYLES: list[TradingStyle] = [
    TradingStyle(
        "scalping",
        "Scalping",
        "Seconds to minutes",
        "Very High",
        ["Stocks", "Futures", "Options"],
        5,
        ["scalping_micro", "vwap_bias", "opening_range_proxy"],
        ["INTRADAY", "FUTURES", "OPTIONS"],
        "scalping",
        "Paper uses bar-proxy scalps (not tick data).",
    ),
    TradingStyle(
        "intraday",
        "Intraday (Day Trading)",
        "Minutes to hours",
        "High",
        ["Stocks", "Futures", "Options"],
        5,
        ["intraday_momentum", "intraday_mean_reversion", "rsi_momentum", "bollinger_reversion", "vwap_bias"],
        ["INTRADAY", "FUTURES", "OPTIONS"],
        "momentum",
    ),
    TradingStyle(
        "momentum",
        "Momentum Trading",
        "Hours to days",
        "High",
        ["Stocks", "ETFs"],
        4,
        ["macd_momentum", "rsi_momentum", "growth_momentum", "adx_trend_strength", "relative_strength"],
        ["SWING", "INTRADAY"],
        "momentum",
    ),
    TradingStyle(
        "swing",
        "Swing Trading",
        "2–30 days",
        "Medium",
        ["Stocks", "ETFs", "Futures"],
        5,
        ["swing_trend", "ema_cross_9_21", "ema_cross_20_50", "supertrend", "smc_bos_fvg"],
        ["SWING", "FUTURES"],
        "trend",
    ),
    TradingStyle(
        "position",
        "Position Trading",
        "Weeks to months",
        "Medium",
        ["Stocks", "ETFs"],
        4,
        ["position_hold", "ema_cross_50_200", "long_term_trend"],
        ["SWING"],
        "investing",
    ),
    TradingStyle(
        "trend",
        "Trend Trading",
        "Days to months",
        "Medium",
        ["Stocks", "Futures"],
        5,
        ["swing_trend", "supertrend", "adx_trend_strength", "futures_trend", "futures_momentum"],
        ["SWING", "FUTURES"],
        "trend",
    ),
    TradingStyle(
        "breakout",
        "Breakout Trading",
        "Hours to weeks",
        "High",
        ["Stocks", "Futures"],
        4,
        ["breakout", "donchian_breakout", "volume_breakout", "futures_breakout", "opening_range_proxy"],
        ["SWING", "INTRADAY", "FUTURES"],
        "breakout",
    ),
    TradingStyle(
        "mean_reversion",
        "Mean Reversion",
        "Hours to weeks",
        "Medium",
        ["Stocks", "ETFs"],
        4,
        ["intraday_mean_reversion", "bollinger_reversion", "rsi_oversold", "quant_zscore", "pair_ratio_zscore"],
        ["SWING", "INTRADAY"],
        "mean_reversion",
    ),
    TradingStyle(
        "options",
        "Options Trading",
        "Intraday to expiry",
        "High",
        ["Options"],
        5,
        ["options_directional", "options_straddle_bias", "oi_pcr_bias", "volatility_regime", "options_income_bias"],
        ["OPTIONS"],
        "options_buy",
    ),
    TradingStyle(
        "futures",
        "Futures Trading",
        "Intraday to weeks",
        "High",
        ["Index & Stock Futures"],
        5,
        ["futures_trend", "futures_momentum", "futures_breakout", "basis_arbitrage"],
        ["FUTURES"],
        "trend",
    ),
    TradingStyle(
        "arbitrage",
        "Arbitrage",
        "Seconds to days",
        "Low–Medium",
        ["Stocks", "Futures", "Options"],
        3,
        ["basis_arbitrage"],
        ["FUTURES"],
        "arbitrage",
        "Cash-fut basis fade proxy (paper).",
    ),
    TradingStyle(
        "pair_trading",
        "Pair Trading",
        "Days to months",
        "Medium",
        ["Correlated Stocks"],
        4,
        ["pair_ratio_zscore", "quant_zscore"],
        ["SWING"],
        "pairs",
        "Single-symbol z-score / ratio proxy until dual-leg broker support.",
    ),
    TradingStyle(
        "event_driven",
        "Event-Driven Trading",
        "Days to weeks",
        "High",
        ["Stocks"],
        4,
        ["event_gap", "volume_breakout", "news_volume_spike"],
        ["SWING", "INTRADAY"],
        "event",
    ),
    TradingStyle(
        "news_based",
        "News-Based Trading",
        "Minutes to days",
        "High",
        ["Stocks", "Indices"],
        4,
        ["news_volume_spike", "event_gap"],
        ["INTRADAY", "SWING"],
        "news",
        "Uses volume/gap proxies (no live news wire).",
    ),
    TradingStyle(
        "quantitative",
        "Quantitative Trading",
        "Any timeframe",
        "Varies",
        ["All markets"],
        5,
        ["quant_zscore", "pair_ratio_zscore", "algo_ensemble", "relative_strength"],
        ["SWING", "INTRADAY"],
        "quant",
    ),
    TradingStyle(
        "algorithmic",
        "Algorithmic Trading",
        "Any timeframe",
        "Varies",
        ["All markets"],
        5,
        ["algo_ensemble", "swing_trend", "breakout", "futures_trend", "options_directional"],
        ["SWING", "INTRADAY", "FUTURES", "OPTIONS"],
        "quant",
        "Meta style — whole paper agent is algorithmic.",
    ),
    TradingStyle(
        "long_term",
        "Long-Term Investing",
        "Years",
        "Lower",
        ["Stocks", "ETFs"],
        4,
        ["long_term_trend", "position_hold", "ema_cross_50_200"],
        ["SWING"],
        "investing",
    ),
    TradingStyle(
        "dividend",
        "Dividend Investing",
        "Years",
        "Lower",
        ["Dividend Stocks"],
        3,
        ["dividend_quality", "long_term_trend"],
        ["SWING"],
        "investing",
    ),
    TradingStyle(
        "value",
        "Value Investing",
        "Years",
        "Lower",
        ["Undervalued Stocks"],
        3,
        ["value_mean_reversion", "rsi_oversold", "dividend_quality"],
        ["SWING"],
        "investing",
    ),
    TradingStyle(
        "growth",
        "Growth Investing",
        "Years",
        "Medium",
        ["Growth Stocks"],
        4,
        ["growth_momentum", "relative_strength", "macd_momentum"],
        ["SWING"],
        "investing",
    ),
    TradingStyle(
        "mutual_fund",
        "Mutual Fund Investing",
        "Months to years",
        "Low–Medium",
        ["Mutual Funds"],
        3,
        ["mutual_fund_proxy", "long_term_trend"],
        ["SWING"],
        "investing",
        "SIP-style equity proxy (no MF NAV feed).",
    ),
    TradingStyle(
        "etf",
        "ETF Investing",
        "Months to years",
        "Low–Medium",
        ["ETFs"],
        4,
        ["etf_rotation", "long_term_trend", "position_hold"],
        ["SWING"],
        "investing",
    ),
]


def get_trading_styles() -> list[dict[str, Any]]:
    return [s.as_dict() for s in TRADING_STYLES]


def get_style(style_id: str) -> Optional[TradingStyle]:
    for s in TRADING_STYLES:
        if s.id == style_id:
            return s
    return None


DEFAULT_TRAIN_SYMBOLS = [
    "RELIANCE",
    "TCS",
    "INFY",
    "HDFCBANK",
    "ICICIBANK",
    "SBIN",
    "ITC",
    "LT",
]
FO_TRAIN_SYMBOLS = ["NIFTY", "BANKNIFTY", "RELIANCE", "TCS"]


def train_trading_styles(
    *,
    backtester: Optional[Backtester] = None,
    learner: Optional[StrategyLearner] = None,
    symbols: Optional[list[str]] = None,
    period: str = "6mo",
    style_ids: Optional[list[str]] = None,
    max_symbols_per_strategy: int = 3,
) -> dict[str, Any]:
    """
    Self-train strategy weights from historical backtests across trading styles.
    Feeds StrategyLearner so regime selector prefers styles that worked recently.
    """
    bt = backtester or Backtester()
    learn = learner or StrategyLearner()
    styles = TRADING_STYLES
    if style_ids:
        wanted = set(style_ids)
        styles = [s for s in styles if s.id in wanted]

    eq_syms = (symbols or DEFAULT_TRAIN_SYMBOLS)[: max(1, max_symbols_per_strategy)]
    fo_syms = FO_TRAIN_SYMBOLS[: max(1, max_symbols_per_strategy)]

    ran = 0
    trained_strategies: set[str] = set()
    style_results: list[dict[str, Any]] = []
    errors: list[str] = []

    for style in styles:
        style_pnl = 0.0
        style_trades = 0
        style_wins = 0
        per_strat: list[dict[str, Any]] = []
        for strat_name in style.strategies:
            if strat_name not in STRATEGIES:
                continue
            meta = next((m for m in list_strategies() if m["name"] == strat_name), None)
            tt = (meta or {}).get("trade_type", "SWING")
            use_syms = fo_syms if tt in ("FUTURES", "OPTIONS") else eq_syms
            for sym in use_syms:
                try:
                    result = bt.run(symbol=sym, strategy=strat_name, period=period)
                    ran += 1
                    trained_strategies.add(strat_name)
                    # Online learning from each closed backtest trade
                    for t in result.trade_log:
                        learn.record_trade(strat_name, float(t.pnl))
                        style_trades += 1
                        style_pnl += float(t.pnl)
                        if t.pnl > 0:
                            style_wins += 1
                    # Also nudge style-level weight
                    if result.trades > 0:
                        learn.record_trade(f"style:{style.id}", float(result.total_pnl))
                    per_strat.append(
                        {
                            "strategy": strat_name,
                            "symbol": sym,
                            "trades": result.trades,
                            "win_rate": result.win_rate,
                            "pnl": result.total_pnl,
                            "return_pct": result.total_return_pct,
                        }
                    )
                except Exception as e:  # noqa: BLE001 — keep training resilient
                    errors.append(f"{style.id}/{strat_name}/{sym}: {e}")

        style_results.append(
            {
                "style_id": style.id,
                "name": style.name,
                "ai_suitability": style.ai_suitability,
                "strategies_trained": sorted({p["strategy"] for p in per_strat}),
                "backtests": len(per_strat),
                "trades": style_trades,
                "wins": style_wins,
                "win_rate": round(style_wins / style_trades * 100, 1) if style_trades else 0.0,
                "pnl": round(style_pnl, 2),
                "samples": per_strat[:12],
            }
        )

    style_results.sort(key=lambda r: (-r["ai_suitability"], -r["pnl"]))
    return {
        "ok": True,
        "period": period,
        "backtests_run": ran,
        "strategies_trained": sorted(trained_strategies),
        "strategy_count_catalog": len(list_strategies()),
        "styles": get_trading_styles(),
        "style_results": style_results,
        "learning": learn.as_dict(),
        "errors": errors[:20],
        "note": "Self-training updates strategy weights from historical paper backtests; live paper closes continue online learning.",
    }
