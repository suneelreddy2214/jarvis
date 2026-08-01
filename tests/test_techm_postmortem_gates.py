"""TECHM post-mortem gates: ADX, ATR stop floor, EMA exit, volume."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from quantx.analysis.technical import IndicatorSnapshot, TechnicalAnalyzer  # noqa: E402
from quantx.core.config import Settings  # noqa: E402
from quantx.core.engine import QuantXEngine  # noqa: E402
from quantx.core.models import MarketDirection, Side, TradeType  # noqa: E402
from quantx.core.position_sizing import PositionSizer  # noqa: E402
from quantx.core.risk import RiskManager  # noqa: E402
from quantx.data.market_data import MarketDataService  # noqa: E402
from quantx.portfolio.db import Database  # noqa: E402
from quantx.portfolio.manager import PortfolioManager  # noqa: E402


def _snap(**kwargs) -> IndicatorSnapshot:
    base = dict(
        close=1652.0,
        ema_9=1660.0,
        ema_21=1640.0,
        ema_50=1620.0,
        ema_200=1500.0,
        sma_20=1645.0,
        sma_50=1620.0,
        rsi=58.0,
        macd=1.0,
        macd_signal=0.5,
        macd_hist=0.5,
        adx=28.0,
        atr=40.0,
        bb_upper=1700.0,
        bb_mid=1650.0,
        bb_lower=1600.0,
        supertrend=1600.0,
        supertrend_dir=1,
        vwap=1650.0,
        volume=1_000_000.0,
        avg_volume=700_000.0,
        volume_ratio=1.5,
        pivot=1650.0,
        r1=1680.0,
        s1=1640.0,  # close support — old logic tightened stop here
        trend=MarketDirection.BULLISH,
        momentum="up",
        supporting=[],
    )
    base.update(kwargs)
    return IndicatorSnapshot(**base)


def test_atr_stop_not_tightened_by_nearby_s1():
    """TECHM bug: min(entry-2ATR, S1) made stops too tight vs ATR."""
    ta = TechnicalAnalyzer()
    snap = _snap(atr=40.0, close=1652.0, s1=1640.0)  # S1 only ~12 pts away
    levels = ta.levels_for_trade(snap, Side.BUY, atr_stop_mult=2.5)
    stop_dist = levels["entry"] - levels["stop_loss"]
    assert stop_dist >= 2.5 * 40.0 - 0.05, f"stop too tight: {stop_dist}"


def test_sizer_rejects_noise_tight_stop():
    s = Settings()
    s.position_sizing.atr_multiplier = 2.5
    s.position_sizing.min_stop_atr_mult = 2.0
    sizer = PositionSizer(s)
    # Entry 1652, SL 1640 (~12 pts) with ATR 40 → reject
    out = sizer.calculate(
        capital=1_000_000,
        entry=1652,
        stop_loss=1640,
        side=Side.BUY,
        atr=40.0,
    )
    assert out.quantity == 0
    assert "too tight" in out.notes.lower() or "ATR" in out.notes


def test_engine_rejects_low_adx(tmp_path):
    s = Settings()
    s.database = {"path": str(tmp_path / "t.db")}
    s.entry.min_adx = 25.0
    s.agent.mode = "paper"
    db = Database(Path(s.database["path"]))
    data = MarketDataService(use_live=False)
    risk = RiskManager(s)
    engine = QuantXEngine(settings=s, risk_manager=risk, market_data=data)
    # Force analyze via synthetic path — monkeypatch ta + data
    import quantx.core.engine as eng_mod

    snap = _snap(adx=18.0, volume_ratio=1.6, ema_9=1660, ema_21=1640, ema_50=1620)
    engine.ta.analyze = lambda df: snap  # type: ignore
    engine.ta.suggest_side = lambda snap: Side.BUY  # type: ignore
    engine.ta.levels_for_trade = TechnicalAnalyzer().levels_for_trade  # type: ignore
    engine.fa.analyze = lambda *a, **k: type("F", (), {"score": 70, "summary": "ok", "data_quality": "full"})()  # type: ignore
    engine.macro.analyze = lambda **k: type(
        "M",
        (),
        {
            "summary": "ok",
            "avoid_new_risk": False,
            "size_multiplier": 1.0,
            "require_extra_confirmation": False,
            "caution_flags": [],
            "nifty_bias": "neutral",
            "vix_regime": "moderate",
            "vix_level": 16.0,
            "usdinr_bias": "neutral",
        },
    )()  # type: ignore
    engine.oa.analyze = lambda *a, **k: type("O", (), {"summary": ""})()  # type: ignore
    data.get_ohlc = lambda *a, **k: pd.DataFrame({"close": [1650] * 50})  # type: ignore
    data.resolve_yahoo_symbol = lambda *a, **k: ("TECHM.NS", "yahoo")  # type: ignore

    rec = engine.analyze_symbol("TECHM", strategy="swing_trend")
    assert not rec.valid
    assert rec.rejection_reason and "ADX" in rec.rejection_reason


def test_opposite_ema_exit(tmp_path):
    s = Settings()
    s.database = {"path": str(tmp_path / "ema.db")}
    s.exit.exit_on_opposite_ema_stack = True
    s.exit.force_flat_on_hard_loss_limit = False
    s.agent.mode = "paper"
    db = Database(Path(s.database["path"]))
    data = MarketDataService(use_live=False)
    risk = RiskManager(s)
    pm = PortfolioManager(db=db, risk=risk, market_data=data, settings=s)

    from quantx.core.models import TradeRecommendation, AIScores
    from datetime import datetime

    rec = TradeRecommendation(
        symbol="TECHM",
        exchange="NSE",
        market_direction=MarketDirection.BULLISH,
        trade_type=TradeType.SWING,
        side=Side.BUY,
        entry=1652.0,
        stop_loss=1550.0,
        target_1=1837.0,
        target_2=1900.0,
        risk_reward=2.0,
        quantity=1,
        capital_at_risk=100.0,
        scores=AIScores(confidence=70, risk_score=40, volatility_score=50, probability_of_success=60),
        reason="test",
        valid=True,
        generated_at=datetime.utcnow(),
    )
    # Bypass broker — open directly
    pos = pm.open_from_recommendation(rec)
    assert pos.status.value == "OPEN" or str(pos.status) == "OPEN"

    # Bearish EMA stack on MTM
    bear = _snap(ema_9=1600, ema_21=1620, ema_50=1650, close=1620)
    from quantx.analysis.technical import TechnicalAnalyzer

    data.get_quote = lambda *a, **k: {"price": 1620.0, "change_pct": -1.0}  # type: ignore
    data.get_ohlc = lambda *a, **k: pd.DataFrame({"close": [1620] * 60})  # type: ignore
    TechnicalAnalyzer.analyze = lambda self, df: bear  # type: ignore

    opens = pm.mark_to_market()
    # Position should be closed via opposite EMA
    closed = db.list_positions("CLOSED")
    assert any("EMA stack" in (c.exit_reason or "") for c in closed) or len(opens) == 0
