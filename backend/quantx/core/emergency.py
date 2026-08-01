"""Emergency controls — kill switch, panic exit, drawdown lock."""

from __future__ import annotations

from typing import Optional

from quantx.core.models import EmergencyState
from quantx.core.risk import RiskManager
from quantx.portfolio.db import Database
from quantx.portfolio.manager import PortfolioManager


class EmergencyController:
    def __init__(
        self,
        portfolio: Optional[PortfolioManager] = None,
        risk: Optional[RiskManager] = None,
        db: Optional[Database] = None,
    ):
        self.portfolio = portfolio or PortfolioManager()
        self.risk = risk or self.portfolio.risk
        self.db = db or self.portfolio.db

    def state(self) -> EmergencyState:
        self.portfolio._hydrate_risk_from_db()
        s = self.risk.state
        messages = []
        if s.kill_switch:
            messages.append("KILL SWITCH ACTIVE — all new trades blocked")
        if s.max_drawdown_lock:
            messages.append("MAX DRAWDOWN LOCK — capital protection engaged")
        if s.manual_override:
            messages.append("MANUAL OVERRIDE — risk gates bypassed (dangerous)")
        if s.trading_halted:
            messages.append(s.halt_reason or "Trading halted")
        return EmergencyState(
            kill_switch=s.kill_switch,
            panic_exit_requested=False,
            max_drawdown_lock=s.max_drawdown_lock,
            manual_override=s.manual_override,
            broker_connected=True,  # paper always connected
            internet_ok=True,
            messages=messages,
        )

    def kill_switch(self, active: bool, reason: str = "Operator kill switch") -> EmergencyState:
        if active:
            self.risk.activate_kill_switch(reason)
        else:
            self.risk.deactivate_kill_switch()
        self.db.set_state("kill_switch", self.risk.state.kill_switch)
        self.portfolio._persist_risk()
        return self.state()

    def panic_exit(self) -> dict:
        closed = self.portfolio.panic_exit_all()
        self.risk.activate_kill_switch("Panic exit — trading locked")
        self.db.set_state("kill_switch", True)
        self.portfolio._persist_risk()
        return {
            "closed": [p.model_dump(mode="json") for p in closed],
            "emergency": self.state().model_dump(),
        }

    def set_manual_override(self, enabled: bool) -> EmergencyState:
        self.risk.update_state(manual_override=enabled)
        self.db.set_state("manual_override", enabled)
        self.portfolio._persist_risk()
        return self.state()

    def clear_drawdown_lock(self) -> EmergencyState:
        """Only clears lock flag; does not reset capital. Requires conscious operator action."""
        self.risk.update_state(max_drawdown_lock=False)
        self.db.set_state("max_drawdown_lock", False)
        self.portfolio._persist_risk()
        return self.state()
