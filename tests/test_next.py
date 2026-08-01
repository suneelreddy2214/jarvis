"""Tests for strategies, backtester, and broker factory."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from quantx.analysis.backtest import Backtester
from quantx.analysis.strategies import list_strategies, get_strategy
from quantx.core.config import Settings
from quantx.data.market_data import MarketDataService
from quantx.execution.base import PaperBrokerAdapter, broker_credentials_present, create_broker
from quantx.execution.broker import PaperBroker
from quantx.portfolio.db import Database
from quantx.portfolio.manager import PortfolioManager
from quantx.core.risk import RiskManager


@pytest.fixture
def settings(tmp_path):
    s = Settings()
    s.database = {"path": str(tmp_path / "bt.db")}
    s.agent.mode = "paper"
    s.broker = {"name": "paper", "slippage_bps": 5}
    return s


def test_list_strategies():
    names = {s["name"] for s in list_strategies()}
    assert "swing_trend" in names
    assert "breakout" in names
    assert "intraday_mean_reversion" in names


def test_get_strategy_unknown():
    with pytest.raises(KeyError):
        get_strategy("does_not_exist")


def test_backtest_runs_synthetic(settings):
    data = MarketDataService(use_live=False)
    bt = Backtester(settings=settings, market_data=data)
    result = bt.run("RELIANCE", strategy="swing_trend", period="1y")
    assert result.symbol == "RELIANCE"
    assert result.strategy == "swing_trend"
    assert result.starting_capital == settings.capital.initial
    assert result.max_drawdown_pct >= 0
    d = result.as_dict()
    assert "trade_log" in d
    assert "equity_curve" in d


def test_backtest_breakout_strategy(settings):
    data = MarketDataService(use_live=False)
    bt = Backtester(settings=settings, market_data=data)
    result = bt.run("TCS", strategy="breakout", period="1y")
    assert result.strategy == "breakout"
    assert result.ending_capital > 0


def test_paper_broker_factory(settings, tmp_path):
    db = Database(tmp_path / "x.db")
    pm = PortfolioManager(db=db, risk=RiskManager(settings), market_data=MarketDataService(use_live=False), settings=settings)
    adapter = create_broker("paper", portfolio=pm, db=db, settings=settings)
    assert adapter.name == "paper"
    status = adapter.status()
    assert status["connected"] is True
    assert status["mode"] == "paper"


def test_live_broker_requires_credentials(settings):
    import os
    for k in ("QUANTX_BROKER_API_KEY", "QUANTX_BROKER_API_SECRET", "QUANTX_BROKER_ACCESS_TOKEN"):
        os.environ.pop(k, None)
    with pytest.raises(RuntimeError):
        create_broker("live")


def test_credentials_helper():
    creds = broker_credentials_present()
    assert set(creds) == {"api_key", "api_secret", "access_token"}
