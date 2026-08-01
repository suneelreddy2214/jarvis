"""Agent macro / fundamental / soft-risk gate tests."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from quantx.analysis.fundamental import FundamentalAnalyzer
from quantx.analysis.macro import MacroAnalyzer
from quantx.core.config import Settings
from quantx.core.risk import RiskManager


def test_calm_vix_is_complacency_not_full_size():
    m = MacroAnalyzer().analyze(india_vix=14.3, nifty_change_pct=0.2, usdinr_change_pct=-0.63)
    assert m.vix_regime == "complacent"
    assert m.require_extra_confirmation is True
    assert m.size_multiplier <= 0.65
    assert "vix_complacency" in m.caution_flags
    assert "inr_strong" in m.caution_flags


def test_bearish_nifty_plus_weak_inr_blocks():
    m = MacroAnalyzer().analyze(india_vix=14.0, nifty_change_pct=-1.0, usdinr_change_pct=0.5)
    assert m.avoid_new_risk is True
    assert m.size_multiplier == 0.0


def test_sparse_fundamentals_score_below_floor():
    f = FundamentalAnalyzer().analyze("INFY", {})
    assert f.data_quality == "sparse"
    assert f.score < 50


def test_rich_fundamentals_score():
    f = FundamentalAnalyzer().analyze(
        "INFY",
        {
            "trailingPE": 22,
            "priceToBook": 2.1,
            "returnOnEquity": 0.22,
            "debtToEquity": 0.3,
            "profitMargins": 0.18,
            "revenueGrowth": 0.12,
            "sector": "Technology",
        },
    )
    assert f.data_quality == "rich"
    assert f.score >= 50


def test_soft_daily_loss_halts_before_hard_limit():
    settings = Settings()
    rm = RiskManager(settings)
    # 1.2% daily loss — above soft 1%, below hard 2%
    rm.update_state(capital=1_000_000, daily_realized_pnl=-12_000, unrealized_pnl=0)
    assert rm.daily_loss_pct >= settings.risk.soft_daily_loss_pct
    assert rm.daily_loss_pct < settings.risk.max_daily_loss_pct
    st = rm.status()
    assert st.can_trade is False
    assert any("Soft daily" in r for r in st.reasons)
