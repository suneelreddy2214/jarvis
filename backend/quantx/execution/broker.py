"""Paper broker execution with slippage, fees, duplicate protection."""

from __future__ import annotations

import hashlib
import logging
from typing import Optional

from quantx.core.config import Settings, get_settings
from quantx.core.market_hours import MarketClock
from quantx.core.models import OrderStatus, Side, TradeRecommendation, TradeType
from quantx.execution.costs import PaperCostModel
from quantx.portfolio.db import Database
from quantx.portfolio.manager import PortfolioManager

logger = logging.getLogger(__name__)


class PaperBroker:
    """
    Realistic paper execution for NSE equities.
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

    def _client_order_id(self, rec: TradeRecommendation) -> str:
        raw = (
            f"{rec.symbol}|{rec.side.value}|{round(rec.entry,2)}|"
            f"{round(rec.stop_loss,2)}|{rec.quantity}|{rec.generated_at.date()}"
        )
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def _style(self, trade_type: TradeType) -> str:
        return "intraday" if trade_type == TradeType.INTRADAY else "swing"

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

        # Quote + slippage fill
        try:
            quote = self.portfolio.data.get_quote(rec.symbol, rec.exchange)
            last = float(quote["price"])
        except Exception as e:
            return {
                "status": OrderStatus.REJECTED.value,
                "message": f"Quote unavailable: {e}",
                "position": None,
            }

        if rec.entry > 0:
            drift = abs(last - rec.entry) / rec.entry
            if drift > 0.03:
                return {
                    "status": OrderStatus.REJECTED.value,
                    "message": f"Price drift {drift*100:.1f}% vs recommendation — refresh analysis",
                    "position": None,
                }

        fill_price = self.costs.apply_slippage(last, rec.side, is_entry=True)

        # Recompute capital at risk with slipped entry (stop unchanged)
        stop_dist = abs(fill_price - rec.stop_loss)
        capital_at_risk = stop_dist * rec.quantity
        max_risk = self.portfolio.risk.state.capital * (
            self.settings.risk.max_risk_per_trade_pct / 100
        )
        if capital_at_risk > max_risk + 1:
            # shrink qty to fit risk
            qty = int(max_risk // stop_dist) if stop_dist > 0 else 0
            if qty <= 0:
                return {
                    "status": OrderStatus.REJECTED.value,
                    "message": "Slippage pushed risk over 1% — trade skipped",
                    "position": None,
                }
            rec.quantity = qty
            capital_at_risk = stop_dist * qty

        # Est. round-trip costs using stop as worst-case exit proxy for margin buffer
        style = self._style(rec.trade_type)
        est = self.costs.estimate(
            entry=fill_price,
            exit=rec.stop_loss,
            quantity=rec.quantity,
            side=rec.side,
            style=style,  # type: ignore[arg-type]
        )

        snap = self.portfolio.snapshot()
        required = capital_at_risk + fill_price * rec.quantity * 0.05 + est.total
        if required > snap.available_margin:
            return {
                "status": OrderStatus.REJECTED.value,
                "message": (
                    f"Insufficient margin: need ~₹{required:,.0f}, "
                    f"available ₹{snap.available_margin:,.0f}"
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
                # Preserve policy RR after adverse slip (stop fixed, target nudged)
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
            # Record only after successful open to avoid blocking retries on transient fails
            position = self.portfolio.open_from_recommendation(rec)
            entry_fee = est.total * 0.5
            if entry_fee > 0:
                self.portfolio.charge_fees(entry_fee, note=f"entry costs {rec.symbol}")
            self.db.record_order(
                coid, rec.symbol, rec.side.value, rec.quantity, fill_price,
                OrderStatus.FILLED.value,
                f"filled pos#{position.id} slip={self.costs.slippage_bps}bps fee≈{entry_fee:.2f}",
            )
            return {
                "status": OrderStatus.FILLED.value,
                "message": (
                    f"Paper fill @ ₹{fill_price:.2f} "
                    f"(slip {self.costs.slippage_bps} bps, entry costs ₹{entry_fee:.2f})"
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
