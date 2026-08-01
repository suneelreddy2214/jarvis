"""Paper trading costs, broker fills, and session agent tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from quantx.core.config import Settings
from quantx.core.engine import QuantXEngine
from quantx.core.models import (
    AIScores,
    MarketDirection,
    Side,
    TradeRecommendation,
    TradeType,
)
from quantx.core.risk import RiskManager
from quantx.data.market_data import MarketDataService
from quantx.execution.broker import PaperBroker
from quantx.execution.costs import PaperCostModel
from quantx.execution.paper_agent import PaperTradingAgent
from quantx.portfolio.db import Database
from quantx.portfolio.manager import PortfolioManager


@pytest.fixture
def settings(tmp_path):
    s = Settings()
    s.database = {"path": str(tmp_path / "paper.db")}
    s.broker = {"name": "paper", "slippage_bps": 5}
    s.agent.mode = "paper"
    return s


@pytest.fixture
def stack(settings):
    db = Database(Path(settings.database["path"]))
    data = MarketDataService(use_live=False)
    risk = RiskManager(settings)
    pm = PortfolioManager(db=db, risk=risk, market_data=data, settings=settings)
    engine = QuantXEngine(settings=settings, risk_manager=risk, market_data=data)
    broker = PaperBroker(portfolio=pm, db=db, settings=settings)
    agent = PaperTradingAgent(portfolio=pm, engine=engine, broker=broker, settings=settings)
    return agent


def test_cost_model_adverse_slippage():
    m = PaperCostModel(slippage_bps=10)
    buy = m.apply_slippage(1000, Side.BUY, is_entry=True)
    sell = m.apply_slippage(1000, Side.SELL, is_entry=True)
    assert buy > 1000
    assert sell < 1000
    costs = m.estimate(1000, 1020, 10, Side.BUY, "swing")
    assert costs.total > 0
    assert costs.brokerage > 0


def test_paper_cycle_runs(stack):
    result = stack.run_once()
    assert "message" in result
    assert "signals" in result
    assert result["portfolio"]["mode"] == "paper"


def test_paper_reset(stack):
    # Force a fee charge then reset
    stack.portfolio.charge_fees(100, "test")
    out = stack.reset_account(confirm=True)
    assert out["ok"] is True
    assert stack.portfolio.snapshot().capital == stack.settings.capital.initial


def test_no_duplicate_symbol_position(stack):
    rec = stack.engine.analyze_symbol("RELIANCE")
    if not rec.valid:
        # Force a minimal valid synthetic rec for gate test
        quote = stack.portfolio.data.get_quote("RELIANCE")
        px = quote["price"]
        rec = TradeRecommendation(
            symbol="RELIANCE",
            market_direction=MarketDirection.BULLISH,
            trade_type=TradeType.SWING,
            side=Side.BUY,
            entry=px,
            stop_loss=px * 0.98,
            target_1=px * 1.04,
            target_2=px * 1.06,
            risk_reward=2.0,
            quantity=10,
            capital_at_risk=px * 0.02 * 10,
            scores=AIScores(
                confidence=70, risk_score=40, volatility_score=40, probability_of_success=60
            ),
            reason="forced",
            valid=True,
        )
        # Ensure risk accepts
        if rec.capital_at_risk > stack.portfolio.risk.state.capital * 0.01:
            rec.quantity = max(1, int((stack.portfolio.risk.state.capital * 0.01) / (px * 0.02)))
            rec.capital_at_risk = abs(rec.entry - rec.stop_loss) * rec.quantity

    fill1 = stack.broker.execute(rec)
    # Second attempt same day/levels should be blocked either as duplicate or existing position
    fill2 = stack.broker.execute(rec)
    assert fill1["status"] in ("FILLED", "REJECTED")
    if fill1["status"] == "FILLED":
        assert fill2["status"] == "REJECTED"


def test_performance_endpoint_shape(stack):
    perf = stack.portfolio.performance()
    assert "win_rate" in perf
    assert "total_fees" in perf
    assert perf["trades"] >= 0
