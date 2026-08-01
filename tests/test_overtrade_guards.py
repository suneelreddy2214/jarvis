"""Anti-overtrading pause + prioritized queue tests."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from quantx.core.config import Settings
from quantx.core.risk import RiskManager
from quantx.portfolio.db import Database
from quantx.portfolio.manager import PortfolioManager
from quantx.core.risk import RiskManager as RM
from quantx.data.market_data import MarketDataService


def test_loss_pause_arms_after_one_loss():
    settings = Settings()
    rm = RiskManager(settings)
    rm.update_state(capital=1_000_000)
    rm.register_trade_result(-1000)
    assert rm.state.consecutive_losses == 1
    assert rm.state.loss_pause_until is not None
    st = rm.status()
    assert st.can_trade is False
    assert any("Post-loss pause" in r for r in st.reasons)


def test_win_clears_loss_pause():
    settings = Settings()
    rm = RiskManager(settings)
    rm.update_state(capital=1_000_000)
    rm.register_trade_result(-500)
    assert rm.state.loss_pause_until is not None
    rm.register_trade_result(800)
    assert rm.state.consecutive_losses == 0
    assert rm.state.loss_pause_until is None


def test_chase_cooldown_roundtrip(tmp_path):
    settings = Settings()
    db = Database(path=tmp_path / "c.db")
    pm = PortfolioManager(
        db=db,
        risk=RM(settings),
        market_data=MarketDataService(use_live=False, allow_synthetic=True),
        settings=settings,
    )
    assert pm.chase_on_cooldown("RELIANCE") is False
    pm.set_chase_cooldown("RELIANCE", minutes=60)
    assert pm.chase_on_cooldown("RELIANCE") is True
