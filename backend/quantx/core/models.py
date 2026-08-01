"""Domain models for QuantX recommendations, positions, and risk."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class TradeType(str, Enum):
    INTRADAY = "INTRADAY"
    SWING = "SWING"
    OPTIONS = "OPTIONS"
    FUTURES = "FUTURES"
    ETF = "ETF"


class MarketDirection(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"
    RANGE_BOUND = "RANGE_BOUND"


class PositionStatus(str, Enum):
    OPEN = "OPEN"
    CLOSED = "CLOSED"
    CANCELLED = "CANCELLED"


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class AIScores(BaseModel):
    confidence: float = Field(ge=0, le=100, description="AI confidence 0-100")
    risk_score: float = Field(ge=0, le=100, description="Higher = riskier")
    volatility_score: float = Field(ge=0, le=100)
    probability_of_success: float = Field(ge=0, le=100)
    expected_return_pct: float = 0.0
    expected_drawdown_pct: float = 0.0


class TradeRecommendation(BaseModel):
    symbol: str
    exchange: str = "NSE"
    market_direction: MarketDirection
    trade_type: TradeType
    side: Side
    entry: float
    stop_loss: float
    target_1: float
    target_2: float
    risk_reward: float
    quantity: int
    capital_at_risk: float
    scores: AIScores
    reason: str
    supporting_indicators: list[str] = Field(default_factory=list)
    fundamental_summary: str = ""
    options_summary: str = ""
    risk_notes: str = ""
    alternative_scenario: str = ""
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    valid: bool = True
    rejection_reason: Optional[str] = None


class Position(BaseModel):
    id: Optional[int] = None
    symbol: str
    exchange: str = "NSE"
    side: Side
    trade_type: TradeType
    quantity: int
    entry_price: float
    current_price: float
    stop_loss: float
    target_1: float
    target_2: float
    trailing_stop: Optional[float] = None
    status: PositionStatus = PositionStatus.OPEN
    pnl: float = 0.0
    pnl_pct: float = 0.0
    capital_at_risk: float = 0.0
    confidence: float = 0.0
    reason: str = ""
    opened_at: datetime = Field(default_factory=datetime.utcnow)
    closed_at: Optional[datetime] = None
    exit_price: Optional[float] = None
    exit_reason: Optional[str] = None


class TradeJournalEntry(BaseModel):
    id: Optional[int] = None
    symbol: str
    side: Side
    trade_type: TradeType
    entry: float
    exit: float
    quantity: int
    pnl: float
    pnl_pct: float
    reason: str
    mistakes: str = ""
    market_condition: str = ""
    confidence: float = 0.0
    supporting_indicators: list[str] = Field(default_factory=list)
    lessons: str = ""
    opened_at: datetime
    closed_at: datetime = Field(default_factory=datetime.utcnow)


class PortfolioSnapshot(BaseModel):
    capital: float
    available_margin: float
    used_margin: float
    open_positions: int
    unrealized_pnl: float
    realized_pnl_today: float
    realized_pnl_week: float
    total_pnl: float
    daily_pnl_pct: float
    weekly_pnl_pct: float
    drawdown_pct: float
    consecutive_losses: int
    trading_halted: bool
    halt_reason: Optional[str] = None
    kill_switch_active: bool = False
    mode: str = "paper"


class RiskStatus(BaseModel):
    can_trade: bool
    reasons: list[str] = Field(default_factory=list)
    daily_loss_used_pct: float = 0.0
    weekly_loss_used_pct: float = 0.0
    drawdown_pct: float = 0.0
    open_positions: int = 0
    consecutive_losses: int = 0
    risk_budget_remaining: float = 0.0


class MarketSession(BaseModel):
    is_open: bool
    phase: str  # pre_market | open | post_market | closed
    server_time_ist: str
    next_open: Optional[str] = None


class EmergencyState(BaseModel):
    kill_switch: bool = False
    panic_exit_requested: bool = False
    max_drawdown_lock: bool = False
    manual_override: bool = False
    broker_connected: bool = True
    internet_ok: bool = True
    messages: list[str] = Field(default_factory=list)


class Report(BaseModel):
    report_type: str
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    title: str
    summary: str
    sections: dict[str, Any] = Field(default_factory=dict)
