"""Configuration loader for QuantX."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = ROOT / "config" / "settings.yaml"
DATA_DIR = ROOT / "data"


class RiskConfig(BaseModel):
    max_risk_per_trade_pct: float = 1.0
    max_daily_loss_pct: float = 2.0
    max_weekly_loss_pct: float = 5.0
    max_open_positions: int = 3
    min_risk_reward: float = 2.0
    max_consecutive_losses: int = 3
    max_drawdown_pct: float = 10.0
    min_liquidity_avg_volume: int = 100_000
    max_aggregate_open_risk_pct: float = 3.0
    max_trades_per_day: int = 5
    include_unrealized_in_loss_limits: bool = True


class CapitalConfig(BaseModel):
    initial: float = 1_000_000
    currency: str = "INR"


class TradingHours(BaseModel):
    open: str = "09:15"
    close: str = "15:30"
    timezone: str = "Asia/Kolkata"


class MarketsConfig(BaseModel):
    exchanges: list[str] = Field(default_factory=lambda: ["NSE", "BSE"])
    products: list[str] = Field(default_factory=lambda: ["equity", "futures", "options", "etf"])
    styles: list[str] = Field(default_factory=lambda: ["intraday", "swing"])
    trading_hours: TradingHours = Field(default_factory=TradingHours)
    pre_market: str = "09:00"
    post_market: str = "15:40"


class PositionSizingConfig(BaseModel):
    method: str = "atr"
    atr_multiplier: float = 2.0
    default_atr_period: int = 14


class MarketDataConfig(BaseModel):
    provider: str = "yahoo_finance"
    yahoo_only: bool = True
    allow_synthetic: bool = False
    quote_cache_ttl_sec: float = 60.0
    ohlc_cache_ttl_sec: float = 300.0


class EntryConfig(BaseModel):
    require_trend: bool = True
    require_momentum: bool = True
    require_volume: bool = True
    require_adx: bool = True
    min_adx: float = 20.0
    min_volume_ratio: float = 1.0
    min_confidence: int = 60
    min_fundamental_score: int = 50
    avoid_major_news: bool = True


class ExitConfig(BaseModel):
    use_atr_stop: bool = True
    use_trailing_stop: bool = True
    trailing_atr_mult: float = 2.0
    trail_after_r: float = 1.0  # only trail after this many R of favorable move
    time_exit_bars: int = 20


class AgentConfig(BaseModel):
    name: str = "QuantX"
    version: str = "1.0.0"
    mode: str = "paper"


class Settings(BaseModel):
    agent: AgentConfig = Field(default_factory=AgentConfig)
    capital: CapitalConfig = Field(default_factory=CapitalConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    markets: MarketsConfig = Field(default_factory=MarketsConfig)
    position_sizing: PositionSizingConfig = Field(default_factory=PositionSizingConfig)
    market_data: MarketDataConfig = Field(default_factory=MarketDataConfig)
    entry: EntryConfig = Field(default_factory=EntryConfig)
    exit: ExitConfig = Field(default_factory=ExitConfig)
    database: dict[str, Any] = Field(default_factory=lambda: {"path": "data/quantx.db"})
    indicators: dict[str, Any] = Field(default_factory=dict)
    broker: dict[str, Any] = Field(default_factory=lambda: {"name": "paper"})
    paper: dict[str, Any] = Field(
        default_factory=lambda: {
            "auto_execute": True,
            "enable_fno": True,
            "max_fo_positions": 2,
            "max_positions_per_symbol": 1,
            "max_new_entries_per_cycle": 1,
            "symbol_cooldown_hours": 24,
        }
    )
    logging: dict[str, Any] = Field(default_factory=dict)

    @property
    def db_path(self) -> Path:
        path = Path(self.database.get("path", "data/quantx.db"))
        if not path.is_absolute():
            path = ROOT / path
        path.parent.mkdir(parents=True, exist_ok=True)
        return path


@lru_cache
def get_settings() -> Settings:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if CONFIG_PATH.exists():
        with open(CONFIG_PATH) as f:
            raw = yaml.safe_load(f) or {}
        return Settings(**raw)
    return Settings()
