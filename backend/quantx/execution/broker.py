"""Paper broker execution with slippage, fees, duplicate protection."""

from __future__ import annotations

import hashlib
from typing import Optional

from quantx.analysis.fno import mark_option_premium, parse_fo_meta
from quantx.core.config import Settings, get_settings
from quantx.core.market_hours import MarketClock
from quantx.core.models import OrderStatus, Position, Side, TradeRecommendation, TradeType
from quantx.execution.costs import PaperCostModel
from quantx.portfolio.db import Database
from quantx.portfolio.manager import PortfolioManager
from quantx.portfolio.margin import MarginCalculator, lot_multiplier


class PaperBroker:
    """
    Realistic paper execution for NSE equities and F&O.
    Applies adverse slippage + round-trip cost estimate on open (reserved) / close.
    """

    def __init__(
        self,
        portfolio: Optional[PortfolioManager] = None,
        db: Optional[Database] = None,
        settings: Optional[Settings] = None,
        cost_model: Optional[PaperCostModel] = None,
    ):
        self.settings = settings or get_settings()
        self.db = db or Database()
        self.portfolio = portfolio or PortfolioManager(db=self.db, settings=self.settings)
        self.clock = MarketClock(self.settings)
        broker_cfg = getattr(self.settings, "broker", {}) or {}
        if isinstance(broker_cfg, dict):
            slip = float(broker_cfg.get("slippage_bps", 5))
        else:
            slip = 5.0
        self.costs = cost_model or PaperCostModel(slippage_bps=slip)
        self.margin = MarginCalculator()

    def _client_order_id(self, rec: TradeRecommendation) -> str:
        raw = (
            f"{rec.symbol}|{rec.trade_type.value}|{rec.side.value}|{round(rec.entry,2)}|"
            f"{round(rec.stop_loss,2)}|{rec.quantity}|{rec.generated_at.date()}"
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def _style(self, trade_type: TradeType) -> str:
        if trade_type == TradeType.INTRADAY:
            return "intraday"
        if trade_type == TradeType.FUTURES:
            return "futures"
        if trade_type == TradeType.OPTIONS:
            return "options"
        return "swing"

    def _fill_price(self, rec: TradeRecommendation) -> tuple[float | None, str | None]:
        """Return (fill_price, error_message)."""
        if rec.trade_type == TradeType.OPTIONS:
            # Options: fill near recommended premium (spot quote is underlying)
            try:
                quote = self.portfolio.data.get_quote(rec.symbol, rec.exchange)
                meta = parse_fo_meta(rec.reason)
                if meta:
                    last = mark_option_premium(
                        rec.entry, meta["underlying_entry"], float(quote["price"]), meta["kind"]
                    )
                else:
                    last = rec.entry
            except Exception:
                last = rec.entry
            return self.costs.apply_slippage(last, rec.side, is_entry=True), None

        try:
            quote = self.portfolio.data.get_quote(rec.symbol, rec.exchange)
            last = float(quote["price"])
        except Exception as e:
            return None, f"Quote unavailable: {e}"

        if rec.entry > 0:
            drift = abs(last - rec.entry) / rec.entry
            # Futures can drift slightly vs spot-based recommendation
            limit = 0.04 if rec.trade_type == TradeType.FUTURES else 0.03
            if drift > limit:
                return None, f"Price drift {drift*100:.1f}% vs recommendation — refresh analysis"
        return self.costs.apply_slippage(last, rec.side, is_entry=True), None

    def execute(self, rec: TradeRecommendation) -> dict:
        # Hard lock: paper mode only in this broker
        if self.settings.agent.mode != "paper":
            return {
                "status": OrderStatus.REJECTED.value,
                "message": "PaperBroker refuses non-paper mode — use live adapter",
                "position": None,
            }

        allowed, msg = self.clock.validate_trading_allowed(mode="paper")
        if not allowed:
            return {"status": OrderStatus.REJECTED.value, "message": msg, "position": None}

        if not rec.valid or rec.quantity <= 0:
            return {
                "status": OrderStatus.REJECTED.value,
                "message": rec.rejection_reason or "Invalid recommendation",
                "position": None,
            }

        # Risk gate
        self.portfolio._hydrate_risk_from_db()
        risk = self.portfolio.risk.status()
        if not risk.can_trade:
            return {
                "status": OrderStatus.REJECTED.value,
                "message": "; ".join(risk.reasons) or "Trading halted",
                "position": None,
            }

        fill_price, err = self._fill_price(rec)
        if err or fill_price is None:
            return {
                "status": OrderStatus.REJECTED.value,
                "message": err or "Fill unavailable",
                "position": None,
            }

        mult = lot_multiplier(rec.trade_type, rec.symbol)
        stop_dist = abs(fill_price - rec.stop_loss)
        capital_at_risk = stop_dist * rec.quantity * mult
        max_risk = self.portfolio.risk.state.capital * (
            self.settings.risk.max_risk_per_trade_pct / 100
        )
        if capital_at_risk > max_risk + 1:
            risk_per_lot = stop_dist * mult
            qty = int(max_risk // risk_per_lot) if risk_per_lot > 0 else 0
            if qty <= 0:
                return {
                    "status": OrderStatus.REJECTED.value,
                    "message": "Slippage pushed risk over 1% — trade skipped",
                    "position": None,
                }
            rec.quantity = qty
            capital_at_risk = stop_dist * qty * mult

        style = self._style(rec.trade_type)
        est = self.costs.estimate(
            entry=fill_price,
            exit=rec.stop_loss,
            quantity=rec.quantity * mult,
            side=rec.side,
            style=style,  # type: ignore[arg-type]
        )

        # Margin check via MarginCalculator
        temp = Position(
            symbol=rec.symbol,
            exchange=rec.exchange,
            side=rec.side,
            trade_type=rec.trade_type,
            quantity=rec.quantity,
            entry_price=fill_price,
            current_price=fill_price,
            stop_loss=rec.stop_loss,
            target_1=rec.target_1,
            target_2=rec.target_2,
            capital_at_risk=capital_at_risk,
        )
        pm = self.margin.margin_for_position(temp)
        snap = self.portfolio.snapshot()
        required = pm.margin_required + est.total * 0.5
        if required > snap.available_margin:
            return {
                "status": OrderStatus.REJECTED.value,
                "message": (
                    f"Insufficient margin: need ~₹{required:,.0f} "
                    f"({rec.trade_type.value}), available ₹{snap.available_margin:,.0f}"
                ),
                "position": None,
            }

        # Update recommendation with realistic fill
        rec.entry = fill_price
        rec.capital_at_risk = round(capital_at_risk, 2)
        min_rr = self.settings.risk.min_risk_reward
        if rec.side == Side.BUY:
            risk_amt = fill_price - rec.stop_loss
            if risk_amt > 0:
                if (rec.target_1 - fill_price) / risk_amt < min_rr:
                    rec.target_1 = round(fill_price + min_rr * risk_amt, 2)
                    rec.target_2 = round(fill_price + (min_rr + 1) * risk_amt, 2)
                rec.risk_reward = round((rec.target_1 - fill_price) / risk_amt, 2)
        else:
            risk_amt = rec.stop_loss - fill_price
            if risk_amt > 0:
                if (fill_price - rec.target_1) / risk_amt < min_rr:
                    rec.target_1 = round(fill_price - min_rr * risk_amt, 2)
                    rec.target_2 = round(fill_price - (min_rr + 1) * risk_amt, 2)
                rec.risk_reward = round((fill_price - rec.target_1) / risk_amt, 2)

        coid = self._client_order_id(rec)
        if self.db.order_exists(coid):
            return {
                "status": OrderStatus.REJECTED.value,
                "message": "Duplicate order blocked — same symbol/side/levels already submitted today",
                "client_order_id": coid,
                "position": None,
            }

        try:
            position = self.portfolio.open_from_recommendation(rec)
            entry_fee = est.total * 0.5
            if entry_fee > 0:
                self.portfolio.charge_fees(entry_fee, note=f"entry costs {rec.symbol} {rec.trade_type.value}")
            self.db.record_order(
                coid, rec.symbol, rec.side.value, rec.quantity, fill_price,
                OrderStatus.FILLED.value,
                f"filled pos#{position.id} {rec.trade_type.value} slip={self.costs.slippage_bps}bps fee≈{entry_fee:.2f}",
            )
            return {
                "status": OrderStatus.FILLED.value,
                "message": (
                    f"Paper {rec.trade_type.value} fill @ ₹{fill_price:.2f} "
                    f"x{rec.quantity} lot(s) (slip {self.costs.slippage_bps} bps, entry costs ₹{entry_fee:.2f})"
                ),
                "client_order_id": coid,
                "fill_price": fill_price,
                "costs": est.as_dict(),
                "position": position.model_dump(mode="json"),
            }
        except Exception as e:
            self.db.record_order(
                coid + "-rej", rec.symbol, rec.side.value, rec.quantity, fill_price,
                OrderStatus.REJECTED.value, str(e),
            )
            return {"status": OrderStatus.REJECTED.value, "message": str(e), "position": None}

    def retry_safe(self, rec: TradeRecommendation, attempts: int = 2) -> dict:
        last: dict = {}
        for _ in range(attempts):
            last = self.execute(rec)
            if last.get("status") == OrderStatus.FILLED.value:
                return last
            if "Duplicate" in last.get("message", ""):
                return last
            if "halt" in last.get("message", "").lower() or "Insufficient" in last.get("message", ""):
                return last
        return last
