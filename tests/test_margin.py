"""Stocks / F&O margin book tests."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from quantx.core.config import Settings
from quantx.core.models import Position, Side, TradeType
from quantx.core.risk import RiskManager
from quantx.data.market_data import MarketDataService
from quantx.portfolio.db import Database
from quantx.portfolio.manager import PortfolioManager
from quantx.portfolio.margin import MarginCalculator, classify_product


def test_classify_products():
    assert classify_product(TradeType.SWING)[0] == "Stocks"
    assert classify_product(TradeType.INTRADAY)[2] == "EQUITY_MIS"
    assert classify_product(TradeType.FUTURES)[1] == "FO"
    assert classify_product(TradeType.OPTIONS)[0] == "Options"


def test_margin_book_stocks_and_fno(tmp_path):
    s = Settings()
    s.database = {"path": str(tmp_path / "m.db")}
    db = Database(Path(s.database["path"]))
    pm = PortfolioManager(
        db=db,
        risk=RiskManager(s),
        market_data=MarketDataService(use_live=False),
        settings=s,
    )

    stock = Position(
        symbol="RELIANCE",
        side=Side.BUY,
        trade_type=TradeType.SWING,
        quantity=10,
        entry_price=2500,
        current_price=2520,
        stop_loss=2400,
        target_1=2600,
        target_2=2700,
        capital_at_risk=1000,
        pnl=200,
    )
    fut = Position(
        symbol="NIFTY",
        side=Side.BUY,
        trade_type=TradeType.FUTURES,
        quantity=1,
        entry_price=24000,
        current_price=24100,
        stop_loss=23800,
        target_1=24500,
        target_2=24800,
        capital_at_risk=5000,
        pnl=2500,
    )
    opt = Position(
        symbol="NIFTY",
        side=Side.BUY,
        trade_type=TradeType.OPTIONS,
        quantity=1,
        entry_price=150,
        current_price=180,
        stop_loss=100,
        target_1=250,
        target_2=300,
        capital_at_risk=3750,
        pnl=750,
    )
    for p in (stock, fut, opt):
        p.id = db.insert_position(p)

    book = pm.margin_book()
    assert book["stocks_count"] == 1
    assert book["fno_count"] == 2
    assert book["by_segment"]["stocks"]["open_positions"] == 1
    assert book["by_segment"]["futures"]["open_positions"] == 1
    assert book["by_segment"]["options"]["open_positions"] == 1
    # CNC stock: 2500*10*100% = 25000
    assert book["by_segment"]["stocks"]["margin_used"] == 25000.0
    # Futures: 24000*1*25*12% = 72000
    assert book["by_segment"]["futures"]["margin_used"] == 72000.0
    # Options buy: 150*1*25*100% = 3750
    assert book["by_segment"]["options"]["margin_used"] == 3750.0
    assert book["total_margin_used"] == 25000 + 72000 + 3750
    assert book["available_margin"] == round(book["capital"] - book["total_margin_used"], 2)

    snap = pm.snapshot()
    assert snap.used_margin == book["total_margin_used"]
    assert snap.available_margin == book["available_margin"]


def test_options_sell_margin_higher_than_buy():
    calc = MarginCalculator()
    buy = Position(
        symbol="NIFTY",
        side=Side.BUY,
        trade_type=TradeType.OPTIONS,
        quantity=1,
        entry_price=100,
        current_price=100,
        stop_loss=50,
        target_1=150,
        target_2=200,
    )
    sell = Position(
        symbol="NIFTY",
        side=Side.SELL,
        trade_type=TradeType.OPTIONS,
        quantity=1,
        entry_price=100,
        current_price=100,
        stop_loss=150,
        target_1=50,
        target_2=20,
    )
    bm = calc.margin_for_position(buy)
    sm = calc.margin_for_position(sell)
    assert bm.margin_required == 100 * 25  # full premium
    assert sm.margin_required == 100 * 25 * 8.0
    assert sm.margin_required > bm.margin_required
