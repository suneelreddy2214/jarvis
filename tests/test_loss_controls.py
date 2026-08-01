"""Tests for loss-control hardening (ADX/VIX/volume/risk/trailing)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from quantx.analysis.macro import MacroAnalyzer
from quantx.core.config import Settings, get_settings
from quantx.core.risk import RiskManager


def test_vix_hard_blocks_at_20():
    m = MacroAnalyzer().analyze(india_vix=20.5, nifty_change_pct=0.2)
    assert m.avoid_new_risk is True
    assert m.size_multiplier == 0.0
    assert "elevated" in m.vix_regime


def test_vix_caution_sizes_down():
    m = MacroAnalyzer().analyze(india_vix=18.5, nifty_change_pct=0.2)
    assert m.avoid_new_risk is False
    assert m.size_multiplier == 0.5


def test_bearish_nifty_with_vix_18_blocks():
    m = MacroAnalyzer().analyze(india_vix=18.2, nifty_change_pct=-0.8)
    assert m.avoid_new_risk is True


def test_daily_loss_includes_unrealized_and_halts():
    get_settings.cache_clear()
    settings = Settings()
    rm = RiskManager(settings)
    rm.update_state(capital=1_000_000, daily_realized_pnl=-5_000, unrealized_pnl=-20_000)
    # realized -0.5% + unrealized -2% on ~1.005M base ≈ > 2%
    assert rm.daily_loss_pct >= settings.risk.max_daily_loss_pct
    st = rm.status()
    assert st.can_trade is False
    assert any("Daily loss" in r for r in st.reasons)


def test_aggregate_open_risk_blocks_new_trade():
    settings = Settings()
    rm = RiskManager(settings)
    rm.update_state(capital=1_000_000, open_risk_amount=30_000, open_positions=1)
    st = rm.status()
    assert st.can_trade is False
    assert any("Aggregate open risk" in r for r in st.reasons)


def test_max_trades_per_day_halts():
    settings = Settings()
    rm = RiskManager(settings)
    rm.update_state(capital=1_000_000, trades_today=settings.risk.max_trades_per_day)
    st = rm.status()
    assert st.can_trade is False
    assert any("Max trades/day" in r for r in st.reasons)


def test_consecutive_losses_still_halt():
    settings = Settings()
    rm = RiskManager(settings)
    rm.update_state(capital=1_000_000)
    for _ in range(3):
        rm.register_trade_result(-1000)
    assert rm.state.consecutive_losses >= 3
    st = rm.status()
    assert st.can_trade is False
