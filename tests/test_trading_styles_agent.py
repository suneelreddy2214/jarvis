"""Trading styles catalog → scan/paper agent wiring."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from quantx.analysis.trading_styles import (  # noqa: E402
    TRADING_STYLES,
    resolve_style_ids,
    style_strategy_jobs,
)
from quantx.core.config import Settings  # noqa: E402
from quantx.core.engine import QuantXEngine  # noqa: E402
from quantx.core.risk import RiskManager  # noqa: E402
from quantx.data.market_data import MarketDataService  # noqa: E402
from quantx.execution.broker import PaperBroker  # noqa: E402
from quantx.execution.paper_agent import PaperTradingAgent  # noqa: E402
from quantx.portfolio.db import Database  # noqa: E402
from quantx.portfolio.manager import PortfolioManager  # noqa: E402


def test_catalog_covers_attached_styles():
    ids = {s.id for s in TRADING_STYLES}
    expected = {
        "scalping",
        "intraday",
        "momentum",
        "swing",
        "position",
        "trend",
        "breakout",
        "mean_reversion",
        "options",
        "futures",
        "arbitrage",
        "pair_trading",
        "event_driven",
        "news_based",
        "quantitative",
        "algorithmic",
        "long_term",
        "dividend",
        "value",
        "growth",
        "mutual_fund",
        "etf",
    }
    missing = expected - ids
    assert not missing, f"Missing styles from catalog: {missing}"
    assert len(TRADING_STYLES) >= 20


def test_resolve_all_and_single():
    all_styles = resolve_style_ids(["all"])
    assert len(all_styles) == len(TRADING_STYLES)
    swing = resolve_style_ids(["swing"])
    assert len(swing) == 1
    assert swing[0].id == "swing"


def test_style_jobs_include_scalping_and_investing():
    jobs = style_strategy_jobs(["scalping", "etf", "swing"], limit_per_style=3)
    styles = {j["style_id"] for j in jobs}
    assert "scalping" in styles
    assert "swing" in styles
    tts = {j["trade_type"] for j in jobs}
    assert "INTRADAY" in tts or "FUTURES" in tts or "OPTIONS" in tts
    assert "SWING" in tts


def test_paper_agent_accepts_style_ids(tmp_path):
    s = Settings()
    s.database = {"path": str(tmp_path / "styles.db")}
    s.broker = {"name": "paper", "slippage_bps": 5}
    s.agent.mode = "paper"
    db = Database(Path(s.database["path"]))
    data = MarketDataService(use_live=False)
    risk = RiskManager(s)
    pm = PortfolioManager(db=db, risk=risk, market_data=data, settings=s)
    engine = QuantXEngine(settings=s, risk_manager=risk, market_data=data)
    broker = PaperBroker(portfolio=pm, db=db, settings=s)
    agent = PaperTradingAgent(portfolio=pm, engine=engine, broker=broker, settings=s)

    out = agent.start(
        symbols=["RELIANCE", "TCS"],
        interval_sec=120,
        auto_execute=False,
        enable_fno=True,
        style_ids=["scalping", "swing", "momentum"],
    )
    assert out["ok"] is True
    assert set(agent.stats.style_ids) >= {"scalping", "swing", "momentum"}
    assert "INTRADAY" in agent.stats.trade_types or "SWING" in agent.stats.trade_types
    assert agent.stats.use_regime_selector is False
    strats = agent._strategies_for("INTRADAY", agent._detect_regime())
    # Scalping/momentum may yield intraday strategies
    assert isinstance(strats, list)
    agent.stop()
