"""F&O search + option chain (expiry / CE / PE) tests."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from quantx.analysis.option_chain import FnoSearchService, list_expiries
from quantx.data.market_data import MarketDataService


def test_expiries_index_and_stock():
    nifty = list_expiries("NIFTY", count=4)
    assert len(nifty) >= 3
    assert all("expiry" in e and "label" in e for e in nifty)
    assert any(e["kind"] in ("weekly", "monthly") for e in nifty)
    stock = list_expiries("RELIANCE", count=3)
    assert len(stock) >= 2
    assert all(e["kind"] == "monthly" for e in stock)


def test_option_chain_has_calls_and_puts():
    svc = FnoSearchService(MarketDataService(use_live=False))
    ov = svc.overview("NIFTY")
    assert ov["spot"] > 0
    assert ov["expiries"]
    chain = svc.chain("NIFTY", ov["default_expiry"])
    assert chain["rows"]
    assert all("call" in r and "put" in r for r in chain["rows"])
    assert all(r["call"]["type"] == "CE" and r["put"]["type"] == "PE" for r in chain["rows"])
    assert chain["lot_size"] == 25
    assert any(r["is_atm"] for r in chain["rows"])


def test_search_symbols():
    svc = FnoSearchService(MarketDataService(use_live=False))
    res = svc.search("HDFC")
    assert any(r["symbol"] == "HDFCBANK" for r in res)
