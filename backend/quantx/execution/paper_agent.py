"""
Paper trading session agent — scan, size, execute, mark-to-market.

Runs autonomously under risk gates. Never revenge trades.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

from quantx.core.config import Settings, get_settings
from quantx.core.engine import QuantXEngine
from quantx.core.models import TradeRecommendation, TradeType
from quantx.data.market_data import DEFAULT_WATCHLIST
from quantx.execution.broker import PaperBroker
from quantx.portfolio.manager import PortfolioManager

logger = logging.getLogger(__name__)


DEFAULT_PAPER_UNIVERSE = [
    s.replace(".NS", "") for s in DEFAULT_WATCHLIST if s.endswith(".NS")
]


@dataclass
class SessionStats:
    started_at: Optional[str] = None
    stopped_at: Optional[str] = None
    cycles: int = 0
    scanned: int = 0
    valid_signals: int = 0
    executed: int = 0
    rejected: int = 0
    closed_by_mtm: int = 0
    last_cycle_at: Optional[str] = None
    last_message: str = "idle"
    running: bool = False
    auto_execute: bool = True
    symbols: list[str] = field(default_factory=lambda: list(DEFAULT_PAPER_UNIVERSE))
    interval_sec: int = 60
    trade_type: str = "SWING"


class PaperTradingAgent:
    """
    Autonomous paper trading loop.

    Each cycle:
      1. Risk gate check
      2. Mark open positions to market / apply exits
      3. Scan universe for valid setups
      4. Execute top valid signals (if auto_execute) without exceeding max positions
      5. Persist stats
    """

    def __init__(
        self,
        portfolio: Optional[PortfolioManager] = None,
        engine: Optional[QuantXEngine] = None,
        broker: Optional[PaperBroker] = None,
        settings: Optional[Settings] = None,
    ):
        self.settings = settings or get_settings()
        self.portfolio = portfolio or PortfolioManager(settings=self.settings)
        self.engine = engine or QuantXEngine(
            settings=self.settings,
            risk_manager=self.portfolio.risk,
            market_data=self.portfolio.data,
        )
        self.broker = broker or PaperBroker(
            portfolio=self.portfolio, db=self.portfolio.db, settings=self.settings
        )
        self.stats = SessionStats()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._on_cycle: Optional[Callable[[dict], None]] = None

    def status(self) -> dict:
        snap = self.portfolio.snapshot()
        risk = self.portfolio.risk.status()
        return {
            "agent": "QuantX Paper",
            "mode": self.settings.agent.mode,
            "session": {
                "running": self.stats.running,
                "auto_execute": self.stats.auto_execute,
                "symbols": self.stats.symbols,
                "interval_sec": self.stats.interval_sec,
                "trade_type": self.stats.trade_type,
                "started_at": self.stats.started_at,
                "stopped_at": self.stats.stopped_at,
                "cycles": self.stats.cycles,
                "scanned": self.stats.scanned,
                "valid_signals": self.stats.valid_signals,
                "executed": self.stats.executed,
                "rejected": self.stats.rejected,
                "closed_by_mtm": self.stats.closed_by_mtm,
                "last_cycle_at": self.stats.last_cycle_at,
                "last_message": self.stats.last_message,
            },
            "portfolio": snap.model_dump(),
            "risk": risk.model_dump(),
        }

    def start(
        self,
        symbols: Optional[list[str]] = None,
        interval_sec: int = 60,
        auto_execute: bool = True,
        trade_type: TradeType = TradeType.SWING,
    ) -> dict:
        with self._lock:
            if self.stats.running:
                return {"ok": False, "message": "Paper session already running", **self.status()}
            self.stats = SessionStats(
                started_at=datetime.utcnow().isoformat() + "Z",
                running=True,
                auto_execute=auto_execute,
                symbols=symbols or list(DEFAULT_PAPER_UNIVERSE),
                interval_sec=max(15, int(interval_sec)),
                trade_type=trade_type.value if isinstance(trade_type, TradeType) else str(trade_type),
            )
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="quantx-paper-session", daemon=True)
            self._thread.start()
            self.portfolio.db.set_state("paper_session_running", True)
            return {"ok": True, "message": "Paper trading session started", **self.status()}

    def stop(self) -> dict:
        with self._lock:
            self._stop.set()
            self.stats.running = False
            self.stats.stopped_at = datetime.utcnow().isoformat() + "Z"
            self.stats.last_message = "stopped by operator"
            self.portfolio.db.set_state("paper_session_running", False)
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        return {"ok": True, "message": "Paper trading session stopped", **self.status()}

    def run_once(self) -> dict:
        """Single scan/execute/MTM cycle — useful for CLI and tests."""
        return self._cycle()

    def reset_account(self, confirm: bool = False) -> dict:
        """Reset paper capital and clear open positions / journal (destructive)."""
        if not confirm:
            return {"ok": False, "message": "Pass confirm=true to reset paper account"}
        if self.stats.running:
            self.stop()
        # Flat reset of paper books
        self.portfolio.db.clear_orders()
        self.portfolio.db.clear_journal()
        initial = self.settings.capital.initial
        self.portfolio.risk.update_state(
            capital=initial,
            peak_capital=initial,
            daily_realized_pnl=0.0,
            weekly_realized_pnl=0.0,
            consecutive_losses=0,
            kill_switch=False,
            max_drawdown_lock=False,
            manual_override=False,
            open_positions=0,
            open_risk_amount=0.0,
            trading_halted=False,
            halt_reason=None,
        )
        self.portfolio._persist_risk()
        self.portfolio.db.set_state("total_fees", 0.0)
        self.portfolio.db.set_state("paper_reset_at", datetime.utcnow().isoformat() + "Z")
        self.stats = SessionStats()
        return {"ok": True, "message": f"Paper account reset to ₹{initial:,.0f}", **self.status()}

    def _loop(self) -> None:
        logger.info(
            "Paper session started | symbols=%s interval=%ss auto=%s",
            self.stats.symbols,
            self.stats.interval_sec,
            self.stats.auto_execute,
        )
        while not self._stop.is_set():
            try:
                self._cycle()
            except Exception as e:
                logger.exception("Paper cycle failed")
                self.stats.last_message = f"cycle error: {e}"
            self._stop.wait(self.stats.interval_sec)
        self.stats.running = False
        logger.info("Paper session loop exited")

    def _cycle(self) -> dict:
        self.stats.cycles += 1
        self.stats.last_cycle_at = datetime.utcnow().isoformat() + "Z"

        # 1) MTM / exits first — capital protection
        before = {p.id for p in self.portfolio.db.list_positions("OPEN")}
        opens = self.portfolio.mark_to_market()
        after = {p.id for p in opens}
        closed = len(before - after)
        self.stats.closed_by_mtm += closed

        risk = self.portfolio.risk.status()
        if not risk.can_trade:
            msg = f"Risk halt — skipping new entries ({'; '.join(risk.reasons) or 'halted'})"
            self.stats.last_message = msg
            result = {"mtm_closed": closed, "executed": [], "rejected": [], "signals": [], "message": msg}
            if self._on_cycle:
                self._on_cycle(result)
            return result

        # 2) Scan
        trade_type = TradeType(self.stats.trade_type)
        recs = self.engine.scan_watchlist(self.stats.symbols, "NSE", trade_type)
        self.stats.scanned += len(recs)
        valid = [r for r in recs if r.valid]
        self.stats.valid_signals += len(valid)

        executed: list[dict] = []
        rejected: list[dict] = []

        # 3) Auto-execute top valid (one per cycle per free slot)
        if self.stats.auto_execute:
            max_pos = self.settings.risk.max_open_positions
            open_syms = {p.symbol for p in self.portfolio.db.list_positions("OPEN")}
            slots = max_pos - len(open_syms)
            for rec in valid:
                if slots <= 0:
                    break
                if rec.symbol in open_syms:
                    rejected.append({"symbol": rec.symbol, "reason": "Already in position — no averaging"})
                    self.stats.rejected += 1
                    continue
                fill = self.broker.retry_safe(rec)
                if fill.get("status") == "FILLED":
                    executed.append(fill)
                    open_syms.add(rec.symbol)
                    slots -= 1
                    self.stats.executed += 1
                else:
                    rejected.append({"symbol": rec.symbol, "reason": fill.get("message", "rejected")})
                    self.stats.rejected += 1

        msg = (
            f"Cycle #{self.stats.cycles}: MTM closed {closed}, "
            f"valid {len(valid)}/{len(recs)}, executed {len(executed)}, rejected {len(rejected)}"
        )
        self.stats.last_message = msg
        logger.info(msg)

        result = {
            "mtm_closed": closed,
            "signals": [r.model_dump(mode="json") for r in recs],
            "valid_count": len(valid),
            "executed": executed,
            "rejected": rejected,
            "message": msg,
            "portfolio": self.portfolio.snapshot().model_dump(),
        }
        if self._on_cycle:
            self._on_cycle(result)
        return result


# Process-wide singleton used by API
_paper_agent: Optional[PaperTradingAgent] = None


def get_paper_agent(
    portfolio: Optional[PortfolioManager] = None,
    engine: Optional[QuantXEngine] = None,
    broker: Optional[PaperBroker] = None,
    settings: Optional[Settings] = None,
) -> PaperTradingAgent:
    global _paper_agent
    if _paper_agent is None:
        _paper_agent = PaperTradingAgent(
            portfolio=portfolio, engine=engine, broker=broker, settings=settings
        )
    return _paper_agent
