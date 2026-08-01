"""Yahoo Finance market-data provider tests."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from quantx.data.market_data import MarketDataService, YahooFinanceError, to_yahoo_symbol


def test_yahoo_symbol_mapping():
    assert to_yahoo_symbol("RELIANCE") == "RELIANCE.NS"
    assert to_yahoo_symbol("NIFTY") == "^NSEI"
    assert to_yahoo_symbol("BANKNIFTY") == "^NSEBANK"
    assert to_yahoo_symbol("INR=X") == "INR=X"
    assert to_yahoo_symbol("^NSEI") == "^NSEI"


def test_yahoo_only_rejects_synthetic():
    svc = MarketDataService(use_live=True, yahoo_only=True, allow_synthetic=False)

    def boom(*_a, **_k):
        raise YahooFinanceError("blocked")

    with patch.object(svc, "_yahoo_chart_ohlc", side_effect=boom), patch.object(
        svc, "_yfinance_ohlc", side_effect=boom
    ):
        with pytest.raises(YahooFinanceError):
            svc.get_ohlc("RELIANCE", "NSE", period="5d")


def test_offline_tests_still_get_synthetic():
    svc = MarketDataService(use_live=False, yahoo_only=False, allow_synthetic=True)
    df = svc.get_ohlc("RELIANCE", "NSE", period="5d")
    assert not df.empty
    assert svc.last_source == "synthetic"


def test_live_yahoo_quote_when_network_allows():
    svc = MarketDataService(use_live=True, yahoo_only=True, allow_synthetic=False)
    try:
        q = svc.get_quote("RELIANCE", "NSE")
    except YahooFinanceError:
        pytest.skip("Yahoo Finance unreachable in this environment")
    assert q["price"] > 0
    assert q.get("source") in ("yahoo_finance", "yahoo_chart", "yahoo_cache", "yfinance")
    assert q.get("yahoo_symbol") == "RELIANCE.NS"
