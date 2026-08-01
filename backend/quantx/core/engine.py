"""
QuantX Decision Engine — generates institutional trade recommendations.

Only recommends when trend, momentum, volume, RR, fundamentals, and risk align.
"""

from __future__ import annotations

import logging
from typing import Optional

from quantx.analysis.fundamental import FundamentalAnalyzer
from quantx.analysis.futures import FuturesAnalyzer
from quantx.analysis.fno import (
    encode_fo_meta,
    is_index,
    option_kind_for_side,
    paper_option_levels,
    paper_option_premium,
)
from quantx.analysis.option_chain import list_expiries
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
from quantx.data.market_data import MarketDataService, YahooFinanceError
from quantx.portfolio.margin import MARGIN_FRAC, lot_multiplier

logger = logging.getLogger(__name__)

# Mean-reversion / range strategies intentionally trade low ADX — exempt from ADX strength gate.
_ADX_EXEMPT_STRATEGIES = {
    "intraday_mean_reversion",
    "bollinger_reversion",
    "value_mean_reversion",
    "quant_zscore",
    "options_income_bias",
    "basis_arbitrage",
    "rsi_oversold",
}

_VOLUME_EXEMPT_STRATEGIES = {
    "intraday_mean_reversion",
    "bollinger_reversion",
    "value_mean_reversion",
    "quant_zscore",
    "rsi_oversold",
    "basis_arbitrage",
}

_BREAKOUT_STRATEGIES = {
    "breakout",
    "volume_breakout",
    "donchian_breakout",
    "futures_breakout",
    "opening_range_proxy",
}

_TREND_STRATEGIES = {
    "swing_trend",
    "ema_cross_9_21",
    "ema_cross_20_50",
    "ema_cross_50_200",
    "supertrend",
    "adx_trend_strength",
    "futures_trend",
    "futures_momentum",
    "growth_momentum",
    "macd_momentum",
    "rsi_momentum",
    "long_term_trend",
    "position_hold",
}


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
        try:
            macro_snap = self.data.get_macro_snapshot()
        except YahooFinanceError as e:
            return self._reject(
                symbol_clean, exchange, trade_type, 0.0,
                f"Yahoo Finance macro unavailable: {e}",
                [], "", "", "",
            )
        macro = self.macro.analyze(
            nifty_change_pct=macro_snap.get("nifty_change_pct"),
            india_vix=macro_snap.get("india_vix"),
            usdinr_change_pct=macro_snap.get("usdinr_change_pct"),
        )

        try:
            df = self.data.get_ohlc(symbol_clean, exchange)
            live_quote = self.data.get_quote(symbol_clean, exchange)
        except YahooFinanceError as e:
            return self._reject(
                symbol_clean, exchange, trade_type, 0.0,
                f"Yahoo Finance price unavailable: {e}",
                [], "", "", macro.summary,
            )
        snap = self.ta.analyze(df)
        # Prefer live Yahoo regularMarketPrice for decision levels when available
        yahoo_px = float(live_quote.get("price") or snap.close)
        if yahoo_px > 0:
            snap.close = yahoo_px
        info = self.data.get_info(symbol_clean, exchange)
        fund = self.fa.analyze(symbol_clean, info)
        opt = self.oa.analyze(None, spot=snap.close)
        index_sym = is_index(symbol_clean)
        price_source = live_quote.get("source") or getattr(self.data, "last_source", "yahoo_finance")
        yahoo_sym = live_quote.get("yahoo_symbol") or ""

        # Liquidity check (relaxed for index underlyings used in F&O)
        min_liq = self.settings.risk.min_liquidity_avg_volume
        if index_sym:
            min_liq = min(min_liq, 50_000)
        if snap.avg_volume < min_liq and trade_type not in (TradeType.FUTURES, TradeType.OPTIONS):
            return self._reject(
                symbol_clean, exchange, trade_type, snap.close,
                f"Low liquidity: avg volume {snap.avg_volume:.0f} < {min_liq}",
                snap.supporting, fund.summary, opt.summary, macro.summary,
            )
        if snap.avg_volume < min_liq and trade_type in (TradeType.FUTURES, TradeType.OPTIONS) and not index_sym:
            return self._reject(
                symbol_clean, exchange, trade_type, snap.close,
                f"Low liquidity for stock F&O: avg volume {snap.avg_volume:.0f} < {min_liq}",
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
        atr_mult = float(self.settings.position_sizing.atr_multiplier or 2.0)
        risk_scale = float(getattr(macro, "size_multiplier", 1.0) or 1.0)
        # After a loss streak, cut size before the hard consecutive-loss halt
        if self.risk.state.consecutive_losses >= 1:
            risk_scale = min(risk_scale, 0.5)
        if self.risk.state.consecutive_losses >= 2:
            risk_scale = min(risk_scale, 0.25)

        # When a named strategy already produced a side, do not re-apply trend/momentum
        # gates (they often double-reject valid strategy signals).
        if strat is None:
            if entry_cfg.require_trend and snap.trend in (MarketDirection.NEUTRAL, MarketDirection.RANGE_BOUND):
                rejects.append("Trend not confirmed")
            if entry_cfg.require_momentum and snap.momentum == "neutral":
                rejects.append("Momentum not confirmed")
        elif strat.name == "intraday_mean_reversion":
            pass  # mean-reversion intentionally trades ranges / neutral momentum

        # ADX strength confirmation (TECHM post-mortem: losers lacked trend strength)
        min_adx = float(getattr(entry_cfg, "min_adx", 25.0) or 25.0)
        require_adx = bool(getattr(entry_cfg, "require_adx", True))
        strat_name = strat.name if strat else ""
        if require_adx and strat_name not in _ADX_EXEMPT_STRATEGIES and snap.adx < min_adx:
            rejects.append(f"ADX {snap.adx:.1f} < {min_adx:.0f} — trend strength not confirmed")

        min_vol = float(getattr(entry_cfg, "min_volume_ratio", 1.3) or 1.3)
        breakout_vol = float(getattr(entry_cfg, "min_breakout_volume_ratio", 1.5) or 1.5)
        if (
            entry_cfg.require_volume
            and strat_name not in _VOLUME_EXEMPT_STRATEGIES
            and snap.volume_ratio < min_vol
            and not index_sym
        ):
            rejects.append(f"Volume not confirmed (ratio {snap.volume_ratio:.2f} < {min_vol:.2f})")
        # Breakout family needs stronger volume confirmation
        if strat_name in _BREAKOUT_STRATEGIES and snap.volume_ratio < breakout_vol and not index_sym:
            rejects.append(
                f"Breakout volume weak (ratio {snap.volume_ratio:.2f} < {breakout_vol:.2f})"
            )

        # RSI alignment for trend / breakout (not mean-reversion)
        if (
            bool(getattr(entry_cfg, "require_rsi_alignment", True))
            and strat_name not in _ADX_EXEMPT_STRATEGIES
            and trade_type in (TradeType.SWING, TradeType.INTRADAY, TradeType.FUTURES)
        ):
            min_rsi = float(getattr(entry_cfg, "min_rsi_long", 52.0) or 52.0)
            max_rsi = float(getattr(entry_cfg, "max_rsi_short", 48.0) or 48.0)
            if side == Side.BUY and snap.rsi < min_rsi:
                rejects.append(f"RSI {snap.rsi:.1f} < {min_rsi:.0f} — long momentum not confirmed")
            if side == Side.SELL and snap.rsi > max_rsi:
                rejects.append(f"RSI {snap.rsi:.1f} > {max_rsi:.0f} — short momentum not confirmed")

        # EMA stack alignment (avoid entering against structure)
        if (
            bool(getattr(entry_cfg, "require_ema_stack", True))
            and strat_name in (_TREND_STRATEGIES | _BREAKOUT_STRATEGIES | {""})
            and trade_type in (TradeType.SWING, TradeType.FUTURES)
        ):
            bull_stack = snap.ema_9 > snap.ema_21 > snap.ema_50
            bear_stack = snap.ema_9 < snap.ema_21 < snap.ema_50
            if side == Side.BUY and not bull_stack:
                rejects.append("EMA stack not bullish (need 9>21>50) — avoid counter-structure long")
            if side == Side.SELL and not bear_stack:
                rejects.append("EMA stack not bearish (need 9<21<50) — avoid counter-structure short")

        # India VIX / USDINR — harden vs TECHM-style entries into bad vol regimes
        vix_regime = getattr(macro, "vix_regime", "unknown")
        caution = set(getattr(macro, "caution_flags", []) or [])
        if (
            bool(getattr(entry_cfg, "block_on_vix_elevated", True))
            and vix_regime == "elevated"
            and trade_type in (TradeType.SWING, TradeType.INTRADAY)
            and strat_name not in _ADX_EXEMPT_STRATEGIES
        ):
            rejects.append(f"India VIX elevated ({macro.vix_level}) — no fresh equity risk")
        if (
            bool(getattr(entry_cfg, "block_breakout_on_vix_complacency", True))
            and "vix_complacency" in caution
            and strat_name in _BREAKOUT_STRATEGIES
        ):
            rejects.append("VIX complacency — block breakout entries (false break risk)")
        if "inr_weak" in caution and side == Side.BUY and trade_type == TradeType.SWING and not index_sym:
            # Extra bar for equity longs when USDINR pressure (IT/exporters sensitive)
            if snap.adx < min_adx + 5:
                rejects.append("USDINR weak + ADX not strong enough for equity long")

        # Fundamentals: skip / relax for index F&O and short-horizon intraday
        if not index_sym and fund.score < entry_cfg.min_fundamental_score:
            if trade_type == TradeType.INTRADAY:
                # Intraday is technical — soft-warn via size cut only (below)
                pass
            elif trade_type not in (TradeType.FUTURES, TradeType.OPTIONS):
                rejects.append(f"Fundamental score {fund.score:.0f} < {entry_cfg.min_fundamental_score}")
            elif fund.score < entry_cfg.min_fundamental_score - 15:
                rejects.append(f"Fundamental score {fund.score:.0f} too weak for stock F&O")
        # Equity swing must not be tech-only when fundamentals are sparse
        require_fund_metrics = bool(getattr(entry_cfg, "require_fundamental_metrics", True))
        fund_quality = getattr(fund, "data_quality", "unknown")
        if (
            require_fund_metrics
            and not index_sym
            and trade_type == TradeType.SWING
            and fund_quality == "sparse"
        ):
            rejects.append("Sparse fundamentals — refuse tech-only swing entry")
        if macro.avoid_new_risk and entry_cfg.avoid_major_news:
            rejects.append(f"Macro risk elevated: {macro.summary}")
        if risk_scale <= 0:
            rejects.append(f"Macro size multiplier zero: {macro.summary}")

        # Index regime alignment — do not buy dips blindly in a down tape
        nifty_bias = getattr(macro, "nifty_bias", "neutral")
        if side == Side.BUY and nifty_bias == "bearish" and trade_type in (
            TradeType.SWING,
            TradeType.FUTURES,
            TradeType.OPTIONS,
        ):
            rejects.append("Nifty bearish — no fresh longs (macro alignment)")
        if side == Side.SELL and nifty_bias == "bullish" and trade_type in (
            TradeType.SWING,
            TradeType.FUTURES,
        ):
            rejects.append("Nifty bullish — no fresh shorts (macro alignment)")

        # USDINR / VIX caution → extra size cut already in macro; raise bar
        if getattr(macro, "require_extra_confirmation", False):
            risk_scale = min(risk_scale, 0.65)

        # Weak/missing fundamentals: demand stronger technical confidence + half size
        weak_fundamentals = (not index_sym) and (fund.score <= 50 or fund_quality in ("sparse", "partial"))
        if weak_fundamentals:
            risk_scale = min(risk_scale, 0.5)

        # Multi-factor confluence: trend, momentum, volume, ADX, macro, fundamentals
        confluence: list[str] = []
        if snap.trend in (MarketDirection.BULLISH, MarketDirection.BEARISH):
            confluence.append("trend")
        if snap.momentum not in ("neutral",):
            confluence.append("momentum")
        if snap.volume_ratio >= min_vol:
            confluence.append("volume")
        if snap.adx >= min_adx:
            confluence.append("adx")
        if not macro.avoid_new_risk and nifty_bias != ("bearish" if side == Side.BUY else "bullish"):
            confluence.append("macro")
        if index_sym or (fund.score >= entry_cfg.min_fundamental_score and fund_quality != "sparse"):
            confluence.append("fundamental")
        min_factors = int(getattr(entry_cfg, "min_confluence_factors", 4) or 4)
        if getattr(macro, "require_extra_confirmation", False):
            min_factors = max(min_factors, 5)
        if trade_type == TradeType.SWING and len(confluence) < min_factors:
            rejects.append(
                f"Confluence {len(confluence)}/{min_factors} weak "
                f"({', '.join(confluence) or 'none'}) — need trend/momentum/vol/ADX/macro/fund"
            )

        # --- Product-specific levels & sizing ---
        futures_note = ""
        options_note = opt.summary
        order_side = side
        fo_meta = ""

        if trade_type == TradeType.FUTURES:
            # Futures paper mark = Yahoo underlying spot (no synthetic basis drift)
            fut_px = round(float(snap.close), 2)
            fut = self.futures.analyze(snap.close, fut_px, days_to_expiry=30)
            futures_note = f"{fut.summary} | Yahoo spot {yahoo_sym or symbol_clean}={fut_px}"
            levels = self.ta.levels_for_trade(snap, side, atr_stop_mult=atr_mult)
            # Align entry to Yahoo spot (levels already use snap.close)
            levels["entry"] = fut_px
            stop_dist = abs(levels["entry"] - levels["stop_loss"])
            if side == Side.BUY:
                levels["stop_loss"] = round(levels["entry"] - stop_dist, 2) if stop_dist else levels["stop_loss"]
                levels["target_1"] = round(levels["entry"] + 2 * stop_dist, 2)
                levels["target_2"] = round(levels["entry"] + 3 * stop_dist, 2)
            else:
                levels["stop_loss"] = round(levels["entry"] + stop_dist, 2) if stop_dist else levels["stop_loss"]
                levels["target_1"] = round(levels["entry"] - 2 * stop_dist, 2)
                levels["target_2"] = round(levels["entry"] - 3 * stop_dist, 2)
            levels["risk_reward"] = 2.0
            mult = lot_multiplier(TradeType.FUTURES, symbol_clean)
            # Ensure ≥1 lot can fit inside 1% risk — but NEVER compress stop below ATR floor
            risk_budget = capital * (self.settings.risk.max_risk_per_trade_pct / 100.0) * max(risk_scale, 0.01)
            max_stop = risk_budget / mult if mult else levels["entry"]
            stop_dist = abs(levels["entry"] - levels["stop_loss"])
            min_stop_atr = float(getattr(self.settings.position_sizing, "min_stop_atr_mult", 2.0) or 2.0)
            atr_floor = max(snap.atr, levels["entry"] * 0.005) * min_stop_atr
            if stop_dist > max_stop > 0:
                if max_stop < atr_floor:
                    rejects.append(
                        f"Futures ATR stop ₹{stop_dist:.2f} exceeds 1% risk budget "
                        f"(max ₹{max_stop:.2f}) — refuse compressed stop (TECHM ATR floor)"
                    )
                else:
                    if side == Side.BUY:
                        levels["stop_loss"] = round(levels["entry"] - max_stop, 2)
                        levels["target_1"] = round(levels["entry"] + 2 * max_stop, 2)
                        levels["target_2"] = round(levels["entry"] + 3 * max_stop, 2)
                    else:
                        levels["stop_loss"] = round(levels["entry"] + max_stop, 2)
                        levels["target_1"] = round(levels["entry"] - 2 * max_stop, 2)
                        levels["target_2"] = round(levels["entry"] - 3 * max_stop, 2)
                    levels["risk_reward"] = 2.0
            size = self.sizer.calculate(
                capital=capital,
                entry=levels["entry"],
                stop_loss=levels["stop_loss"],
                side=side,
                atr=None,
                lot_size=mult,
                broker_margin_pct=MARGIN_FRAC["FUTURES"] * 100,
                quantity_as_lots=True,
                risk_scale=risk_scale,
            )
            # Tag nearest monthly (or weekly for index) expiry on futures
            exps = list_expiries(symbol_clean, count=6)
            monthly = next((e for e in exps if e.get("kind") == "monthly"), exps[0] if exps else None)
            if monthly:
                fo_meta = encode_fo_meta(snap.close, "FUT", float(round(snap.close)), monthly["expiry"])
            strategy_tags = strategy_tags + ["futures", f"lot={mult}"]
            if monthly:
                strategy_tags.append(f"expiry={monthly['expiry']}")
            reason_prefix = (
                f"FUTURES ({mult} mult"
                + (f", expiry {monthly['label']}" if monthly else "")
                + f"). {futures_note}. {fo_meta} "
            )

        elif trade_type == TradeType.OPTIONS:
            # Directional long premium only (CE on bullish, PE on bearish)
            kind = option_kind_for_side(side)
            premium = paper_option_premium(snap.close, snap.atr)
            levels = paper_option_levels(premium)
            strike = round(snap.close / 50) * 50
            if symbol_clean.upper() == "BANKNIFTY":
                strike = round(snap.close / 100) * 100
            exps = list_expiries(symbol_clean, count=6)
            near = exps[0] if exps else None
            expiry_iso = near["expiry"] if near else None
            fo_meta = encode_fo_meta(snap.close, kind, float(strike), expiry_iso)
            options_note = (
                f"Paper {kind} @ strike {strike:.0f}"
                + (f", expiry {near['label']}" if near else "")
                + f", premium ₹{premium:.2f} (ATM proxy). {opt.summary}"
            )
            order_side = Side.BUY  # always long options in v1
            mult = lot_multiplier(TradeType.OPTIONS, symbol_clean)
            # Cap premium stop so 1 lot fits 1% risk
            risk_budget = capital * (self.settings.risk.max_risk_per_trade_pct / 100.0)
            max_prem_stop = risk_budget / mult if mult else levels["entry"] * 0.5
            if (levels["entry"] - levels["stop_loss"]) > max_prem_stop > 0:
                levels["stop_loss"] = round(max(levels["entry"] - max_prem_stop, 0.5), 2)
                risk = levels["entry"] - levels["stop_loss"]
                levels["target_1"] = round(levels["entry"] + 2 * risk, 2)
                levels["target_2"] = round(levels["entry"] + 3 * risk, 2)
                levels["risk_reward"] = 2.0
            size = self.sizer.calculate(
                capital=capital,
                entry=levels["entry"],
                stop_loss=levels["stop_loss"],
                side=order_side,
                atr=None,  # do not widen option premium stops via ATR
                lot_size=mult,
                broker_margin_pct=MARGIN_FRAC["OPTIONS_BUY"] * 100,
                quantity_as_lots=True,
                risk_scale=risk_scale,
            )
            strategy_tags = strategy_tags + ["options", kind, f"lot={mult}"]
            if expiry_iso:
                strategy_tags.append(f"expiry={expiry_iso}")
            reason_prefix = f"OPTIONS long {kind}. {fo_meta} "

        else:
            levels = self.ta.levels_for_trade(snap, side, atr_stop_mult=atr_mult)
            # Hard ATR floor check — reject noise-tight stops
            stop_dist = abs(levels["entry"] - levels["stop_loss"])
            min_stop_atr = float(getattr(self.settings.position_sizing, "min_stop_atr_mult", 2.0) or 2.0)
            atr_floor = max(snap.atr, levels["entry"] * 0.005) * min_stop_atr
            if stop_dist + 1e-9 < atr_floor:
                rejects.append(
                    f"Stop ₹{stop_dist:.2f} < {min_stop_atr:.1f}×ATR ₹{atr_floor:.2f} — noise stop risk"
                )
            size = self.sizer.calculate(
                capital=capital,
                entry=levels["entry"],
                stop_loss=levels["stop_loss"],
                side=side,
                atr=snap.atr,
                risk_scale=risk_scale,
            )
            reason_prefix = ""

        if levels["risk_reward"] < self.settings.risk.min_risk_reward:
            rejects.append(f"RR {levels['risk_reward']:.2f} < {self.settings.risk.min_risk_reward}")

        if size.quantity <= 0:
            rejects.append(size.notes or "Position size zero")

        scores = self._score(snap, fund.score if not index_sym else max(fund.score, 60), order_side, levels["risk_reward"], macro)
        scores.confidence = round(min(100.0, scores.confidence + strategy_boost), 1)
        scores.probability_of_success = round(min(100.0, scores.confidence * 0.85), 1)
        min_conf = entry_cfg.min_confidence
        if weak_fundamentals:
            min_conf = max(min_conf, 70)  # demand stronger tech when fundamentals thin
        if scores.confidence < min_conf:
            rejects.append(f"Confidence {scores.confidence:.0f} < {min_conf}")

        reason = reason_prefix + self._build_reason(order_side, snap, fund, opt, macro)
        if strategy_reason:
            reason = f"[{strat.name if strat else trade_type.value}] {strategy_reason}. {reason}"
        alt = self._alternative(order_side, snap)
        supporting = snap.supporting + ([f"strategy:{t}" for t in strategy_tags] if strategy_tags else [])

        rec = TradeRecommendation(
            symbol=symbol_clean,
            exchange=exchange,
            market_direction=snap.trend,
            trade_type=trade_type,
            side=order_side,
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
            fundamental_summary=fund.summary if not index_sym else "Index underlying — fundamental gate relaxed.",
            options_summary=options_note,
            risk_notes=(
                f"Risking ₹{size.capital_at_risk:.0f} ({size.risk_pct:.2f}% of capital). "
                f"ATR={snap.atr:.2f}. Product={trade_type.value}. "
                f"Price source={price_source}"
                + (f" ({yahoo_sym})" if yahoo_sym else "")
                + f". Confluence={len(confluence)}:{','.join(confluence)}"
                + (f". Macro flags={','.join(getattr(macro, 'caution_flags', []) or [])}" if getattr(macro, "caution_flags", None) else "")
                + ". "
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
        if getattr(macro, "size_multiplier", 1.0) < 1.0:
            conf -= 8
        if getattr(macro, "require_extra_confirmation", False):
            conf -= 5
        if "vix_complacency" in getattr(macro, "caution_flags", []):
            conf -= 5
        if "inr_weak" in getattr(macro, "caution_flags", []):
            conf -= 5
        if snap.adx < 20:
            conf -= 10
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
