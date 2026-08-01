"""
QuantX Risk Management Engine.

Capital preservation is the primary objective.
All trade proposals must pass these gates before execution.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo

from quantx.core.config import RiskConfig, Settings, get_settings
from quantx.core.models import RiskStatus, Side, TradeRecommendation

IST = ZoneInfo("Asia/Kolkata")


@dataclass
class RiskState:
    capital: float
    peak_capital: float
    daily_realized_pnl: float = 0.0
    weekly_realized_pnl: float = 0.0
    unrealized_pnl: float = 0.0
    open_positions: int = 0
    consecutive_losses: int = 0
    trades_today: int = 0
    kill_switch: bool = False
    max_drawdown_lock: bool = False
    manual_override: bool = False
    trading_halted: bool = False
    halt_reason: Optional[str] = None
    open_risk_amount: float = 0.0
    risk_day: Optional[str] = None  # YYYY-MM-DD IST
    risk_week: Optional[str] = None  # ISO week key
    loss_pause_until: Optional[str] = None  # ISO timestamp — no new entries while active


class RiskManager:
    """Enforces institutional risk limits. Never violated."""

    def __init__(self, settings: Optional[Settings] = None):
        self.settings = settings or get_settings()
        self.cfg: RiskConfig = self.settings.risk
        self.state = RiskState(
            capital=self.settings.capital.initial,
            peak_capital=self.settings.capital.initial,
        )

    @staticmethod
    def _today_ist() -> date:
        return datetime.now(IST).date()

    @staticmethod
    def _week_key(d: Optional[date] = None) -> str:
        d = d or RiskManager._today_ist()
        iso = d.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"

    def ensure_period_rolls(self) -> None:
        """Reset daily/weekly counters on IST calendar boundaries."""
        today = self._today_ist().isoformat()
        week = self._week_key()
        if self.state.risk_day and self.state.risk_day != today:
            self.state.daily_realized_pnl = 0.0
            self.state.consecutive_losses = 0
            self.state.trades_today = 0
        if self.state.risk_week and self.state.risk_week != week:
            self.state.weekly_realized_pnl = 0.0
        self.state.risk_day = today
        self.state.risk_week = week

    def update_state(self, **kwargs) -> None:
        for k, v in kwargs.items():
            if hasattr(self.state, k):
                setattr(self.state, k, v)
        if self.state.capital > self.state.peak_capital:
            self.state.peak_capital = self.state.capital
        self.ensure_period_rolls()
        self._recompute_halt()

    def _loss_base(self, period_pnl: float) -> float:
        base = self.state.capital - period_pnl
        return base if base > 0 else 0.0

    def _period_loss_pct(self, period_pnl: float) -> float:
        """Loss % vs period-start capital; optionally folds in open unrealized."""
        pnl = period_pnl
        if self.cfg.include_unrealized_in_loss_limits:
            pnl = period_pnl + float(self.state.unrealized_pnl or 0.0)
        base = self._loss_base(period_pnl)
        if base <= 0:
            return 0.0
        loss = min(0.0, pnl)
        return abs(loss) / base * 100

    @property
    def drawdown_pct(self) -> float:
        if self.state.peak_capital <= 0:
            return 0.0
        equity = self.state.capital + float(self.state.unrealized_pnl or 0.0)
        return max(0.0, (self.state.peak_capital - equity) / self.state.peak_capital * 100)

    @property
    def daily_loss_pct(self) -> float:
        return self._period_loss_pct(self.state.daily_realized_pnl)

    @property
    def weekly_loss_pct(self) -> float:
        return self._period_loss_pct(self.state.weekly_realized_pnl)

    def _recompute_halt(self) -> None:
        self.ensure_period_rolls()
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
        if self.state.trades_today >= self.cfg.max_trades_per_day:
            reasons.append(f"Max trades/day {self.cfg.max_trades_per_day} reached")

        # Soft throttles — stop NEW risk before hard kill limits (post-mortem: halted too late)
        soft_daily = float(getattr(self.cfg, "soft_daily_loss_pct", 1.0) or 1.0)
        soft_weekly = float(getattr(self.cfg, "soft_weekly_loss_pct", 2.5) or 2.5)
        soft_dd = float(getattr(self.cfg, "soft_drawdown_pct", 5.0) or 5.0)
        if self.daily_loss_pct >= soft_daily:
            reasons.append(
                f"Soft daily loss throttle {self.daily_loss_pct:.2f}% >= {soft_daily}% — no new entries"
            )
        if self.weekly_loss_pct >= soft_weekly:
            reasons.append(
                f"Soft weekly loss throttle {self.weekly_loss_pct:.2f}% >= {soft_weekly}% — no new entries"
            )
        if self.drawdown_pct >= soft_dd and not self.state.max_drawdown_lock:
            reasons.append(
                f"Soft drawdown throttle {self.drawdown_pct:.2f}% >= {soft_dd}% — no new entries"
            )

        # Post-loss pause (anti-overtrading / revenge trading)
        if self._loss_pause_active():
            reasons.append(
                f"Post-loss pause active until {self.state.loss_pause_until} "
                f"(streak {self.state.consecutive_losses})"
            )

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

        agg_cap = self.state.capital * (self.cfg.max_aggregate_open_risk_pct / 100.0)
        if self.state.open_risk_amount >= agg_cap > 0:
            can_trade = False
            reasons.append(
                f"Aggregate open risk ₹{self.state.open_risk_amount:.0f} "
                f">= {self.cfg.max_aggregate_open_risk_pct}% cap"
            )

        risk_budget = self.state.capital * (self.cfg.max_risk_per_trade_pct / 100)

        return RiskStatus(
            can_trade=can_trade,
            reasons=reasons,
            daily_loss_used_pct=self.daily_loss_pct,
            weekly_loss_used_pct=self.weekly_loss_pct,
            drawdown_pct=self.drawdown_pct,
            open_positions=self.state.open_positions,
            consecutive_losses=self.state.consecutive_losses,
            risk_budget_remaining=max(0.0, risk_budget),
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

        # Aggregate open risk after this trade
        agg_cap = self.state.capital * (self.cfg.max_aggregate_open_risk_pct / 100.0)
        projected = self.state.open_risk_amount + float(rec.capital_at_risk or 0)
        if agg_cap > 0 and projected > agg_cap + 1e-6:
            reasons.append(
                f"Projected aggregate open risk ₹{projected:.0f} exceeds "
                f"{self.cfg.max_aggregate_open_risk_pct}% cap (₹{agg_cap:.0f})"
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

        return (len(reasons) == 0, reasons)

    def _loss_pause_active(self) -> bool:
        until = self.state.loss_pause_until
        if not until:
            return False
        try:
            expiry = datetime.fromisoformat(str(until).replace("Z", "+00:00"))
            if expiry.tzinfo is None:
                expiry = expiry.replace(tzinfo=IST)
            now = datetime.now(IST)
            if expiry.tzinfo != IST:
                expiry = expiry.astimezone(IST)
            if now < expiry:
                return True
            # Expired — clear
            self.state.loss_pause_until = None
            return False
        except Exception:
            self.state.loss_pause_until = None
            return False

    def _arm_loss_pause(self) -> None:
        """Pause new entries after consecutive losses to prevent overtrading."""
        threshold = int(getattr(self.cfg, "pause_after_consecutive_losses", 1) or 1)
        minutes_per = int(getattr(self.cfg, "loss_pause_minutes_per_streak", 30) or 30)
        streak = int(self.state.consecutive_losses or 0)
        if streak < threshold or minutes_per <= 0:
            return
        pause_min = minutes_per * streak
        until = datetime.now(IST) + timedelta(minutes=pause_min)
        self.state.loss_pause_until = until.isoformat()

    def register_trade_result(self, pnl: float) -> None:
        """Update consecutive loss streak and PnL after a closed trade."""
        self.ensure_period_rolls()
        self.state.daily_realized_pnl += pnl
        self.state.weekly_realized_pnl += pnl
        self.state.capital += pnl
        if pnl < 0:
            self.state.consecutive_losses += 1
            self._arm_loss_pause()
        else:
            self.state.consecutive_losses = 0
            self.state.loss_pause_until = None
        if self.state.capital > self.state.peak_capital:
            self.state.peak_capital = self.state.capital
        self._recompute_halt()

    def register_entry(self) -> None:
        """Count a new fill toward the daily trade budget."""
        self.ensure_period_rolls()
        self.state.trades_today += 1
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
        self.state.trades_today = 0
        self.state.loss_pause_until = None
        self.state.risk_day = self._today_ist().isoformat()
        self._recompute_halt()

    def reset_weekly(self) -> None:
        self.state.weekly_realized_pnl = 0.0
        self.state.risk_week = self._week_key()
        self._recompute_halt()
