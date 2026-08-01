"""
Zerodha Kite Connect adapter (live).

Does NOT place orders unless:
  - agent mode is live
  - QUANTX_BROKER_API_KEY / SECRET / ACCESS_TOKEN are set
  - kiteconnect package is installed

Paper mode never imports or calls this for execution.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

from quantx.core.models import OrderStatus, Side, TradeRecommendation
from quantx.execution.base import BrokerAdapter, broker_credentials_present

logger = logging.getLogger(__name__)


class KiteBrokerAdapter(BrokerAdapter):
    name = "zerodha_kite"

    def __init__(self):
        self.api_key = os.getenv("QUANTX_BROKER_API_KEY", "")
        self.api_secret = os.getenv("QUANTX_BROKER_API_SECRET", "")
        self.access_token = os.getenv("QUANTX_BROKER_ACCESS_TOKEN", "")
        self._kite = None
        self._init_error: Optional[str] = None
        self._connect()

    def _connect(self) -> None:
        try:
            from kiteconnect import KiteConnect  # type: ignore

            kite = KiteConnect(api_key=self.api_key)
            kite.set_access_token(self.access_token)
            self._kite = kite
            # Lightweight profile ping
            kite.profile()
            logger.info("Kite Connect session established")
        except ImportError:
            self._init_error = (
                "kiteconnect package not installed. pip install kiteconnect"
            )
            logger.warning(self._init_error)
        except Exception as e:
            self._init_error = f"Kite auth/connect failed: {e}"
            logger.error(self._init_error)
            self._kite = None

    def status(self) -> dict[str, Any]:
        creds = broker_credentials_present()
        return {
            "name": self.name,
            "connected": self._kite is not None,
            "mode": "live",
            "credentials": creds,
            "message": self._init_error
            or ("Kite session active" if self._kite else "Not connected"),
        }

    def execute(self, rec: TradeRecommendation) -> dict[str, Any]:
        if self._kite is None:
            return {
                "status": OrderStatus.REJECTED.value,
                "message": self._init_error or "Kite not connected",
                "position": None,
            }
        if not rec.valid or rec.quantity <= 0:
            return {
                "status": OrderStatus.REJECTED.value,
                "message": rec.rejection_reason or "Invalid recommendation",
                "position": None,
            }

        transaction_type = "BUY" if rec.side == Side.BUY else "SELL"
        try:
            order_id = self._kite.place_order(
                variety="regular",
                exchange=rec.exchange,
                tradingsymbol=rec.symbol,
                transaction_type=transaction_type,
                quantity=rec.quantity,
                order_type="LIMIT",
                price=rec.entry,
                product="CNC" if rec.trade_type.value != "INTRADAY" else "MIS",
                validity="DAY",
                tag="QuantX",
            )
            # Place exchange stop-loss as SL order (protective)
            try:
                self._kite.place_order(
                    variety="regular",
                    exchange=rec.exchange,
                    tradingsymbol=rec.symbol,
                    transaction_type="SELL" if rec.side == Side.BUY else "BUY",
                    quantity=rec.quantity,
                    order_type="SL",
                    price=rec.stop_loss,
                    trigger_price=rec.stop_loss,
                    product="CNC" if rec.trade_type.value != "INTRADAY" else "MIS",
                    validity="DAY",
                    tag="QuantXSL",
                )
            except Exception as sl_err:
                logger.error("Entry placed but SL failed: %s", sl_err)

            return {
                "status": OrderStatus.SUBMITTED.value,
                "message": f"Kite order submitted id={order_id}",
                "order_id": order_id,
                "position": None,
            }
        except Exception as e:
            logger.exception("Kite place_order failed")
            return {
                "status": OrderStatus.REJECTED.value,
                "message": str(e),
                "position": None,
            }

    def cancel_all(self) -> dict[str, Any]:
        if self._kite is None:
            return {"status": "error", "message": self._init_error or "Not connected"}
        try:
            orders = self._kite.orders()
            cancelled = []
            for o in orders:
                if o.get("status") in ("OPEN", "TRIGGER PENDING"):
                    self._kite.cancel_order(variety=o.get("variety", "regular"), order_id=o["order_id"])
                    cancelled.append(o["order_id"])
            return {"status": "ok", "cancelled": cancelled}
        except Exception as e:
            return {"status": "error", "message": str(e)}
