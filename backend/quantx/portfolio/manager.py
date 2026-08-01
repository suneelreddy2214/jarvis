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
    TradeType,
)
from quantx.core.risk import RiskManager
from quantx.data.market_data import MarketDataService
from quantx.portfolio.db import Database
from quantx.portfolio.margin import MarginCalculator, lot_multiplier
from quantx.analysis.fno import mark_option_premium, parse_fo_meta


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

    def margin_book(self) -> dict:
        """Stocks / F&O / ETF margin breakdown for dashboard."""
        self._hydrate_risk_from_db()
        opens = self.db.list_positions("OPEN")
        book = MarginCalculator().build_book(self.risk.state.capital, opens)
        data = book.as_dict()
        stocks = [p for p in data["positions"] if p["product"] in ("Stocks", "ETF")]
        fno = [p for p in data["positions"] if p["segment"] == "FO"]
        data["stocks"] = stocks
        data["fno"] = fno
        data["stocks_count"] = len(stocks)
        data["fno_count"] = len(fno)
        data["initial_capital"] = float(self.settings.capital.initial)
        data["unrealized_pnl"] = round(sum(p.pnl for p in opens), 2)
        return data

    def snapshot(self) -> PortfolioSnapshot:
        self._hydrate_risk_from_db()
        opens = self.db.list_positions("OPEN")
        unrealized = sum(p.pnl for p in opens)
        total_pnl = (self.risk.state.capital + unrealized) - self.settings.capital.initial
        book = MarginCalculator().build_book(self.risk.state.capital, opens)
        return PortfolioSnapshot(
            capital=self.risk.state.capital,
            available_margin=round(book.available_margin, 2),
            used_margin=round(book.total_margin_used, 2),
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

        # Never average / pyramid into an existing symbol+product (paper safety)
        opens = self.db.list_positions("OPEN")
        for p in opens:
            if p.symbol == rec.symbol and p.trade_type == rec.trade_type:
                raise ValueError(
                    f"Refusing to add to existing {p.symbol} {p.trade_type.value} position "
                    f"(side={p.side.value}, PnL {p.pnl:.2f}). No averaging."
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
            mult = lot_multiplier(p.trade_type, p.symbol)
            try:
                if p.trade_type == TradeType.OPTIONS:
                    q = self.data.get_quote(p.symbol, p.exchange)
                    meta = parse_fo_meta(p.reason)
                    if meta:
                        price = mark_option_premium(
                            p.entry_price,
                            meta["underlying_entry"],
                            float(q["price"]),
                            meta["kind"],
                        )
                    else:
                        # Fallback: scale premium with underlying move
                        price = max(0.05, p.entry_price * (1 + float(q.get("change_pct", 0)) / 200))
                else:
                    q = self.data.get_quote(p.symbol, p.exchange)
                    price = q["price"]
            except Exception:
                price = p.current_price
            p.current_price = price
            if p.side == Side.BUY:
                p.pnl = (price - p.entry_price) * p.quantity * mult
            else:
                p.pnl = (p.entry_price - price) * p.quantity * mult
            notional = p.entry_price * p.quantity * mult
            p.pnl_pct = (p.pnl / notional * 100) if notional else 0

            # Trailing stop: ratchet in favor of trade
            if self.settings.exit.use_trailing_stop:
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

    def charge_fees(self, amount: float, note: str = "") -> None:
        """Deduct paper trading costs from capital (does not count as a trade loss streak)."""
        if amount <= 0:
            return
        self._hydrate_risk_from_db()
        self.risk.state.capital -= amount
        self.risk.state.daily_realized_pnl -= amount
        self.risk.state.weekly_realized_pnl -= amount
        self._persist_risk()
        fees = self.db.get_state("total_fees", 0.0) + amount
        self.db.set_state("total_fees", fees)
        if note:
            self.db.set_state("last_fee_note", note)

    def close_position(self, position_id: int, exit_price: float, reason: str) -> Position:
        from quantx.execution.costs import PaperCostModel

        self._hydrate_risk_from_db()
        p = self.db.get_position(position_id)
        if not p or p.status != PositionStatus.OPEN:
            raise ValueError("Position not found or already closed")

        costs = PaperCostModel(
            slippage_bps=float((self.settings.broker or {}).get("slippage_bps", 5))
            if isinstance(self.settings.broker, dict)
            else 5.0
        )
        slipped_exit = costs.apply_slippage(exit_price, p.side, is_entry=False)
        if p.trade_type == TradeType.FUTURES:
            style = "futures"
        elif p.trade_type == TradeType.OPTIONS:
            style = "options"
        elif p.trade_type.value == "INTRADAY":
            style = "intraday"
        else:
            style = "swing"
        mult = lot_multiplier(p.trade_type, p.symbol)
        fee = costs.estimate(
            p.entry_price, slipped_exit, p.quantity * mult, p.side, style  # type: ignore[arg-type]
        )
        exit_fee = fee.total * 0.5  # entry half already charged

        p.exit_price = slipped_exit
        p.current_price = slipped_exit
        p.closed_at = datetime.utcnow()
        p.exit_reason = reason
        p.status = PositionStatus.CLOSED
        if p.side == Side.BUY:
            gross = (slipped_exit - p.entry_price) * p.quantity * mult
        else:
            gross = (p.entry_price - slipped_exit) * p.quantity * mult
        p.pnl = gross - exit_fee
        notional = p.entry_price * p.quantity * mult
        p.pnl_pct = (p.pnl / notional * 100) if notional else 0
        self.db.update_position(p)

        self.risk.register_trade_result(p.pnl)
        self.risk.update_state(open_positions=len(self.db.list_positions("OPEN")))
        self._persist_risk()
        fees = self.db.get_state("total_fees", 0.0) + exit_fee
        self.db.set_state("total_fees", fees)

        entry = TradeJournalEntry(
            symbol=p.symbol,
            side=p.side,
            trade_type=p.trade_type,
            entry=p.entry_price,
            exit=slipped_exit,
            quantity=p.quantity,
            pnl=p.pnl,
            pnl_pct=p.pnl_pct,
            reason=p.reason,
            mistakes="" if p.pnl >= 0 else "Review entry timing / stop placement",
            market_condition=f"{reason} | exit fee ₹{exit_fee:.2f}",
            confidence=p.confidence,
            lessons="Follow risk rules; never remove stop." if p.pnl < 0 else "Plan worked — journal what confirmed.",
            opened_at=p.opened_at,
            closed_at=p.closed_at or datetime.utcnow(),
        )
        self.db.insert_journal(entry)
        return p

    def performance(self) -> dict:
        journal = self.db.list_journal(500)
        wins = [j for j in journal if j.pnl > 0]
        losses = [j for j in journal if j.pnl <= 0]
        total_pnl = sum(j.pnl for j in journal)
        win_rate = len(wins) / len(journal) * 100 if journal else 0.0
        avg_win = sum(j.pnl for j in wins) / len(wins) if wins else 0.0
        avg_loss = sum(j.pnl for j in losses) / len(losses) if losses else 0.0
        expectancy = (
            avg_win * (win_rate / 100) + avg_loss * (1 - win_rate / 100) if journal else 0.0
        )
        snap = self.snapshot()
        return {
            "trades": len(journal),
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 2),
            "total_realized_pnl": round(total_pnl, 2),
            "avg_win": round(avg_win, 2),
            "avg_loss": round(avg_loss, 2),
            "expectancy": round(expectancy, 2),
            "total_fees": round(self.db.get_state("total_fees", 0.0), 2),
            "capital": snap.capital,
            "drawdown_pct": snap.drawdown_pct,
            "open_positions": snap.open_positions,
        }

    def panic_exit_all(self) -> list[Position]:
        closed = []
        for p in self.db.list_positions("OPEN"):
            closed.append(self.close_position(p.id, p.current_price, "PANIC EXIT"))  # type: ignore
        return closed
