"""Additive strategies + paper capital adjust tests."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

import quantx.analysis.additional_strategies  # noqa: F401
from quantx.analysis.strategies import STRATEGIES, list_strategies
from quantx.core.config import Settings
from quantx.core.risk import RiskManager
from quantx.data.market_data import MarketDataService
from quantx.portfolio.db import Database
from quantx.portfolio.manager import PortfolioManager


CORE = {
    "swing_trend",
    "intraday_mean_reversion",
    "breakout",
    "futures_trend",
    "options_directional",
}


def test_core_strategies_preserved():
    for name in CORE:
        assert name in STRATEGIES


def test_additional_strategies_registered():
    names = {s["name"] for s in list_strategies()}
    assert CORE.issubset(names)
    assert "ema_cross_9_21" in names
    assert "supertrend" in names
    assert "donchian_breakout" in names
    assert "smc_bos_fvg" in names
    assert len(names) >= 20


def test_paper_capital_increase_decrease(tmp_path):
    s = Settings()
    s.database = {"path": str(tmp_path / "cap.db")}
    pm = PortfolioManager(
        db=Database(Path(s.database["path"])),
        risk=RiskManager(s),
        market_data=MarketDataService(use_live=False),
        settings=s,
    )
    before = pm.snapshot().capital
    up = pm.adjust_capital(delta=250_000, reason="test deposit")
    assert up["after"] == before + 250_000
    # Deposit should not inflate trading PnL
    assert abs(pm.snapshot().total_pnl) < 1.0
    down = pm.adjust_capital(delta=-100_000, reason="test withdraw")
    assert down["after"] == before + 150_000
    assert abs(pm.snapshot().total_pnl) < 1.0
