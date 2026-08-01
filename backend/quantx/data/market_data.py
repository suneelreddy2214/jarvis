"""
Market data for QuantX — Yahoo Finance only for paper trading.

Uses Yahoo chart API (query1/query2) with a browser User-Agent.
yfinance is a secondary path. Synthetic OHLC is test-only (allow_synthetic).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional

import numpy as np
import pandas as pd
import requests

logger = logging.getLogger(__name__)

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
    "^NSEI",
    "^NSEBANK",
    "^INDIAVIX",
]

YAHOO_CHART_HOSTS = (
    "https://query1.finance.yahoo.com",
    "https://query2.finance.yahoo.com",
)

YAHOO_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

# Symbols that must not get an exchange suffix
_PASSTHROUGH = {"INR=X", "USDINR=X"}


class YahooFinanceError(RuntimeError):
    """Raised when Yahoo Finance cannot supply a price in yahoo-only mode."""


def to_yahoo_symbol(symbol: str, exchange: str = "NSE") -> str:
    s = symbol.strip().upper()
    if s in _PASSTHROUGH or s.startswith("^") or s.endswith(".NS") or s.endswith(".BO") or "=" in s:
        # FX pairs like INR=X must stay as-is (never append .NS)
        if s.endswith(".NS") and "=" in s.replace(".NS", ""):
            return s.replace(".NS", "")
        return s
    index_map = {
        "NIFTY": "^NSEI",
        "NIFTY50": "^NSEI",
        "BANKNIFTY": "^NSEBANK",
        "INDIAVIX": "^INDIAVIX",
        "VIX": "^INDIAVIX",
        "USDINR": "INR=X",
        # Yahoo NSE renames
        "ZOMATO": "ETERNAL.NS",
    }
    if s in index_map:
        return index_map[s]
    if exchange.upper() == "BSE":
        return f"{s}.BO"
    return f"{s}.NS"


def _range_for_period(period: str) -> str:
    p = (period or "6mo").lower()
    mapping = {
        "1d": "1d",
        "5d": "5d",
        "1mo": "1mo",
        "3mo": "3mo",
        "6mo": "6mo",
        "1y": "1y",
        "2y": "2y",
        "5y": "5y",
        "10y": "10y",
        "ytd": "ytd",
        "max": "max",
    }
    return mapping.get(p, "6mo")


class MarketDataService:
    """
    Yahoo Finance market data for stocks, indices, and F&O underlyings.

    Paper trading default: yahoo_only=True → never serve synthetic prices.
    Unit tests may pass use_live=False / allow_synthetic=True.
    """

    def __init__(
        self,
        use_live: bool = True,
        yahoo_only: Optional[bool] = None,
        allow_synthetic: Optional[bool] = None,
        quote_cache_ttl_sec: float = 60.0,
        ohlc_cache_ttl_sec: float = 300.0,
        session: Optional[requests.Session] = None,
    ):
        self.use_live = use_live
        # Paper default: Yahoo only. Offline tests set use_live=False → synthetic allowed.
        if yahoo_only is None:
            yahoo_only = bool(use_live)
        if allow_synthetic is None:
            allow_synthetic = not bool(use_live)
        self.yahoo_only = bool(yahoo_only)
        self.allow_synthetic = bool(allow_synthetic)
        self.quote_cache_ttl_sec = float(quote_cache_ttl_sec)
        self.ohlc_cache_ttl_sec = float(ohlc_cache_ttl_sec)
        self._cache: dict[str, tuple[float, pd.DataFrame]] = {}
        self._quote_cache: dict[str, tuple[float, dict]] = {}
        self._session = session or requests.Session()
        self._session.headers.update(
            {
                "User-Agent": YAHOO_UA,
                "Accept": "application/json,text/plain,*/*",
            }
        )
        self.last_source: str = "unknown"
        self.last_error: Optional[str] = None
        self._yahoo_crumb: Optional[str] = None
        self._crumb_fetched_at: float = 0.0

    def _ensure_yahoo_crumb(self, force: bool = False) -> Optional[str]:
        """Yahoo quoteSummary requires a crumb + cookie (A3)."""
        now = time.time()
        if not force and self._yahoo_crumb and now - self._crumb_fetched_at < 3600:
            return self._yahoo_crumb
        try:
            self._session.get("https://fc.yahoo.com", timeout=10)
            resp = self._session.get(
                "https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=10
            )
            if resp.status_code == 200 and resp.text and "Too Many" not in resp.text:
                self._yahoo_crumb = resp.text.strip()
                self._crumb_fetched_at = now
                return self._yahoo_crumb
        except Exception as e:
            logger.warning("Yahoo crumb fetch failed: %s", e)
        return self._yahoo_crumb

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def clear_cache(self) -> None:
        self._cache.clear()
        self._quote_cache.clear()

    def provider_status(self) -> dict[str, Any]:
        return {
            "provider": "yahoo_finance",
            "yahoo_only": self.yahoo_only,
            "allow_synthetic": self.allow_synthetic,
            "use_live": self.use_live,
            "last_source": self.last_source,
            "last_error": self.last_error,
            "quote_cache_ttl_sec": self.quote_cache_ttl_sec,
            "ohlc_cache_ttl_sec": self.ohlc_cache_ttl_sec,
        }

    def get_ohlc(
        self,
        symbol: str,
        exchange: str = "NSE",
        period: str = "6mo",
        interval: str = "1d",
    ) -> pd.DataFrame:
        ysym = to_yahoo_symbol(symbol, exchange)
        cache_key = f"{ysym}:{period}:{interval}"
        now = time.time()
        hit = self._cache.get(cache_key)
        if hit and now - hit[0] < self.ohlc_cache_ttl_sec:
            self.last_source = "yahoo_cache"
            return hit[1].copy()

        df: Optional[pd.DataFrame] = None
        err: Optional[str] = None

        if self.use_live:
            try:
                df = self._yahoo_chart_ohlc(ysym, period=period, interval=interval)
                if df is not None and not df.empty:
                    self.last_source = "yahoo_chart"
                    self.last_error = None
            except Exception as e:
                err = str(e)
                logger.warning("Yahoo chart OHLC failed for %s: %s", ysym, e)
                df = None

            if df is None or df.empty:
                try:
                    df = self._yfinance_ohlc(ysym, period=period, interval=interval)
                    if df is not None and not df.empty:
                        self.last_source = "yfinance"
                        self.last_error = None
                except Exception as e:
                    err = f"{err + '; ' if err else ''}{e}"
                    logger.warning("yfinance OHLC failed for %s: %s", ysym, e)
                    df = None

        if df is None or df.empty:
            self.last_error = err or f"No Yahoo data for {ysym}"
            if self.yahoo_only and not self.allow_synthetic:
                raise YahooFinanceError(
                    f"Yahoo Finance unavailable for {ysym}. "
                    f"Paper trading is configured for Yahoo only (no synthetic prices). "
                    f"Detail: {self.last_error}"
                )
            df = self._synthetic_ohlc(ysym)
            self.last_source = "synthetic"

        self._cache[cache_key] = (now, df)
        return df.copy()

    def get_quote(self, symbol: str, exchange: str = "NSE") -> dict:
        ysym = to_yahoo_symbol(symbol, exchange)
        now = time.time()
        hit = self._quote_cache.get(ysym)
        if hit and now - hit[0] < self.quote_cache_ttl_sec:
            self.last_source = "yahoo_cache"
            return dict(hit[1])

        quote: Optional[dict] = None
        err: Optional[str] = None

        if self.use_live:
            try:
                quote = self._yahoo_chart_quote(ysym)
            except Exception as e:
                err = str(e)
                logger.warning("Yahoo chart quote failed for %s: %s", ysym, e)

            if quote is None:
                try:
                    df = self.get_ohlc(symbol, exchange, period="5d", interval="1d")
                    last = df.iloc[-1]
                    prev = df.iloc[-2] if len(df) > 1 else last
                    change_pct = (
                        (float(last["close"]) - float(prev["close"])) / float(prev["close"]) * 100
                        if float(prev["close"])
                        else 0.0
                    )
                    quote = {
                        "symbol": symbol.upper().replace(".NS", "").replace(".BO", ""),
                        "exchange": exchange,
                        "yahoo_symbol": ysym,
                        "price": float(last["close"]),
                        "open": float(last["open"]),
                        "high": float(last["high"]),
                        "low": float(last["low"]),
                        "volume": float(last["volume"]),
                        "change_pct": round(change_pct, 3),
                        "as_of": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                        "source": self.last_source,
                    }
                except Exception as e:
                    err = f"{err + '; ' if err else ''}{e}"

        if quote is None:
            self.last_error = err or f"No Yahoo quote for {ysym}"
            if self.yahoo_only and not self.allow_synthetic:
                raise YahooFinanceError(
                    f"Yahoo Finance quote unavailable for {ysym}. "
                    f"Paper trading is Yahoo-only. Detail: {self.last_error}"
                )
            # Synthetic quote path for tests
            df = self._synthetic_ohlc(ysym, bars=5)
            last = df.iloc[-1]
            prev = df.iloc[-2]
            change_pct = (float(last["close"]) - float(prev["close"])) / float(prev["close"]) * 100
            quote = {
                "symbol": symbol.upper().replace(".NS", "").replace(".BO", ""),
                "exchange": exchange,
                "yahoo_symbol": ysym,
                "price": float(last["close"]),
                "open": float(last["open"]),
                "high": float(last["high"]),
                "low": float(last["low"]),
                "volume": float(last["volume"]),
                "change_pct": round(change_pct, 3),
                "as_of": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                "source": "synthetic",
            }
            self.last_source = "synthetic"

        self._quote_cache[ysym] = (now, quote)
        return dict(quote)

    def get_info(self, symbol: str, exchange: str = "NSE") -> dict:
        ysym = to_yahoo_symbol(symbol, exchange)
        if not self.use_live:
            return {}
        # Prefer Yahoo quoteSummary modules (more reliable than yfinance .info under 429s)
        try:
            info = self._yahoo_quote_summary(ysym)
            if info:
                return info
        except Exception as e:
            logger.warning("Yahoo quoteSummary failed for %s: %s", ysym, e)
        try:
            import yfinance as yf

            info = yf.Ticker(ysym).info or {}
            keys = [
                "trailingPE",
                "priceToBook",
                "returnOnEquity",
                "debtToEquity",
                "profitMargins",
                "revenueGrowth",
                "sector",
                "industry",
                "marketCap",
                "dividendYield",
            ]
            return {k: info.get(k) for k in keys if info.get(k) is not None}
        except Exception as e:
            logger.warning("info fetch failed for %s: %s", ysym, e)
            return {}

    def _yahoo_quote_summary(self, ysym: str) -> dict:
        modules = "summaryDetail,defaultKeyStatistics,financialData,assetProfile"
        last_err: Optional[Exception] = None
        crumb = self._ensure_yahoo_crumb()
        for host in YAHOO_CHART_HOSTS:
            url = f"{host}/v10/finance/quoteSummary/{ysym}"
            try:
                params: dict[str, str] = {"modules": modules}
                if crumb:
                    params["crumb"] = crumb
                resp = self._session.get(url, params=params, timeout=15)
                if resp.status_code in (401, 403) and crumb:
                    crumb = self._ensure_yahoo_crumb(force=True)
                    if crumb:
                        params["crumb"] = crumb
                        resp = self._session.get(url, params=params, timeout=15)
                if resp.status_code == 429:
                    time.sleep(0.4)
                    continue
                if resp.status_code >= 400:
                    last_err = YahooFinanceError(f"quoteSummary HTTP {resp.status_code}")
                    continue
                data = resp.json()
                result = ((data.get("quoteSummary") or {}).get("result") or [None])[0]
                if not result:
                    continue
                summary = result.get("summaryDetail") or {}
                keystat = result.get("defaultKeyStatistics") or {}
                financial = result.get("financialData") or {}
                profile = result.get("assetProfile") or {}

                def _raw(node, field):
                    block = node.get(field)
                    if isinstance(block, dict):
                        return block.get("raw", block.get("fmt"))
                    return block

                out = {}
                pe = _raw(summary, "trailingPE") or _raw(keystat, "trailingPE")
                pb = _raw(keystat, "priceToBook")
                roe = _raw(financial, "returnOnEquity")
                de = _raw(financial, "debtToEquity")
                margin = _raw(financial, "profitMargins")
                growth = _raw(financial, "revenueGrowth")
                mcap = _raw(summary, "marketCap")
                div = _raw(summary, "dividendYield")
                if pe is not None:
                    out["trailingPE"] = float(pe)
                if pb is not None:
                    out["priceToBook"] = float(pb)
                if roe is not None:
                    out["returnOnEquity"] = float(roe)
                if de is not None:
                    # Yahoo financialData.debtToEquity is percent-style (e.g. 9.5 → 0.095)
                    out["debtToEquity"] = float(de) / 100.0
                if margin is not None:
                    out["profitMargins"] = float(margin)
                if growth is not None:
                    out["revenueGrowth"] = float(growth)
                if mcap is not None:
                    out["marketCap"] = float(mcap)
                if div is not None:
                    out["dividendYield"] = float(div)
                if profile.get("sector"):
                    out["sector"] = profile["sector"]
                if profile.get("industry"):
                    out["industry"] = profile["industry"]
                return out
            except Exception as e:
                last_err = e
                continue
        if last_err:
            raise YahooFinanceError(str(last_err))
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
                out[f"{key}_source"] = q.get("source", self.last_source)
            except Exception as e:
                logger.warning("macro quote failed for %s: %s", sym, e)
                continue
        return out

    # ------------------------------------------------------------------
    # Yahoo Finance chart API
    # ------------------------------------------------------------------

    def _yahoo_chart_raw(self, ysym: str, period: str, interval: str) -> dict:
        rng = _range_for_period(period)
        params = {"range": rng, "interval": interval, "includePrePost": "false"}
        last_err: Optional[Exception] = None
        for host in YAHOO_CHART_HOSTS:
            url = f"{host}/v8/finance/chart/{ysym}"
            try:
                resp = self._session.get(url, params=params, timeout=15)
                if resp.status_code == 429:
                    last_err = YahooFinanceError(f"Yahoo rate-limited (429) for {ysym}")
                    time.sleep(0.4)
                    continue
                resp.raise_for_status()
                data = resp.json()
                err = (data.get("chart") or {}).get("error")
                if err:
                    raise YahooFinanceError(str(err))
                result = (data.get("chart") or {}).get("result") or []
                if not result:
                    raise YahooFinanceError(f"Empty chart result for {ysym}")
                return result[0]
            except Exception as e:
                last_err = e
                continue
        raise YahooFinanceError(str(last_err) if last_err else f"Yahoo chart failed for {ysym}")

    def _yahoo_chart_ohlc(self, ysym: str, period: str, interval: str) -> pd.DataFrame:
        result = self._yahoo_chart_raw(ysym, period=period, interval=interval)
        ts = result.get("timestamp") or []
        quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        if not ts or not quote:
            raise YahooFinanceError(f"No OHLC bars for {ysym}")
        df = pd.DataFrame(
            {
                "open": quote.get("open") or [],
                "high": quote.get("high") or [],
                "low": quote.get("low") or [],
                "close": quote.get("close") or [],
                "volume": quote.get("volume") or [],
            },
            index=pd.to_datetime(ts, unit="s", utc=True).tz_convert("Asia/Kolkata").tz_localize(None),
        )
        df.index.name = "date"
        df = df.dropna(subset=["close"])
        if df.empty:
            raise YahooFinanceError(f"All-NaN OHLC for {ysym}")
        return df

    def _yahoo_chart_quote(self, ysym: str) -> dict:
        result = self._yahoo_chart_raw(ysym, period="5d", interval="1d")
        meta = result.get("meta") or {}
        price = meta.get("regularMarketPrice")
        if price is None:
            # fall back to last close bar
            quote = ((result.get("indicators") or {}).get("quote") or [{}])[0]
            closes = [c for c in (quote.get("close") or []) if c is not None]
            if not closes:
                raise YahooFinanceError(f"No regularMarketPrice for {ysym}")
            price = closes[-1]
        prev_close = meta.get("chartPreviousClose") or meta.get("previousClose")
        if prev_close:
            change_pct = (float(price) - float(prev_close)) / float(prev_close) * 100
        else:
            change_pct = 0.0
        ts = result.get("timestamp") or []
        qbars = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        opens = qbars.get("open") or []
        highs = qbars.get("high") or []
        lows = qbars.get("low") or []
        vols = qbars.get("volume") or []

        def _last(arr, default=0.0):
            for v in reversed(arr or []):
                if v is not None:
                    return float(v)
            return float(default)

        display = ysym.replace(".NS", "").replace(".BO", "").replace("^", "")
        if display in ("NSEI",):
            display = "NIFTY"
        elif display in ("NSEBANK",):
            display = "BANKNIFTY"
        elif display in ("INDIAVIX",):
            display = "INDIAVIX"

        out = {
            "symbol": display,
            "exchange": "NSE",
            "yahoo_symbol": ysym,
            "price": float(price),
            "open": _last(opens, price),
            "high": _last(highs, price),
            "low": _last(lows, price),
            "volume": _last(vols, 0),
            "change_pct": round(float(change_pct), 3),
            "currency": meta.get("currency") or "INR",
            "as_of": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "source": "yahoo_finance",
            "exchange_name": meta.get("exchangeName") or meta.get("fullExchangeName"),
        }
        self.last_source = "yahoo_finance"
        self.last_error = None
        return out

    def _yfinance_ohlc(self, ysym: str, period: str, interval: str) -> pd.DataFrame:
        import yfinance as yf

        ticker = yf.Ticker(ysym)
        df = ticker.history(period=period, interval=interval)
        if df is None or df.empty:
            raise YahooFinanceError(f"yfinance empty for {ysym}")
        df = df.rename(
            columns={
                "Open": "open",
                "High": "high",
                "Low": "low",
                "Close": "close",
                "Volume": "volume",
            }
        )
        return df[["open", "high", "low", "close", "volume"]].dropna()

    def _synthetic_ohlc(self, symbol: str, bars: int = 180) -> pd.DataFrame:
        """Deterministic synthetic series — tests / explicit offline only."""
        seed = abs(hash(symbol)) % (2**32)
        rng = np.random.default_rng(seed)
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
