"""Market data providers for NSE/BSE equities via yfinance + synthetic fallbacks."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Optional

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# Common NSE symbols for demo / watchlist
DEFAULT_WATCHLIST = [
    "RELIANCE.NS",
    "TCS.NS",
    "INFY.NS",
    "HDFCBANK.NS",
    "ICICIBANK.NS",
    "SBIN.NS",
    "BHARTIARTL.NS",
    "ITC.NS",
    "LT.NS",
    "AXISBANK.NS",
    "^NSEI",       # Nifty 50
    "^NSEBANK",    # Bank Nifty
    "^INDIAVIX",   # India VIX
]


def to_yahoo_symbol(symbol: str, exchange: str = "NSE") -> str:
    s = symbol.strip().upper()
    if s.startswith("^") or s.endswith(".NS") or s.endswith(".BO"):
        return s
    # Index aliases used by F&O paper book
    index_map = {
        "NIFTY": "^NSEI",
        "NIFTY50": "^NSEI",
        "BANKNIFTY": "^NSEBANK",
        "INDIAVIX": "^INDIAVIX",
    }
    if s in index_map:
        return index_map[s]
    if exchange.upper() == "BSE":
        return f"{s}.BO"
    return f"{s}.NS"


class MarketDataService:
    """Fetch OHLC and quotes. Falls back to deterministic synthetic data offline."""

    def __init__(self, use_live: bool = True):
        self.use_live = use_live
        self._cache: dict[str, pd.DataFrame] = {}

    def get_ohlc(
        self,
        symbol: str,
        exchange: str = "NSE",
        period: str = "6mo",
        interval: str = "1d",
    ) -> pd.DataFrame:
        ysym = to_yahoo_symbol(symbol, exchange)
        cache_key = f"{ysym}:{period}:{interval}"
        if cache_key in self._cache:
            return self._cache[cache_key].copy()

        df = None
        if self.use_live:
            try:
                import yfinance as yf

                ticker = yf.Ticker(ysym)
                df = ticker.history(period=period, interval=interval)
                if df is not None and not df.empty:
                    df = df.rename(columns={
                        "Open": "open",
                        "High": "high",
                        "Low": "low",
                        "Close": "close",
                        "Volume": "volume",
                    })
                    df = df[["open", "high", "low", "close", "volume"]].dropna()
            except Exception as e:
                logger.warning("yfinance failed for %s: %s — using synthetic", ysym, e)
                df = None

        if df is None or df.empty:
            df = self._synthetic_ohlc(ysym)

        self._cache[cache_key] = df
        return df.copy()

    def get_quote(self, symbol: str, exchange: str = "NSE") -> dict:
        df = self.get_ohlc(symbol, exchange, period="5d", interval="1d")
        last = df.iloc[-1]
        prev = df.iloc[-2] if len(df) > 1 else last
        change_pct = (float(last["close"]) - float(prev["close"])) / float(prev["close"]) * 100
        return {
            "symbol": symbol.upper().replace(".NS", "").replace(".BO", ""),
            "exchange": exchange,
            "price": float(last["close"]),
            "open": float(last["open"]),
            "high": float(last["high"]),
            "low": float(last["low"]),
            "volume": float(last["volume"]),
            "change_pct": round(change_pct, 3),
            "as_of": datetime.utcnow().isoformat() + "Z",
        }

    def get_info(self, symbol: str, exchange: str = "NSE") -> dict:
        ysym = to_yahoo_symbol(symbol, exchange)
        if not self.use_live:
            return {}
        try:
            import yfinance as yf

            info = yf.Ticker(ysym).info or {}
            keys = [
                "trailingPE", "priceToBook", "returnOnEquity", "debtToEquity",
                "profitMargins", "revenueGrowth", "sector", "industry",
                "marketCap", "dividendYield",
            ]
            return {k: info.get(k) for k in keys if info.get(k) is not None}
        except Exception as e:
            logger.warning("info fetch failed for %s: %s", ysym, e)
            return {}

    def get_macro_snapshot(self) -> dict:
        out: dict = {}
        for sym, key in [("^NSEI", "nifty"), ("^INDIAVIX", "india_vix"), ("INR=X", "usdinr")]:
            try:
                q = self.get_quote(sym, "NSE")
                if key == "india_vix":
                    out["india_vix"] = q["price"]
                elif key == "nifty":
                    out["nifty_change_pct"] = q["change_pct"]
                    out["nifty"] = q["price"]
                elif key == "usdinr":
                    out["usdinr_change_pct"] = q["change_pct"]
                    out["usdinr"] = q["price"]
            except Exception:
                continue
        return out

    def _synthetic_ohlc(self, symbol: str, bars: int = 180) -> pd.DataFrame:
        """Deterministic synthetic series for offline tests / demos."""
        seed = abs(hash(symbol)) % (2**32)
        rng = np.random.default_rng(seed)
        # Base prices by symbol family
        base = 1000.0
        if "RELIANCE" in symbol:
            base = 2800
        elif "TCS" in symbol or "INFY" in symbol:
            base = 3500
        elif "HDFC" in symbol or "ICICI" in symbol:
            base = 1600
        elif "NSEI" in symbol or symbol in ("NIFTY", "NIFTY50"):
            base = 24000
        elif "NSEBANK" in symbol or "BANKNIFTY" in symbol:
            base = 52000
        elif "VIX" in symbol:
            base = 14.0

        dates = pd.bdate_range(end=datetime.utcnow().date(), periods=bars)
        rets = rng.normal(0.0004, 0.012, size=bars)
        close = base * np.cumprod(1 + rets)
        high = close * (1 + rng.uniform(0.002, 0.015, bars))
        low = close * (1 - rng.uniform(0.002, 0.015, bars))
        open_ = close * (1 + rng.normal(0, 0.004, bars))
        volume = rng.integers(200_000, 2_000_000, bars).astype(float)
        if "VIX" in symbol:
            volume = rng.integers(1_000, 50_000, bars).astype(float)
        if "NSEI" in symbol or "NSEBANK" in symbol:
            volume = rng.integers(150_000, 800_000, bars).astype(float)

        df = pd.DataFrame(
            {"open": open_, "high": high, "low": low, "close": close, "volume": volume},
            index=dates,
        )
        df.index.name = "date"
        return df
