"""
Historical backtester — same risk, sizing, and exit rules as paper trading.

Walks bar-by-bar; never looks ahead. Capital preservation rules enforced.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from quantx.analysis.strategies import Strategy, get_strategy
from quantx.analysis.technical import TechnicalAnalyzer
from quantx.core.config import Settings, get_settings
from quantx.core.models import Side
from quantx.core.position_sizing import PositionSizer
from quantx.core.risk import RiskManager
from quantx.data.market_data import MarketDataService
from quantx.execution.costs import PaperCostModel


@dataclass
class BacktestTrade:
    symbol: str
    side: str
    entry_date: str
    exit_date: str
    entry: float
    exit: float
    quantity: int
    pnl: float
    pnl_pct: float
    reason_entry: str
    reason_exit: str
    fees: float


@dataclass
class BacktestResult:
    strategy: str
    symbol: str
    starting_capital: float
    ending_capital: float
    total_pnl: float
    total_return_pct: float
    max_drawdown_pct: float
    trades: int
    wins: int
    losses: int
    win_rate: float
    expectancy: float
    total_fees: float
    trade_log: list[BacktestTrade] = field(default_factory=list)
    equity_curve: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "strategy": self.strategy,
            "symbol": self.symbol,
            "starting_capital": self.starting_capital,
            "ending_capital": round(self.ending_capital, 2),
            "total_pnl": round(self.total_pnl, 2),
            "total_return_pct": round(self.total_return_pct, 3),
            "max_drawdown_pct": round(self.max_drawdown_pct, 3),
            "trades": self.trades,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(self.win_rate, 2),
            "expectancy": round(self.expectancy, 2),
            "total_fees": round(self.total_fees, 2),
            "trade_log": [t.__dict__ for t in self.trade_log],
            "equity_curve": self.equity_curve[-120:],  # trim for API
            "notes": self.notes,
        }


class Backtester:
    def __init__(
        self,
        settings: Optional[Settings] = None,
        market_data: Optional[MarketDataService] = None,
    ):
        self.settings = settings or get_settings()
        self.data = market_data or MarketDataService(use_live=True)
        self.ta = TechnicalAnalyzer()
        self.sizer = PositionSizer(self.settings)
        self.costs = PaperCostModel(
            slippage_bps=float((self.settings.broker or {}).get("slippage_bps", 5))
            if isinstance(self.settings.broker, dict)
            else 5.0
        )

    def run(
        self,
        symbol: str,
        strategy: str | Strategy = "swing_trend",
        exchange: str = "NSE",
        period: str = "1y",
        capital: Optional[float] = None,
    ) -> BacktestResult:
        strat = get_strategy(strategy) if isinstance(strategy, str) else strategy
        capital = float(capital if capital is not None else self.settings.capital.initial)
        start_cap = capital
        peak = capital
        max_dd = 0.0
        total_fees = 0.0

        df = self.data.get_ohlc(symbol, exchange, period=period, interval="1d")
        if len(df) < 60:
            return BacktestResult(
                strategy=strat.name,
                symbol=symbol.upper(),
                starting_capital=start_cap,
                ending_capital=start_cap,
                total_pnl=0,
                total_return_pct=0,
                max_drawdown_pct=0,
                trades=0,
                wins=0,
                losses=0,
                win_rate=0,
                expectancy=0,
                total_fees=0,
                notes=["Insufficient history for backtest (need ≥60 bars)"],
            )

        risk = RiskManager(self.settings)
        risk.update_state(capital=capital, peak_capital=capital)

        open_pos: Optional[dict] = None
        trades: list[BacktestTrade] = []
        equity: list[dict] = []
        notes: list[str] = []

        # Warm-up: start after enough bars for indicators
        for i in range(50, len(df)):
            window = df.iloc[: i + 1]
            bar = window.iloc[-1]
            ts = window.index[-1]
            date_s = ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts)
            price = float(bar["close"])

            # Manage open position
            if open_pos is not None:
                side: Side = open_pos["side"]
                stop = open_pos["stop"]
                t1 = open_pos["t1"]
                t2 = open_pos["t2"]
                trail = open_pos.get("trail")
                if self.settings.exit.use_trailing_stop:
                    dist = abs(open_pos["entry"] - stop) * 0.5
                    if side == Side.BUY:
                        cand = price - dist
                        trail = cand if trail is None else max(trail, cand)
                        eff_stop = max(stop, trail)
                    else:
                        cand = price + dist
                        trail = cand if trail is None else min(trail, cand)
                        eff_stop = min(stop, trail)
                    open_pos["trail"] = trail
                else:
                    eff_stop = stop

                exit_reason = None
                if side == Side.BUY:
                    if price <= eff_stop:
                        exit_reason = "Stop / trail"
                    elif price >= t2:
                        exit_reason = "Target 2"
                    elif price >= t1:
                        exit_reason = "Target 1"
                else:
                    if price >= eff_stop:
                        exit_reason = "Stop / trail"
                    elif price <= t2:
                        exit_reason = "Target 2"
                    elif price <= t1:
                        exit_reason = "Target 1"

                # Time exit
                bars_held = i - open_pos["entry_idx"]
                if exit_reason is None and bars_held >= self.settings.exit.time_exit_bars:
                    exit_reason = "Time exit"

                if exit_reason:
                    slipped = self.costs.apply_slippage(price, side, is_entry=False)
                    style = "intraday" if strat.trade_type.value == "INTRADAY" else "swing"
                    fee = self.costs.estimate(
                        open_pos["entry"], slipped, open_pos["qty"], side, style  # type: ignore
                    ).total
                    if side == Side.BUY:
                        gross = (slipped - open_pos["entry"]) * open_pos["qty"]
                    else:
                        gross = (open_pos["entry"] - slipped) * open_pos["qty"]
                    pnl = gross - fee
                    capital += pnl
                    total_fees += fee
                    risk.register_trade_result(pnl)
                    notional = open_pos["entry"] * open_pos["qty"]
                    trades.append(
                        BacktestTrade(
                            symbol=symbol.upper(),
                            side=side.value,
                            entry_date=open_pos["entry_date"],
                            exit_date=date_s,
                            entry=open_pos["entry"],
                            exit=slipped,
                            quantity=open_pos["qty"],
                            pnl=round(pnl, 2),
                            pnl_pct=round(pnl / notional * 100, 3) if notional else 0,
                            reason_entry=open_pos["reason"],
                            reason_exit=exit_reason,
                            fees=round(fee, 2),
                        )
                    )
                    open_pos = None
                    risk.update_state(open_positions=0)

            # Equity / drawdown
            peak = max(peak, capital)
            dd = (peak - capital) / peak * 100 if peak else 0
            max_dd = max(max_dd, dd)
            equity.append({"date": date_s, "equity": round(capital, 2), "drawdown_pct": round(dd, 3)})

            # Entries only if flat and risk allows
            if open_pos is not None:
                continue
            status = risk.status()
            if not status.can_trade:
                continue
            if dd >= self.settings.risk.max_drawdown_pct:
                notes.append(f"Max drawdown lock at {date_s}")
                break

            try:
                snap = self.ta.analyze(window)
            except ValueError:
                continue

            signal = strat.evaluate(snap, window)
            if signal.side is None:
                continue
            if snap.avg_volume < self.settings.risk.min_liquidity_avg_volume:
                continue
            if snap.volume_ratio < 1.0 and "volume" not in signal.tags:
                continue

            levels = self.ta.levels_for_trade(snap, signal.side)
            # Include expected entry slippage in sizing
            entry = self.costs.apply_slippage(levels["entry"], signal.side, is_entry=True)
            stop = levels["stop_loss"]
            # Restore min RR after slip
            min_rr = self.settings.risk.min_risk_reward
            risk_amt = abs(entry - stop)
            if risk_amt <= 0:
                continue
            if signal.side == Side.BUY:
                t1 = entry + min_rr * risk_amt
                t2 = entry + (min_rr + 1) * risk_amt
            else:
                t1 = entry - min_rr * risk_amt
                t2 = entry - (min_rr + 1) * risk_amt

            size = self.sizer.calculate(
                capital=capital, entry=entry, stop_loss=stop, side=signal.side, atr=snap.atr
            )
            if size.quantity <= 0:
                continue
            if size.risk_pct > self.settings.risk.max_risk_per_trade_pct + 0.05:
                continue

            # Entry fee (half round-trip estimate)
            style = "intraday" if strat.trade_type.value == "INTRADAY" else "swing"
            fee_est = self.costs.estimate(entry, stop, size.quantity, signal.side, style)  # type: ignore
            entry_fee = fee_est.total * 0.5
            capital -= entry_fee
            total_fees += entry_fee

            open_pos = {
                "side": signal.side,
                "entry": entry,
                "stop": stop,
                "t1": t1,
                "t2": t2,
                "qty": size.quantity,
                "entry_idx": i,
                "entry_date": date_s,
                "reason": signal.reason,
                "trail": None,
            }
            risk.update_state(open_positions=1, capital=capital)

        # Force close at end
        if open_pos is not None:
            last = df.iloc[-1]
            ts = df.index[-1]
            date_s = ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts)
            price = float(last["close"])
            side = open_pos["side"]
            slipped = self.costs.apply_slippage(price, side, is_entry=False)
            style = "intraday" if strat.trade_type.value == "INTRADAY" else "swing"
            fee = self.costs.estimate(open_pos["entry"], slipped, open_pos["qty"], side, style).total  # type: ignore
            gross = (
                (slipped - open_pos["entry"]) * open_pos["qty"]
                if side == Side.BUY
                else (open_pos["entry"] - slipped) * open_pos["qty"]
            )
            pnl = gross - fee
            capital += pnl
            total_fees += fee
            notional = open_pos["entry"] * open_pos["qty"]
            trades.append(
                BacktestTrade(
                    symbol=symbol.upper(),
                    side=side.value,
                    entry_date=open_pos["entry_date"],
                    exit_date=date_s,
                    entry=open_pos["entry"],
                    exit=slipped,
                    quantity=open_pos["qty"],
                    pnl=round(pnl, 2),
                    pnl_pct=round(pnl / notional * 100, 3) if notional else 0,
                    reason_entry=open_pos["reason"],
                    reason_exit="EOD flat",
                    fees=round(fee, 2),
                )
            )

        wins = [t for t in trades if t.pnl > 0]
        losses = [t for t in trades if t.pnl <= 0]
        win_rate = len(wins) / len(trades) * 100 if trades else 0
        avg_win = sum(t.pnl for t in wins) / len(wins) if wins else 0
        avg_loss = sum(t.pnl for t in losses) / len(losses) if losses else 0
        expectancy = avg_win * (win_rate / 100) + avg_loss * (1 - win_rate / 100) if trades else 0

        return BacktestResult(
            strategy=strat.name,
            symbol=symbol.upper(),
            starting_capital=start_cap,
            ending_capital=capital,
            total_pnl=capital - start_cap,
            total_return_pct=(capital - start_cap) / start_cap * 100 if start_cap else 0,
            max_drawdown_pct=max_dd,
            trades=len(trades),
            wins=len(wins),
            losses=len(losses),
            win_rate=win_rate,
            expectancy=expectancy,
            total_fees=total_fees,
            trade_log=trades,
            equity_curve=equity,
            notes=notes or ["Backtest complete under QuantX risk rules."],
        )

