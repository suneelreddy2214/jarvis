"""
QuantX Decision Engine — generates institutional trade recommendations.

Only recommends when trend, momentum, volume, RR, fundamentals, and risk align.
"""

from __future__ import annotations

import logging
from typing import Optional

from quantx.analysis.fundamental import FundamentalAnalyzer
from quantx.analysis.futures import FuturesAnalyzer
from quantx.analysis.macro import MacroAnalyzer
from quantx.analysis.options import OptionsAnalyzer
from quantx.analysis.strategies import Strategy, get_strategy
from quantx.analysis.technical import TechnicalAnalyzer
from quantx.core.config import Settings, get_settings
from quantx.core.models import (
    AIScores,
    MarketDirection,
    Side,
    TradeRecommendation,
    TradeType,
)
from quantx.core.position_sizing import PositionSizer
from quantx.core.risk import RiskManager
from quantx.data.market_data import MarketDataService

logger = logging.getLogger(__name__)


class QuantXEngine:
    """Institutional decision engine. Capital preservation first."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        risk_manager: Optional[RiskManager] = None,
        market_data: Optional[MarketDataService] = None,
        strategy: Optional[str | Strategy] = None,
    ):
        self.settings = settings or get_settings()
        self.risk = risk_manager or RiskManager(self.settings)
        self.data = market_data or MarketDataService()
        self.ta = TechnicalAnalyzer()
        self.fa = FundamentalAnalyzer()
        self.oa = OptionsAnalyzer()
        self.futures = FuturesAnalyzer()
        self.macro = MacroAnalyzer()
        self.sizer = PositionSizer(self.settings)
        self.strategy: Optional[Strategy] = None
        if strategy is not None:
            self.strategy = get_strategy(strategy) if isinstance(strategy, str) else strategy

    def analyze_symbol(
        self,
        symbol: str,
        exchange: str = "NSE",
        trade_type: TradeType = TradeType.SWING,
        capital: Optional[float] = None,
        strategy: Optional[str] = None,
    ) -> TradeRecommendation:
        capital = capital if capital is not None else self.risk.state.capital
        symbol_clean = symbol.upper().replace(".NS", "").replace(".BO", "")
        strat = None
        if strategy:
            strat = get_strategy(strategy)
        elif self.strategy:
            strat = self.strategy
        if strat is not None:
            trade_type = strat.trade_type

        # Macro gate
        macro_snap = self.data.get_macro_snapshot()
        macro = self.macro.analyze(
            nifty_change_pct=macro_snap.get("nifty_change_pct"),
            india_vix=macro_snap.get("india_vix"),
            usdinr_change_pct=macro_snap.get("usdinr_change_pct"),
        )

        df = self.data.get_ohlc(symbol_clean, exchange)
        snap = self.ta.analyze(df)
        info = self.data.get_info(symbol_clean, exchange)
        fund = self.fa.analyze(symbol_clean, info)
        opt = self.oa.analyze(None, spot=snap.close)

        # Liquidity check
        if snap.avg_volume < self.settings.risk.min_liquidity_avg_volume:
            return self._reject(
                symbol_clean, exchange, trade_type, snap.close,
                f"Low liquidity: avg volume {snap.avg_volume:.0f} < {self.settings.risk.min_liquidity_avg_volume}",
                snap.supporting, fund.summary, opt.summary, macro.summary,
            )

        side = None
        strategy_tags: list[str] = []
        strategy_boost = 0.0
        strategy_reason = ""
        if strat is not None:
            sig = strat.evaluate(snap, df)
            side = sig.side
            strategy_tags = sig.tags
            strategy_boost = sig.confidence_boost
            strategy_reason = sig.reason
            if side is None:
                return self._reject(
                    symbol_clean, exchange, trade_type, snap.close,
                    f"Strategy {strat.name} flat — {sig.reason}",
                    snap.supporting + strategy_tags, fund.summary, opt.summary, macro.summary,
                    direction=snap.trend,
                )
        else:
            side = self.ta.suggest_side(snap)
            if side is None:
                return self._reject(
                    symbol_clean, exchange, trade_type, snap.close,
                    f"No aligned setup — trend={snap.trend.value}, momentum={snap.momentum}",
                    snap.supporting, fund.summary, opt.summary, macro.summary,
                    direction=snap.trend,
                )

        # Entry gates
        entry_cfg = self.settings.entry
        rejects: list[str] = []

        if entry_cfg.require_trend and snap.trend in (MarketDirection.NEUTRAL, MarketDirection.RANGE_BOUND):
            # Mean-reversion strategies may trade ranges
            if not (strat and strat.name == "intraday_mean_reversion"):
                rejects.append("Trend not confirmed")
        if entry_cfg.require_momentum and snap.momentum == "neutral":
            if not (strat and strat.name == "intraday_mean_reversion"):
                rejects.append("Momentum not confirmed")
        if entry_cfg.require_volume and snap.volume_ratio < 1.0:
            rejects.append("Volume not confirmed")
        if fund.score < entry_cfg.min_fundamental_score:
            rejects.append(f"Fundamental score {fund.score:.0f} < {entry_cfg.min_fundamental_score}")
        if macro.avoid_new_risk and entry_cfg.avoid_major_news:
            rejects.append(f"Macro risk elevated: {macro.summary}")

        levels = self.ta.levels_for_trade(snap, side)
        if levels["risk_reward"] < self.settings.risk.min_risk_reward:
            rejects.append(f"RR {levels['risk_reward']:.2f} < {self.settings.risk.min_risk_reward}")

        size = self.sizer.calculate(
            capital=capital,
            entry=levels["entry"],
            stop_loss=levels["stop_loss"],
            side=side,
            atr=snap.atr,
        )
        if size.quantity <= 0:
            rejects.append(size.notes or "Position size zero")

        scores = self._score(snap, fund.score, side, levels["risk_reward"], macro)
        scores.confidence = round(min(100.0, scores.confidence + strategy_boost), 1)
        scores.probability_of_success = round(min(100.0, scores.confidence * 0.85), 1)
        if scores.confidence < entry_cfg.min_confidence:
            rejects.append(f"Confidence {scores.confidence:.0f} < {entry_cfg.min_confidence}")

        reason = self._build_reason(side, snap, fund, opt, macro)
        if strategy_reason:
            reason = f"[{strat.name}] {strategy_reason}. {reason}"
        alt = self._alternative(side, snap)
        supporting = snap.supporting + ([f"strategy:{t}" for t in strategy_tags] if strategy_tags else [])

        rec = TradeRecommendation(
            symbol=symbol_clean,
            exchange=exchange,
            market_direction=snap.trend,
            trade_type=trade_type,
            side=side,
            entry=round(levels["entry"], 2),
            stop_loss=round(levels["stop_loss"], 2),
            target_1=round(levels["target_1"], 2),
            target_2=round(levels["target_2"], 2),
            risk_reward=round(levels["risk_reward"], 2),
            quantity=size.quantity,
            capital_at_risk=size.capital_at_risk,
            scores=scores,
            reason=reason,
            supporting_indicators=supporting,
            fundamental_summary=fund.summary,
            options_summary=opt.summary,
            risk_notes=(
                f"Risking ₹{size.capital_at_risk:.0f} ({size.risk_pct:.2f}% of capital). "
                f"ATR={snap.atr:.2f}. Max daily loss remaining gate applies. "
                + (size.notes or "")
            ),
            alternative_scenario=alt,
            valid=True,
        )

        ok, risk_reasons = self.risk.validate_recommendation(rec)
        if not ok:
            rejects.extend(risk_reasons)

        if rejects:
            rec.valid = False
            rec.rejection_reason = "; ".join(rejects)
            rec.quantity = 0
            rec.capital_at_risk = 0.0
            rec.risk_notes = "TRADE REJECTED — " + rec.rejection_reason
        return rec

    def scan_watchlist(
        self,
        symbols: list[str],
        exchange: str = "NSE",
        trade_type: TradeType = TradeType.SWING,
        strategy: Optional[str] = None,
    ) -> list[TradeRecommendation]:
        results = []
        for sym in symbols:
            try:
                results.append(self.analyze_symbol(sym, exchange, trade_type, strategy=strategy))
            except Exception as e:
                logger.exception("Scan failed for %s", sym)
                results.append(
                    TradeRecommendation(
                        symbol=sym,
                        exchange=exchange,
                        market_direction=MarketDirection.NEUTRAL,
                        trade_type=trade_type,
                        side=Side.BUY,
                        entry=0,
                        stop_loss=0,
                        target_1=0,
                        target_2=0,
                        risk_reward=0,
                        quantity=0,
                        capital_at_risk=0,
                        scores=AIScores(
                            confidence=0, risk_score=100, volatility_score=50,
                            probability_of_success=0,
                        ),
                        reason=f"Analysis error: {e}",
                        valid=False,
                        rejection_reason=str(e),
                    )
                )
        # Valid first, then by confidence
        results.sort(key=lambda r: (not r.valid, -r.scores.confidence))
        return results

    def _score(self, snap, fund_score: float, side: Side, rr: float, macro) -> AIScores:
        conf = 40.0
        # Trend alignment
        if side == Side.BUY and snap.trend == MarketDirection.BULLISH:
            conf += 15
        if side == Side.SELL and snap.trend == MarketDirection.BEARISH:
            conf += 15
        if snap.adx >= 25:
            conf += 10
        if snap.volume_ratio >= 1.2:
            conf += 8
        if snap.momentum in ("strong_up", "strong_down"):
            conf += 8
        conf += min(10, (fund_score - 50) / 5)
        conf += min(8, (rr - 2) * 4)
        if macro.avoid_new_risk:
            conf -= 15
        conf = max(0, min(100, conf))

        vol_score = min(100, (snap.atr / snap.close * 100) * 25) if snap.close else 50
        risk_score = min(100, vol_score * 0.5 + (100 - conf) * 0.5)
        prob = conf * 0.85  # calibrated lower than raw confidence
        exp_ret = rr * (self.settings.risk.max_risk_per_trade_pct) * (prob / 100)
        exp_dd = self.settings.risk.max_risk_per_trade_pct

        return AIScores(
            confidence=round(conf, 1),
            risk_score=round(risk_score, 1),
            volatility_score=round(vol_score, 1),
            probability_of_success=round(prob, 1),
            expected_return_pct=round(exp_ret, 3),
            expected_drawdown_pct=round(exp_dd, 3),
        )

    def _build_reason(self, side, snap, fund, opt, macro) -> str:
        action = "LONG" if side == Side.BUY else "SHORT"
        return (
            f"{action} setup: {snap.trend.value} trend with {snap.momentum} momentum. "
            f"Price {snap.close:.2f}, ATR {snap.atr:.2f}, ADX {snap.adx:.1f}. "
            f"{fund.summary} {opt.summary} Macro: {macro.summary}"
        )

    def _alternative(self, side, snap) -> str:
        if side == Side.BUY:
            return (
                f"If price loses SuperTrend ({snap.supertrend:.2f}) or closes below S1 ({snap.s1:.2f}) "
                f"with rising volume, abort long thesis — do not average down."
            )
        return (
            f"If price reclaims SuperTrend ({snap.supertrend:.2f}) or closes above R1 ({snap.r1:.2f}) "
            f"with rising volume, abort short thesis — do not add to losers."
        )

    def _reject(
        self, symbol, exchange, trade_type, price, reason, supporting, fund, opt, macro,
        direction=MarketDirection.NEUTRAL,
    ) -> TradeRecommendation:
        return TradeRecommendation(
            symbol=symbol,
            exchange=exchange,
            market_direction=direction,
            trade_type=trade_type,
            side=Side.BUY,
            entry=round(price, 2) if price else 0,
            stop_loss=0,
            target_1=0,
            target_2=0,
            risk_reward=0,
            quantity=0,
            capital_at_risk=0,
            scores=AIScores(
                confidence=0, risk_score=80, volatility_score=50,
                probability_of_success=0,
            ),
            reason=reason,
            supporting_indicators=supporting or [],
            fundamental_summary=fund,
            options_summary=opt,
            risk_notes=f"NO TRADE — {reason}. Macro: {macro}",
            alternative_scenario="Wait for confluence of trend, momentum, volume, and acceptable RR.",
            valid=False,
            rejection_reason=reason,
        )
