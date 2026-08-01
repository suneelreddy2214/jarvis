"""
Paper trading session agent — scan, size, execute, mark-to-market.

Runs autonomously under risk gates. Never revenge trades.
Supports Stocks (equity) and F&O (futures + long options) in the same session.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Optional

import quantx.analysis.additional_strategies  # noqa: F401
import quantx.analysis.trading_styles  # noqa: F401
from quantx.analysis.fno import DEFAULT_FO_UNIVERSE, is_index
from quantx.analysis.learning import StrategyLearner
from quantx.analysis.regime import RegimeDetector
from quantx.analysis.selector import select_strategies_for_regime
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
    skipped: int = 0
    closed_by_mtm: int = 0
    last_cycle_at: Optional[str] = None
    last_message: str = "idle"
    running: bool = False
    auto_execute: bool = True
    symbols: list[str] = field(default_factory=lambda: list(DEFAULT_PAPER_UNIVERSE))
    fo_symbols: list[str] = field(default_factory=lambda: list(DEFAULT_FO_UNIVERSE))
    interval_sec: int = 60
    trade_type: str = "SWING"
    trade_types: list[str] = field(default_factory=lambda: ["SWING"])
    enable_fno: bool = False
    use_regime_selector: bool = True
    last_regime: Optional[str] = None
    last_strategies: list[str] = field(default_factory=list)


class PaperTradingAgent:
    """
    Autonomous paper trading loop.

    Each cycle:
      1. Risk gate check
      2. Mark open positions to market / apply exits
      3. Scan Stocks + optional F&O universes
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
                "fo_symbols": self.stats.fo_symbols,
                "interval_sec": self.stats.interval_sec,
                "trade_type": self.stats.trade_type,
                "trade_types": self.stats.trade_types,
                "enable_fno": self.stats.enable_fno,
                "use_regime_selector": self.stats.use_regime_selector,
                "last_regime": self.stats.last_regime,
                "last_strategies": self.stats.last_strategies,
                "started_at": self.stats.started_at,
                "stopped_at": self.stats.stopped_at,
                "cycles": self.stats.cycles,
                "scanned": self.stats.scanned,
                "valid_signals": self.stats.valid_signals,
                "executed": self.stats.executed,
                "rejected": self.stats.rejected,
                "skipped": self.stats.skipped,
                "closed_by_mtm": self.stats.closed_by_mtm,
                "last_cycle_at": self.stats.last_cycle_at,
                "last_message": self.stats.last_message,
            },
            "portfolio": snap.model_dump(),
            "risk": risk.model_dump(),
            "margin": self.portfolio.margin_book(),
        }

    def start(
        self,
        symbols: Optional[list[str]] = None,
        interval_sec: int = 60,
        auto_execute: bool = True,
        trade_type: TradeType = TradeType.SWING,
        enable_fno: bool = False,
        trade_types: Optional[list[TradeType | str]] = None,
        fo_symbols: Optional[list[str]] = None,
    ) -> dict:
        with self._lock:
            if self.stats.running:
                return {"ok": False, "message": "Paper session already running", **self.status()}

            types: list[str]
            if trade_types:
                types = [
                    t.value if isinstance(t, TradeType) else str(t).upper() for t in trade_types
                ]
            elif enable_fno:
                base = trade_type.value if isinstance(trade_type, TradeType) else str(trade_type)
                types = []
                for t in [base, TradeType.FUTURES.value, TradeType.OPTIONS.value]:
                    if t not in types:
                        types.append(t)
            else:
                types = [
                    trade_type.value if isinstance(trade_type, TradeType) else str(trade_type)
                ]

            self.stats = SessionStats(
                started_at=datetime.utcnow().isoformat() + "Z",
                running=True,
                auto_execute=auto_execute,
                symbols=symbols or list(DEFAULT_PAPER_UNIVERSE),
                fo_symbols=fo_symbols or list(DEFAULT_FO_UNIVERSE),
                interval_sec=max(15, int(interval_sec)),
                trade_type=types[0],
                trade_types=types,
                enable_fno=enable_fno or any(
                    t in (TradeType.FUTURES.value, TradeType.OPTIONS.value) for t in types
                ),
            )
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="quantx-paper-session", daemon=True)
            self._thread.start()
            self.portfolio.db.set_state("paper_session_running", True)
            self.portfolio.db.set_state("paper_enable_fno", self.stats.enable_fno)
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
        self.portfolio.db.clear_orders()
        self.portfolio.db.clear_journal()
        self.portfolio.db.clear_cycles()
        # Close any open positions by wiping table via status update
        for p in self.portfolio.db.list_positions("OPEN"):
            try:
                self.portfolio.close_position(p.id, p.current_price, "ACCOUNT RESET")  # type: ignore[arg-type]
            except Exception:
                pass
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
        self.portfolio.db.set_state("capital_basis", initial)
        self.portfolio.db.set_state("paper_reset_at", datetime.utcnow().isoformat() + "Z")
        self.stats = SessionStats()
        return {"ok": True, "message": f"Paper account reset to ₹{initial:,.0f}", **self.status()}

    def _loop(self) -> None:
        logger.info(
            "Paper session started | symbols=%s fo=%s types=%s interval=%ss auto=%s",
            self.stats.symbols,
            self.stats.fo_symbols if self.stats.enable_fno else [],
            self.stats.trade_types,
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

    def _symbols_for(self, trade_type: str) -> list[str]:
        if trade_type in (TradeType.FUTURES.value, TradeType.OPTIONS.value):
            return list(self.stats.fo_symbols)
        return list(self.stats.symbols)

    def _strategy_for(self, trade_type: str) -> Optional[str]:
        if trade_type == TradeType.FUTURES.value:
            return "futures_trend"
        if trade_type == TradeType.OPTIONS.value:
            return "options_directional"
        if trade_type == TradeType.INTRADAY.value:
            return "intraday_mean_reversion"
        return None  # default TA path / swing

    def _detect_regime(self):
        macro_snap = self.portfolio.data.get_macro_snapshot()
        macro = self.engine.macro.analyze(
            nifty_change_pct=macro_snap.get("nifty_change_pct"),
            india_vix=macro_snap.get("india_vix"),
            usdinr_change_pct=macro_snap.get("usdinr_change_pct"),
        )
        # Use NIFTY snapshot for technical regime when possible
        snap = None
        try:
            df = self.portfolio.data.get_ohlc("NIFTY", "NSE")
            snap = self.engine.ta.analyze(df)
        except Exception:
            snap = None
        return RegimeDetector().detect(
            snap=snap,
            india_vix=macro_snap.get("india_vix"),
            avoid_new_risk=macro.avoid_new_risk,
        )

    def _strategies_for(self, trade_type: str, regime) -> list[str]:
        if not self.stats.use_regime_selector:
            s = self._strategy_for(trade_type)
            return [s] if s else ["swing_trend"]
        learner = StrategyLearner(self.portfolio.db)
        return select_strategies_for_regime(
            regime, trade_type, learner=learner, limit=3
        )

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
            result = {
                "cycle_no": self.stats.cycles,
                "mtm_closed": closed,
                "executed": [],
                "rejected": [],
                "signals": [],
                "scanned": 0,
                "valid_count": 0,
                "message": msg,
                "created_at": self.stats.last_cycle_at,
            }
            self._persist_cycle(result)
            if self._on_cycle:
                self._on_cycle(result)
            return result

        # 2) Scan all enabled products with regime-selected strategies
        types = self.stats.trade_types or [self.stats.trade_type]
        regime = self._detect_regime()
        self.stats.last_regime = regime.regime.value
        used_strategies: list[str] = []
        recs: list[TradeRecommendation] = []
        for tt in types:
            try:
                strat_names = self._strategies_for(tt, regime)
                used_strategies.extend(strat_names)
                for strat_name in strat_names:
                    batch = self.engine.scan_watchlist(
                        self._symbols_for(tt),
                        "NSE",
                        TradeType(tt),
                        strategy=strat_name,
                    )
                    recs.extend(batch)
            except Exception as e:
                logger.exception("Scan failed for trade_type=%s", tt)
                self.stats.last_message = f"scan error ({tt}): {e}"

        # Dedupe by symbol+trade_type keeping highest confidence
        best: dict[tuple[str, str], TradeRecommendation] = {}
        for r in recs:
            key = (r.symbol, r.trade_type.value)
            prev = best.get(key)
            if prev is None or (r.valid and not prev.valid) or (
                r.valid == prev.valid and r.scores.confidence > prev.scores.confidence
            ):
                best[key] = r
        recs = list(best.values())
        # Prefer index F&O and diversified SWING over stacking stock F&O
        def _exec_rank(r: TradeRecommendation) -> tuple:
            fo = r.trade_type in (TradeType.FUTURES, TradeType.OPTIONS)
            idx = is_index(r.symbol)
            # lower tuple sorts first: valid already filtered later
            return (
                0 if (fo and idx) else (1 if not fo else 2),
                -r.scores.confidence,
            )

        recs.sort(key=lambda r: (not r.valid, *_exec_rank(r)))
        self.stats.last_strategies = sorted(set(used_strategies))
        self.stats.scanned += len(recs)
        valid = [r for r in recs if r.valid]
        self.stats.valid_signals += len(valid)

        executed: list[dict] = []
        rejected: list[dict] = []
        skipped: list[dict] = []

        def _skip(rec: TradeRecommendation, reason: str) -> None:
            skipped.append(
                {
                    "symbol": rec.symbol,
                    "trade_type": rec.trade_type.value,
                    "reason": reason,
                    "status": "SKIPPED",
                }
            )
            self.stats.skipped += 1

        # 3) Auto-execute top valid (keyed by symbol+trade_type)
        if self.stats.auto_execute:
            max_pos = self.settings.risk.max_open_positions
            paper_cfg = getattr(self.settings, "paper", None) or {}
            if not isinstance(paper_cfg, dict):
                paper_cfg = {}
            max_fo = int(paper_cfg.get("max_fo_positions", 3))
            max_per_sym = int(paper_cfg.get("max_positions_per_symbol", 1))
            opens_now = self.portfolio.db.list_positions("OPEN")
            open_keys = {(p.symbol, p.trade_type.value) for p in opens_now}
            open_syms: dict[str, int] = {}
            for p in opens_now:
                open_syms[p.symbol] = open_syms.get(p.symbol, 0) + 1
            fo_open = sum(
                1
                for p in opens_now
                if p.trade_type in (TradeType.FUTURES, TradeType.OPTIONS)
            )
            slots = max_pos - len(opens_now)
            fo_skips_logged = False
            slot_skips_logged = False
            for rec in valid:
                key = (rec.symbol, rec.trade_type.value)
                is_fo = rec.trade_type in (TradeType.FUTURES, TradeType.OPTIONS)
                if slots <= 0:
                    if not slot_skips_logged:
                        _skip(rec, f"Max open positions ({max_pos}) — remaining signals skipped")
                        slot_skips_logged = True
                    continue
                if is_fo and fo_open >= max_fo:
                    if not fo_skips_logged:
                        _skip(rec, f"Max F&O positions ({max_fo}/{max_fo}) — remaining F&O skipped")
                        fo_skips_logged = True
                    continue
                if key in open_keys:
                    _skip(rec, "Already in position — no averaging")
                    continue
                if open_syms.get(rec.symbol, 0) >= max_per_sym:
                    _skip(
                        rec,
                        f"Max positions per symbol ({max_per_sym}) — diversify away from {rec.symbol}",
                    )
                    continue
                fill = self.broker.retry_safe(rec)
                if fill.get("status") == "FILLED":
                    executed.append(
                        {
                            "symbol": rec.symbol,
                            "trade_type": rec.trade_type.value,
                            "side": rec.side.value,
                            "quantity": rec.quantity,
                            "fill_price": fill.get("fill_price", rec.entry),
                            "status": "FILLED",
                            "message": fill.get("message", ""),
                        }
                    )
                    open_keys.add(key)
                    open_syms[rec.symbol] = open_syms.get(rec.symbol, 0) + 1
                    slots -= 1
                    if is_fo:
                        fo_open += 1
                    self.stats.executed += 1
                else:
                    rejected.append(
                        {
                            "symbol": rec.symbol,
                            "trade_type": rec.trade_type.value,
                            "reason": fill.get("message", "rejected"),
                            "status": "REJECTED",
                        }
                    )
                    self.stats.rejected += 1

        msg = (
            f"Cycle #{self.stats.cycles}: regime={regime.regime.value}, "
            f"strats={','.join(self.stats.last_strategies[:5])}, "
            f"MTM closed {closed}, valid {len(valid)}/{len(recs)}, "
            f"executed {len(executed)}, rejected {len(rejected)}, skipped {len(skipped)}"
        )
        self.stats.last_message = msg
        logger.info(msg)

        result = {
            "cycle_no": self.stats.cycles,
            "mtm_closed": closed,
            "signals": [r.model_dump(mode="json") for r in recs],
            "scanned": len(recs),
            "valid_count": len(valid),
            "executed": executed,
            "rejected": rejected,
            "skipped": skipped,
            "executed_count": len(executed),
            "rejected_count": len(rejected),
            "skipped_count": len(skipped),
            "trade_types": types,
            "enable_fno": self.stats.enable_fno,
            "regime": regime.as_dict(),
            "strategies_used": self.stats.last_strategies,
            "message": msg,
            "portfolio": self.portfolio.snapshot().model_dump(),
            "margin": self.portfolio.margin_book(),
            "created_at": self.stats.last_cycle_at,
        }
        self._persist_cycle(result)
        if self._on_cycle:
            self._on_cycle(result)
        return result

    def _persist_cycle(self, result: dict) -> None:
        try:
            # Store skipped alongside rejected (tagged) so UI can separate them
            ledger = list(result.get("rejected") or []) + list(result.get("skipped") or [])
            self.portfolio.db.insert_cycle(
                {
                    "cycle_no": result.get("cycle_no", self.stats.cycles),
                    "message": result.get("message", ""),
                    "mtm_closed": result.get("mtm_closed", 0),
                    "scanned": result.get("scanned", 0),
                    "valid_count": result.get("valid_count", 0),
                    "executed_count": result.get("executed_count", len(result.get("executed") or [])),
                    "rejected_count": result.get("rejected_count", len(result.get("rejected") or [])),
                    "executed": result.get("executed") or [],
                    "rejected": ledger,
                    "created_at": result.get("created_at"),
                }
            )
        except Exception:
            logger.exception("Failed to persist cycle log")

    def list_cycles(self, limit: int = 50) -> list[dict]:
        rows = self.portfolio.db.list_cycles(limit)
        for row in rows:
            ledger = row.get("rejected") or []
            rejected = [x for x in ledger if (x.get("status") or "REJECTED") == "REJECTED"]
            skipped = [x for x in ledger if x.get("status") == "SKIPPED"]
            # Back-compat: old rows without status were capacity "rejections"
            if not skipped and rejected:
                soft = []
                hard = []
                for x in rejected:
                    reason = str(x.get("reason") or "")
                    if any(
                        k in reason
                        for k in (
                            "Max open positions",
                            "Max F&O positions",
                            "Already in position",
                            "Max positions per symbol",
                            "remaining",
                        )
                    ):
                        soft.append({**x, "status": "SKIPPED"})
                    else:
                        hard.append({**x, "status": "REJECTED"})
                rejected, skipped = hard, soft
            row["rejected"] = rejected
            row["skipped"] = skipped
            row["rejected_count"] = len(rejected)
            row["skipped_count"] = len(skipped)
        return rows


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
