"""Broker adapter interface — paper today, live when credentials exist."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from quantx.core.models import TradeRecommendation


class BrokerAdapter(ABC):
    name: str

    @abstractmethod
    def status(self) -> dict[str, Any]:
        ...

    @abstractmethod
    def execute(self, rec: TradeRecommendation) -> dict[str, Any]:
        ...

    @abstractmethod
    def cancel_all(self) -> dict[str, Any]:
        ...


def broker_credentials_present() -> dict[str, bool]:
    import os

    return {
        "api_key": bool(os.getenv("QUANTX_BROKER_API_KEY")),
        "api_secret": bool(os.getenv("QUANTX_BROKER_API_SECRET")),
        "access_token": bool(os.getenv("QUANTX_BROKER_ACCESS_TOKEN")),
    }


def create_broker(mode: str = "paper", **kwargs) -> BrokerAdapter:
    """Factory: paper by default; kite only if mode=live and env credentials exist."""
    mode = (mode or "paper").lower()
    if mode == "paper":
        from quantx.execution.broker import PaperBroker

        return PaperBrokerAdapter(PaperBroker(**kwargs))

    if mode == "live":
        creds = broker_credentials_present()
        if not all(creds.values()):
            raise RuntimeError(
                "Live mode requires QUANTX_BROKER_API_KEY, QUANTX_BROKER_API_SECRET, "
                "QUANTX_BROKER_ACCESS_TOKEN. Falling back refused — stay in paper."
            )
        from quantx.execution.kite_broker import KiteBrokerAdapter

        return KiteBrokerAdapter()

    raise ValueError(f"Unknown broker mode: {mode}")


class PaperBrokerAdapter(BrokerAdapter):
    name = "paper"

    def __init__(self, paper_broker):
        self._broker = paper_broker

    def status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "connected": True,
            "mode": "paper",
            "message": "Simulated fills with slippage + NSE fee model",
            "credentials": broker_credentials_present(),
        }

    def execute(self, rec: TradeRecommendation) -> dict[str, Any]:
        return self._broker.retry_safe(rec)

    def cancel_all(self) -> dict[str, Any]:
        return {"status": "ok", "message": "No resting paper orders"}


# Ensure PaperBroker satisfies conceptual interface via adapter
