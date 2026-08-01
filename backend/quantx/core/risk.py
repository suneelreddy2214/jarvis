"""
QuantX Risk Management Engine.

Capital preservation is the primary objective.
All trade proposals must pass these gates before execution.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from quantx.core.config import RiskConfig, Settings, get_settings
from quantx.core.models import RiskStatus, Side, TradeRecommendation


@dataclass
class RiskState:
    capital: float
    peak_capital: float
    daily_realized_pnl: float = 0.0
    weekly_realized_pnl: float = 0.0
    open_positions: int = 0
    consecutive_losses: int = 0
    kill_switch: bool = False
    max_drawdown_lock: bool = False
    manual_override: bool = False
    trading_halted: bool = False
    halt_reason: Optional[str] = None
    open_risk_amount: float = 0.0


class RiskManager:
    """Enforces institutional risk limits. Never violated."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self.cfg: RiskConfig = self.settings.risk
        self.state = RiskState(
            capital=self.settings.capital.initial,
            peak_capital=self.settings.capital.initial,
        )

    def update_state(self, **kwargs) -> None:
        for k, v in kwargs.items():
            if hasattr(self.state, k):
                setattr(self.state, k, v)
        if self.state.capital > self.state.peak_capital:
            self.state.peak_capital = self.state.capital
        self._recompute_halt()

    @property
    def drawdown_pct(self) -> float:
        if self.state.peak_capital <= 0:
            return 0.0
        return max(0.0, (self.state.peak_capital - self.state.capital) / self.state.peak_capital * 100)

    @property
    def daily_loss_pct(self) -> float:
        if self.state.capital <= 0:
            return 0.0
        base = self.state.capital - self.state.daily_realized_pnl
        if base <= 0:
            return 0.0
        loss = min(0.0, self.state.daily_realized_pnl)
        return abs(loss) / base * 100

    @property
    def weekly_loss_pct(self) -> float:
        if self.state.capital <= 0:
            return 0.0
        base = self.state.capital - self.state.weekly_realized_pnl
        if base <= 0:
            return 0.0
        loss = min(0.0, self.state.weekly_realized_pnl)
        return abs(loss) / base * 100

    def _recompute_halt(self) -> None:
        reasons: list[str] = []

        if self.state.kill_switch:
            reasons.append("Kill switch activated")
        if self.state.max_drawdown_lock:
            reasons.append("Max drawdown lock engaged")
        if self.drawdown_pct >= self.cfg.max_drawdown_pct:
            self.state.max_drawdown_lock = True
            reasons.append(f"Drawdown {self.drawdown_pct:.2f}% >= limit {self.cfg.max_drawdown_pct}%")
        if self.daily_loss_pct >= self.cfg.max_daily_loss_pct:
            reasons.append(f"Daily loss {self.daily_loss_pct:.2f}% >= limit {self.cfg.max_daily_loss_pct}%")
        if self.weekly_loss_pct >= self.cfg.max_weekly_loss_pct:
            reasons.append(f"Weekly loss {self.weekly_loss_pct:.2f}% >= limit {self.cfg.max_weekly_loss_pct}%")
        if self.state.consecutive_losses >= self.cfg.max_consecutive_losses:
            reasons.append(f"Consecutive losses {self.state.consecutive_losses} >= {self.cfg.max_consecutive_losses}")

        if reasons and not self.state.manual_override:
            self.state.trading_halted = True
            self.state.halt_reason = "; ".join(reasons)
        elif self.state.manual_override:
            self.state.trading_halted = False
            self.state.halt_reason = "Manual override active — risk gates bypassed (use with caution)"
        else:
            self.state.trading_halted = False
            self.state.halt_reason = None

    def status(self) -> RiskStatus:
        self._recompute_halt()
        reasons: list[str] = []
        can_trade = True

        if self.state.trading_halted and not self.state.manual_override:
            can_trade = False
            reasons.append(self.state.halt_reason or "Trading halted")

        if self.state.open_positions >= self.cfg.max_open_positions:
            can_trade = False
            reasons.append(f"Max open positions ({self.cfg.max_open_positions}) reached")

        risk_budget = self.state.capital * (self.cfg.max_risk_per_trade_pct / 100)
        remaining = max(0.0, risk_budget)  # per-trade budget; open risk tracked separately

        return RiskStatus(
            can_trade=can_trade,
            reasons=reasons,
            daily_loss_used_pct=self.daily_loss_pct,
            weekly_loss_used_pct=self.weekly_loss_pct,
            drawdown_pct=self.drawdown_pct,
            open_positions=self.state.open_positions,
            consecutive_losses=self.state.consecutive_losses,
            risk_budget_remaining=remaining,
        )

    def validate_recommendation(self, rec: TradeRecommendation) -> tuple[bool, list[str]]:
        """Validate a trade against all risk rules. Returns (ok, rejection_reasons)."""
        reasons: list[str] = []
        status = self.status()

        if not status.can_trade:
            reasons.extend(status.reasons)

        # Risk per trade
        risk_pct = (rec.capital_at_risk / self.state.capital * 100) if self.state.capital else 999
        if risk_pct > self.cfg.max_risk_per_trade_pct + 1e-6:
            reasons.append(
                f"Risk {risk_pct:.2f}% exceeds max {self.cfg.max_risk_per_trade_pct}% per trade"
            )

        # Risk-reward
        if rec.risk_reward < self.cfg.min_risk_reward:
            reasons.append(
                f"Risk-reward {rec.risk_reward:.2f} below minimum {self.cfg.min_risk_reward}"
            )

        # Stop loss must exist and be on correct side
        if rec.side == Side.BUY and rec.stop_loss >= rec.entry:
            reasons.append("BUY stop loss must be below entry")
        if rec.side == Side.SELL and rec.stop_loss <= rec.entry:
            reasons.append("SELL stop loss must be above entry")

        # Targets must be beyond entry
        if rec.side == Side.BUY and rec.target_1 <= rec.entry:
            reasons.append("BUY target must be above entry")
        if rec.side == Side.SELL and rec.target_1 >= rec.entry:
            reasons.append("SELL target must be below entry")

        # Quantity
        if rec.quantity <= 0:
            reasons.append("Quantity must be positive")

        # Never average losing trades — checked at portfolio layer when adding

        return (len(reasons) == 0, reasons)

    def register_trade_result(self, pnl: float) -> None:
        """Update consecutive loss streak and PnL after a closed trade."""
        self.state.daily_realized_pnl += pnl
        self.state.weekly_realized_pnl += pnl
        self.state.capital += pnl
        if pnl < 0:
            self.state.consecutive_losses += 1
        else:
            self.state.consecutive_losses = 0
        if self.state.capital > self.state.peak_capital:
            self.state.peak_capital = self.state.capital
        self._recompute_halt()

    def activate_kill_switch(self, reason: str = "Manual kill switch") -> None:
        self.state.kill_switch = True
        self.state.trading_halted = True
        self.state.halt_reason = reason
        self.state.manual_override = False

    def deactivate_kill_switch(self) -> None:
        self.state.kill_switch = False
        self._recompute_halt()

    def reset_daily(self) -> None:
        self.state.daily_realized_pnl = 0.0
        self.state.consecutive_losses = 0
        self._recompute_halt()

    def reset_weekly(self) -> None:
        self.state.weekly_realized_pnl = 0.0
        self._recompute_halt()
