"""Unit tests for QuantX risk, sizing, and technical analysis."""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from quantx.analysis.options import OptionsAnalyzer
from quantx.analysis.technical import TechnicalAnalyzer
from quantx.core.config import Settings
from quantx.core.models import (
    AIScores,
    MarketDirection,
    Side,
    TradeRecommendation,
    TradeType,
)
from quantx.core.position_sizing import PositionSizer
from quantx.core.risk import RiskManager
from quantx.data.market_data import MarketDataService
from quantx.core.engine import QuantXEngine


@pytest.fixture
def settings():
    return Settings()


@pytest.fixture
def risk(settings):
    return RiskManager(settings)


def test_risk_blocks_oversize_trade(risk):
    rec = TradeRecommendation(
        symbol="TEST",
        market_direction=MarketDirection.BULLISH,
        trade_type=TradeType.SWING,
        side=Side.BUY,
        entry=100,
        stop_loss=90,
        target_1=120,
        target_2=130,
        risk_reward=2.0,
        quantity=2000,  # risk = 2000*10 = 20_000 on 1M = 2% > 1%
        capital_at_risk=20_000,
        scores=AIScores(confidence=70, risk_score=40, volatility_score=40, probability_of_success=60),
        reason="test",
    )
    ok, reasons = risk.validate_recommendation(rec)
    assert not ok
    assert any("exceeds max" in r for r in reasons)


def test_risk_requires_min_rr(risk):
    rec = TradeRecommendation(
        symbol="TEST",
        market_direction=MarketDirection.BULLISH,
        trade_type=TradeType.SWING,
        side=Side.BUY,
        entry=100,
        stop_loss=95,
        target_1=107,
        target_2=110,
        risk_reward=1.4,
        quantity=100,
        capital_at_risk=500,
        scores=AIScores(confidence=70, risk_score=40, volatility_score=40, probability_of_success=60),
        reason="test",
    )
    ok, reasons = risk.validate_recommendation(rec)
    assert not ok
    assert any("Risk-reward" in r for r in reasons)


def test_consecutive_losses_halt(risk):
    for _ in range(3):
        risk.register_trade_result(-1000)
    status = risk.status()
    assert not status.can_trade
    assert status.consecutive_losses == 3


def test_kill_switch(risk):
    risk.activate_kill_switch("test")
    assert not risk.status().can_trade
    risk.deactivate_kill_switch()
    # still may be ok if no other halt
    assert risk.state.kill_switch is False


def test_position_sizer_respects_1pct(settings):
    sizer = PositionSizer(settings)
    result = sizer.calculate(
        capital=1_000_000,
        entry=1000,
        stop_loss=980,  # 20 risk per share
        side=Side.BUY,
        atr=15,
    )
    assert result.quantity > 0
    assert result.risk_pct <= settings.risk.max_risk_per_trade_pct + 0.05
    assert result.capital_at_risk <= 1_000_000 * 0.01 + 1


def test_technical_analyzer_runs():
    rng = np.random.default_rng(42)
    n = 120
    close = 100 * np.cumprod(1 + rng.normal(0.001, 0.01, n))
    df = pd.DataFrame({
        "open": close * 0.99,
        "high": close * 1.01,
        "low": close * 0.98,
        "close": close,
        "volume": rng.integers(100000, 500000, n),
    })
    snap = TechnicalAnalyzer().analyze(df)
    assert snap.close > 0
    assert 0 <= snap.rsi <= 100
    assert snap.atr >= 0
    assert len(snap.supporting) > 0


def test_options_pcr():
    df = pd.DataFrame({
        "strike": [90, 100, 110],
        "call_oi": [100, 200, 150],
        "put_oi": [180, 220, 200],
        "call_iv": [20, 18, 22],
        "put_iv": [21, 19, 23],
    })
    summary = OptionsAnalyzer().analyze(df, spot=100)
    assert summary.pcr is not None
    assert summary.pcr > 1
    assert summary.max_pain is not None


def test_engine_with_synthetic_data(tmp_path, settings):
    settings.database = {"path": str(tmp_path / "test.db")}
    data = MarketDataService(use_live=False)
    risk = RiskManager(settings)
    engine = QuantXEngine(settings=settings, risk_manager=risk, market_data=data)
    rec = engine.analyze_symbol("RELIANCE", "NSE")
    assert rec.symbol == "RELIANCE"
    assert rec.scores is not None
    # May or may not be valid depending on synthetic path — but must not crash
    assert rec.reason


def test_never_average_losers_logic(tmp_path, settings):
    from quantx.portfolio.db import Database
    from quantx.portfolio.manager import PortfolioManager
    from quantx.core.models import Position, PositionStatus, TradeType

    db = Database(tmp_path / "t.db")
    pm = PortfolioManager(db=db, risk=RiskManager(settings), market_data=MarketDataService(use_live=False), settings=settings)
    pos = Position(
        symbol="AAA",
        side=Side.BUY,
        trade_type=TradeType.SWING,
        quantity=10,
        entry_price=100,
        current_price=90,
        stop_loss=85,
        target_1=120,
        target_2=130,
        pnl=-100,
        status=PositionStatus.OPEN,
    )
    db.insert_position(pos)

    rec = TradeRecommendation(
        symbol="AAA",
        market_direction=MarketDirection.BULLISH,
        trade_type=TradeType.SWING,
        side=Side.BUY,
        entry=90,
        stop_loss=85,
        target_1=100,
        target_2=110,
        risk_reward=2.0,
        quantity=10,
        capital_at_risk=50,
        scores=AIScores(confidence=70, risk_score=40, volatility_score=40, probability_of_success=60),
        reason="avg",
        valid=True,
    )
    # Make risk accept size
    with pytest.raises(ValueError, match="average losing"):
        pm.open_from_recommendation(rec)
