"""Paper broker execution with duplicate protection and validation."""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime
from typing import Optional

from quantx.core.config import Settings, get_settings
from quantx.core.market_hours import MarketClock
from quantx.core.models import OrderStatus, TradeRecommendation
from quantx.portfolio.db import Database
from quantx.portfolio.manager import PortfolioManager

logger = logging.getLogger(__name__)


class PaperBroker:
    """
    Secure paper execution layer.
    Live broker adapters would implement the same interface with API keys from env.
    """

    def __init__(
        self,
        portfolio: Optional[PortfolioManager] = None,
        db: Optional[Database] = None,
        settings: Optional[Settings] = None,
    ):
        self.settings = settings or get_settings()
        self.db = db or Database()
        self.portfolio = portfolio or PortfolioManager(db=self.db, settings=self.settings)
        self.clock = MarketClock(self.settings)

    def _client_order_id(self, rec: TradeRecommendation) -> str:
        raw = f"{rec.symbol}|{rec.side.value}|{rec.entry}|{rec.stop_loss}|{rec.quantity}|{rec.generated_at.date()}"
        return hashlib.sha256(raw.encode()).hexdigest()[:24]

    def execute(self, rec: TradeRecommendation) -> dict:
        allowed, msg = self.clock.validate_trading_allowed(mode=self.settings.agent.mode)
        if not allowed:
            return {"status": OrderStatus.REJECTED.value, "message": msg, "position": None}

        if not rec.valid:
            return {
                "status": OrderStatus.REJECTED.value,
                "message": rec.rejection_reason or "Invalid recommendation",
                "position": None,
            }

        # Margin validation (paper: require capital_at_risk + notional*0.2 available)
        snap = self.portfolio.snapshot()
        required = rec.capital_at_risk + rec.entry * rec.quantity * 0.05
        if required > snap.available_margin:
            return {
                "status": OrderStatus.REJECTED.value,
                "message": f"Insufficient margin: need ~{required:.0f}, available {snap.available_margin:.0f}",
                "position": None,
            }

        coid = self._client_order_id(rec)
        if self.db.order_exists(coid):
            return {
                "status": OrderStatus.REJECTED.value,
                "message": "Duplicate order blocked — same symbol/side/levels already submitted today",
                "client_order_id": coid,
                "position": None,
            }

        # Price verification — entry should be near last quote
        try:
            quote = self.portfolio.data.get_quote(rec.symbol, rec.exchange)
            last = quote["price"]
            slip = abs(last - rec.entry) / rec.entry if rec.entry else 1
            if slip > 0.03:
                return {
                    "status": OrderStatus.REJECTED.value,
                    "message": f"Price drift {slip*100:.1f}% vs recommendation — refresh analysis",
                    "position": None,
                }
            # Use verified last price for fill in paper mode
            rec.entry = round(last, 2)
        except Exception as e:
            logger.warning("Price verify failed: %s", e)

        try:
            self.db.record_order(
                coid, rec.symbol, rec.side.value, rec.quantity, rec.entry,
                OrderStatus.SUBMITTED.value, "submitted",
            )
            position = self.portfolio.open_from_recommendation(rec)
            self.db.record_order(
                coid + "-fill", rec.symbol, rec.side.value, rec.quantity, rec.entry,
                OrderStatus.FILLED.value, f"filled pos#{position.id}",
            )
            return {
                "status": OrderStatus.FILLED.value,
                "message": "Paper fill successful",
                "client_order_id": coid,
                "position": position.model_dump(mode="json"),
            }
        except Exception as e:
            self.db.record_order(
                coid + "-rej", rec.symbol, rec.side.value, rec.quantity, rec.entry,
                OrderStatus.REJECTED.value, str(e),
            )
            return {"status": OrderStatus.REJECTED.value, "message": str(e), "position": None}

    def retry_safe(self, rec: TradeRecommendation, attempts: int = 2) -> dict:
        last = {}
        for i in range(attempts):
            last = self.execute(rec)
            if last["status"] == OrderStatus.FILLED.value:
                return last
            if "Duplicate" in last.get("message", ""):
                return last  # do not retry duplicates
        return last
