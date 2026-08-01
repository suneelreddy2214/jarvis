"""Hard stop fill + anomalous gap protection tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from quantx.analysis.additional_strategies import AdxTrendFilterStrategy
from quantx.analysis.technical import IndicatorSnapshot
from quantx.core.config import Settings
from quantx.core.models import MarketDirection, Position, PositionStatus, Side, TradeType
from quantx.core.risk import RiskManager
from quantx.data.market_data import MarketDataService
from quantx.portfolio.db import Database
from quantx.portfolio.manager import PortfolioManager


def _snap(**kwargs):
    base = dict(
        close=100.0,
        ema_9=100.0,
        ema_21=99.0,
        ema_50=98.0,
        ema_200=95.0,
        sma_20=99.0,
        sma_50=98.0,
        rsi=55.0,
        macd=1.0,
        macd_signal=0.5,
        macd_hist=0.5,
        adx=30.0,
        atr=2.0,
        bb_upper=105.0,
        bb_mid=100.0,
        bb_lower=95.0,
        supertrend=99.0,
        supertrend_dir=1,
        vwap=100.0,
        volume=1_000_000.0,
        avg_volume=500_000.0,
        volume_ratio=1.2,
        pivot=100.0,
        r1=102.0,
        s1=98.0,
        trend=MarketDirection.BULLISH,
        momentum="strong_up",
        supporting=[],
    )
    base.update(kwargs)
    return IndicatorSnapshot(**base)


def test_adx_requires_ema_alignment():
    s = AdxTrendFilterStrategy()
    df = pd.DataFrame({"close": [100.0] * 30})
    sig = s.evaluate(
        _snap(close=90, ema_50=100, ema_200=110, trend=MarketDirection.BULLISH, supertrend_dir=1),
        df,
    )
    assert sig.side is None


def test_adx_aligned_long():
    s = AdxTrendFilterStrategy()
    df = pd.DataFrame({"close": [100.0] * 30})
    sig = s.evaluate(_snap(), df)
    assert sig.side == Side.BUY


def test_hard_stop_fill_caps_loss(tmp_path):
    settings = Settings()
    db_path = tmp_path / "t.db"
    settings.database = {"path": str(db_path)}
    db = Database(path=db_path)
    data = MarketDataService(use_live=False, yahoo_only=False, allow_synthetic=True)
    pm = PortfolioManager(db=db, risk=RiskManager(settings), market_data=data, settings=settings)
    pm.risk.update_state(capital=1_000_000)
    pm._persist_risk()

    pos = Position(
        symbol="TESTCO",
        exchange="NSE",
        side=Side.BUY,
        trade_type=TradeType.SWING,
        quantity=100,
        entry_price=100.0,
        current_price=100.0,
        stop_loss=95.0,
        target_1=110.0,
        target_2=115.0,
        capital_at_risk=500.0,
        confidence=70,
        reason="test",
        status=PositionStatus.OPEN,
    )
    pos.id = db.insert_position(pos)

    class GapData:
        def get_quote(self, symbol, exchange="NSE"):
            return {"price": 40.0, "change_pct": -60}

    pm.data = GapData()  # type: ignore
    pm.mark_to_market()
    closed = db.get_position(pos.id)
    assert closed is not None
    assert closed.status == PositionStatus.CLOSED
    assert closed.exit_price is not None
    assert abs(closed.exit_price - 95.0) < 1.0
    assert closed.pnl > -600
