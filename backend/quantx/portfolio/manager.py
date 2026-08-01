"""Portfolio management — positions, PnL, never average losers."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from quantx.core.config import Settings, get_settings
from quantx.core.models import (
    PortfolioSnapshot,
    Position,
    PositionStatus,
    Side,
    TradeJournalEntry,
    TradeRecommendation,
)
from quantx.core.risk import RiskManager
from quantx.data.market_data import MarketDataService
from quantx.portfolio.db import Database


class PortfolioManager:
    def __init__(
        self,
        db: Optional[Database] = None,
        risk: Optional[RiskManager] = None,
        market_data: Optional[MarketDataService] = None,
        settings: Optional[Settings] = None,
    ):
        self.settings = settings or get_settings()
        self.db = db or Database()
        self.risk = risk or RiskManager(self.settings)
        self.data = market_data or MarketDataService()
        self._hydrate_risk_from_db()

    def _hydrate_risk_from_db(self) -> None:
        capital = self.db.get_state("capital", self.settings.capital.initial)
        peak = self.db.get_state("peak_capital", capital)
        daily = self.db.get_state("daily_realized_pnl", 0.0)
        weekly = self.db.get_state("weekly_realized_pnl", 0.0)
        consecutive = self.db.get_state("consecutive_losses", 0)
        kill = self.db.get_state("kill_switch", False)
        dd_lock = self.db.get_state("max_drawdown_lock", False)
        override = self.db.get_state("manual_override", False)
        opens = self.db.list_positions("OPEN")
        open_risk = sum(p.capital_at_risk for p in opens)
        self.risk.update_state(
            capital=capital,
            peak_capital=peak,
            daily_realized_pnl=daily,
            weekly_realized_pnl=weekly,
            consecutive_losses=consecutive,
            kill_switch=kill,
            max_drawdown_lock=dd_lock,
            manual_override=override,
            open_positions=len(opens),
            open_risk_amount=open_risk,
        )

    def _persist_risk(self) -> None:
        s = self.risk.state
        self.db.set_state("capital", s.capital)
        self.db.set_state("peak_capital", s.peak_capital)
        self.db.set_state("daily_realized_pnl", s.daily_realized_pnl)
        self.db.set_state("weekly_realized_pnl", s.weekly_realized_pnl)
        self.db.set_state("consecutive_losses", s.consecutive_losses)
        self.db.set_state("kill_switch", s.kill_switch)
        self.db.set_state("max_drawdown_lock", s.max_drawdown_lock)
        self.db.set_state("manual_override", s.manual_override)

    def snapshot(self) -> PortfolioSnapshot:
        self._hydrate_risk_from_db()
        opens = self.db.list_positions("OPEN")
        unrealized = sum(p.pnl for p in opens)
        total_pnl = (self.risk.state.capital + unrealized) - self.settings.capital.initial
        used_margin = sum(p.entry_price * p.quantity * 0.2 for p in opens)  # approx 20% margin
        return PortfolioSnapshot(
            capital=self.risk.state.capital,
            available_margin=max(0, self.risk.state.capital - used_margin),
            used_margin=used_margin,
            open_positions=len(opens),
            unrealized_pnl=round(unrealized, 2),
            realized_pnl_today=round(self.risk.state.daily_realized_pnl, 2),
            realized_pnl_week=round(self.risk.state.weekly_realized_pnl, 2),
            total_pnl=round(total_pnl, 2),
            daily_pnl_pct=round(self.risk.daily_loss_pct if self.risk.state.daily_realized_pnl < 0 else
                                (self.risk.state.daily_realized_pnl / self.risk.state.capital * 100 if self.risk.state.capital else 0), 3),
            weekly_pnl_pct=round(
                self.risk.state.weekly_realized_pnl / self.risk.state.capital * 100 if self.risk.state.capital else 0, 3
            ),
            drawdown_pct=round(self.risk.drawdown_pct, 3),
            consecutive_losses=self.risk.state.consecutive_losses,
            trading_halted=self.risk.state.trading_halted,
            halt_reason=self.risk.state.halt_reason,
            kill_switch_active=self.risk.state.kill_switch,
            mode=self.settings.agent.mode,
        )

    def open_from_recommendation(self, rec: TradeRecommendation) -> Position:
        self._hydrate_risk_from_db()
        if not rec.valid:
            raise ValueError(rec.rejection_reason or "Invalid recommendation")

        ok, reasons = self.risk.validate_recommendation(rec)
        if not ok:
            raise ValueError("; ".join(reasons))

        # Never average losing trades
        opens = self.db.list_positions("OPEN")
        for p in opens:
            if p.symbol == rec.symbol and p.side == rec.side and p.pnl < 0:
                raise ValueError(
                    f"Refusing to average losing {p.symbol} position (PnL {p.pnl:.2f}). Safety rule."
                )

        pos = Position(
            symbol=rec.symbol,
            exchange=rec.exchange,
            side=rec.side,
            trade_type=rec.trade_type,
            quantity=rec.quantity,
            entry_price=rec.entry,
            current_price=rec.entry,
            stop_loss=rec.stop_loss,
            target_1=rec.target_1,
            target_2=rec.target_2,
            capital_at_risk=rec.capital_at_risk,
            confidence=rec.scores.confidence,
            reason=rec.reason,
        )
        pos.id = self.db.insert_position(pos)
        self.risk.update_state(open_positions=len(self.db.list_positions("OPEN")))
        self._persist_risk()
        return pos

    def mark_to_market(self) -> list[Position]:
        opens = self.db.list_positions("OPEN")
        updated = []
        for p in opens:
            try:
                q = self.data.get_quote(p.symbol, p.exchange)
                price = q["price"]
            except Exception:
                price = p.current_price
            p.current_price = price
            if p.side == Side.BUY:
                p.pnl = (price - p.entry_price) * p.quantity
            else:
                p.pnl = (p.entry_price - price) * p.quantity
            notional = p.entry_price * p.quantity
            p.pnl_pct = (p.pnl / notional * 100) if notional else 0

            # Trailing stop: ratchet in favor of trade
            if self.settings.exit.use_trailing_stop:
                # Use 2 ATR approx via 1% of price as fallback trail distance
                trail_dist = abs(p.entry_price - p.stop_loss) * 0.5
                if p.side == Side.BUY:
                    candidate = price - trail_dist
                    if p.trailing_stop is None or candidate > p.trailing_stop:
                        p.trailing_stop = candidate
                    effective_stop = max(p.stop_loss, p.trailing_stop or p.stop_loss)
                else:
                    candidate = price + trail_dist
                    if p.trailing_stop is None or candidate < p.trailing_stop:
                        p.trailing_stop = candidate
                    effective_stop = min(p.stop_loss, p.trailing_stop or p.stop_loss)
            else:
                effective_stop = p.stop_loss

            exit_reason = None
            if p.side == Side.BUY:
                if price <= effective_stop:
                    exit_reason = "Stop loss / trailing stop hit"
                elif price >= p.target_2:
                    exit_reason = "Target 2 hit"
                elif price >= p.target_1:
                    exit_reason = "Target 1 hit"
            else:
                if price >= effective_stop:
                    exit_reason = "Stop loss / trailing stop hit"
                elif price <= p.target_2:
                    exit_reason = "Target 2 hit"
                elif price <= p.target_1:
                    exit_reason = "Target 1 hit"

            if exit_reason:
                self.close_position(p.id, price, exit_reason)  # type: ignore[arg-type]
            else:
                self.db.update_position(p)
                updated.append(p)
        return self.db.list_positions("OPEN")

    def close_position(self, position_id: int, exit_price: float, reason: str) -> Position:
        self._hydrate_risk_from_db()
        p = self.db.get_position(position_id)
        if not p or p.status != PositionStatus.OPEN:
            raise ValueError("Position not found or already closed")

        p.exit_price = exit_price
        p.current_price = exit_price
        p.closed_at = datetime.utcnow()
        p.exit_reason = reason
        p.status = PositionStatus.CLOSED
        if p.side == Side.BUY:
            p.pnl = (exit_price - p.entry_price) * p.quantity
        else:
            p.pnl = (p.entry_price - exit_price) * p.quantity
        notional = p.entry_price * p.quantity
        p.pnl_pct = (p.pnl / notional * 100) if notional else 0
        self.db.update_position(p)

        self.risk.register_trade_result(p.pnl)
        self.risk.update_state(open_positions=len(self.db.list_positions("OPEN")))
        self._persist_risk()

        entry = TradeJournalEntry(
            symbol=p.symbol,
            side=p.side,
            trade_type=p.trade_type,
            entry=p.entry_price,
            exit=exit_price,
            quantity=p.quantity,
            pnl=p.pnl,
            pnl_pct=p.pnl_pct,
            reason=p.reason,
            mistakes="" if p.pnl >= 0 else "Review entry timing / stop placement",
            market_condition=reason,
            confidence=p.confidence,
            lessons="Follow risk rules; never remove stop." if p.pnl < 0 else "Plan worked — journal what confirmed.",
            opened_at=p.opened_at,
            closed_at=p.closed_at or datetime.utcnow(),
        )
        self.db.insert_journal(entry)
        return p

    def panic_exit_all(self) -> list[Position]:
        closed = []
        for p in self.db.list_positions("OPEN"):
            closed.append(self.close_position(p.id, p.current_price, "PANIC EXIT"))  # type: ignore
        return closed
