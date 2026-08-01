"""F&O paper trading — futures/options recommendations and mixed cycles."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from quantx.analysis.fno import (
    encode_fo_meta,
    mark_option_premium,
    paper_option_levels,
    parse_fo_meta,
)
from quantx.analysis.strategies import list_strategies
from quantx.core.config import Settings
from quantx.core.engine import QuantXEngine
from quantx.core.models import Side, TradeType
from quantx.core.risk import RiskManager
from quantx.data.market_data import MarketDataService
from quantx.execution.paper_agent import PaperTradingAgent
from quantx.execution.broker import PaperBroker
from quantx.portfolio.db import Database
from quantx.portfolio.manager import PortfolioManager


def _stack(tmp_path):
    s = Settings()
    s.database = {"path": str(tmp_path / "fno.db")}
    s.broker = {"name": "paper", "slippage_bps": 5}
    s.agent.mode = "paper"
    s.paper = {"enable_fno": True, "max_fo_positions": 2}
    s.entry.min_confidence = 40  # allow more setups in synthetic data
    s.entry.min_fundamental_score = 40
    s.entry.require_volume = False
    db = Database(Path(s.database["path"]))
    data = MarketDataService(use_live=False)
    risk = RiskManager(s)
    pm = PortfolioManager(db=db, risk=risk, market_data=data, settings=s)
    engine = QuantXEngine(settings=s, risk_manager=risk, market_data=data)
    broker = PaperBroker(portfolio=pm, db=db, settings=s)
    agent = PaperTradingAgent(portfolio=pm, engine=engine, broker=broker, settings=s)
    return agent, engine, pm, s


def test_fno_strategies_registered():
    names = {s["name"] for s in list_strategies()}
    assert "futures_trend" in names
    assert "options_directional" in names


def test_paper_option_helpers():
    levels = paper_option_levels(100)
    assert levels["risk_reward"] >= 2.0
    assert levels["stop_loss"] < levels["entry"] < levels["target_1"]
    meta = encode_fo_meta(24000, "CE", 24000)
    parsed = parse_fo_meta(f"setup {meta} more")
    assert parsed is not None
    assert parsed["kind"] == "CE"
    prem = mark_option_premium(100, 24000, 24100, "CE")
    assert prem > 100


def test_futures_recommendation(tmp_path):
    _, engine, _, _ = _stack(tmp_path)
    rec = engine.analyze_symbol("NIFTY", trade_type=TradeType.FUTURES, strategy="futures_trend")
    assert rec.trade_type == TradeType.FUTURES
    assert rec.symbol == "NIFTY"
    # May be invalid on flat synthetic series — still must be typed futures
    if rec.valid:
        assert rec.quantity >= 1
        assert "FUTURES" in rec.reason or "futures" in (rec.reason + " ".join(rec.supporting_indicators)).lower()


def test_options_recommendation(tmp_path):
    _, engine, _, _ = _stack(tmp_path)
    rec = engine.analyze_symbol("NIFTY", trade_type=TradeType.OPTIONS, strategy="options_directional")
    assert rec.trade_type == TradeType.OPTIONS
    if rec.valid:
        assert rec.side == Side.BUY  # long premium only
        assert rec.quantity >= 1
        assert parse_fo_meta(rec.reason) is not None


def test_mixed_fno_cycle(tmp_path):
    agent, _, pm, _ = _stack(tmp_path)
    agent.stats.enable_fno = True
    agent.stats.trade_types = ["SWING", "FUTURES", "OPTIONS"]
    agent.stats.symbols = ["RELIANCE", "TCS"]
    agent.stats.fo_symbols = ["NIFTY", "BANKNIFTY"]
    agent.stats.auto_execute = True
    result = agent.run_once()
    assert "message" in result
    assert result.get("enable_fno") is True
    assert "FUTURES" in (result.get("trade_types") or [])
    # Margin book always present
    book = pm.margin_book()
    assert "by_segment" in book
    assert "futures" in book["by_segment"]
    assert "options" in book["by_segment"]


def test_same_symbol_equity_and_futures_allowed(tmp_path):
    _, engine, pm, _ = _stack(tmp_path)
    # Force-open a swing position then a futures on same symbol if both valid
    swing = engine.analyze_symbol("RELIANCE", trade_type=TradeType.SWING)
    fut = engine.analyze_symbol("RELIANCE", trade_type=TradeType.FUTURES, strategy="futures_trend")
    opened = 0
    if swing.valid and swing.quantity > 0:
        pm.open_from_recommendation(swing)
        opened += 1
    if fut.valid and fut.quantity > 0:
        pm.open_from_recommendation(fut)
        opened += 1
    opens = pm.db.list_positions("OPEN")
    types = {p.trade_type for p in opens}
    if opened == 2:
        assert TradeType.SWING in types and TradeType.FUTURES in types
    # At minimum duplicate same trade_type is blocked
    if swing.valid and swing.quantity > 0 and any(p.trade_type == TradeType.SWING for p in opens):
        try:
            pm.open_from_recommendation(swing)
            assert False, "should block averaging"
        except ValueError as e:
            assert "averaging" in str(e).lower() or "Refusing" in str(e)
