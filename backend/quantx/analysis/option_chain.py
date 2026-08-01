"""Paper F&O option chain — expiries, Call/Put quotes for dashboard search."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

import math

from quantx.analysis.fno import DEFAULT_FO_UNIVERSE, is_index, lot_multiplier
from quantx.core.models import TradeType
from quantx.data.market_data import MarketDataService

# Common NSE cash + F&O searchable universe (paper)
SEARCH_UNIVERSE = sorted(
    set(
        [
            "RELIANCE",
            "TCS",
            "INFY",
            "HDFCBANK",
            "ICICIBANK",
            "SBIN",
            "BHARTIARTL",
            "ITC",
            "LT",
            "AXISBANK",
            "KOTAKBANK",
            "HINDUNILVR",
            "BAJFINANCE",
            "ASIANPAINT",
            "MARUTI",
            "TITAN",
            "SUNPHARMA",
            "WIPRO",
            "ULTRACEMCO",
            "NESTLEIND",
            "POWERGRID",
            "NTPC",
            "ONGC",
            "COALINDIA",
            "TATAMOTORS",
            "TATASTEEL",
            "JSWSTEEL",
            "ADANIENT",
            "ADANIPORTS",
            "NIFTY",
            "BANKNIFTY",
        ]
        + list(DEFAULT_FO_UNIVERSE)
    )
)


def _next_thursday(from_day: date) -> date:
    # NSE weekly index options expire Thursday
    days = (3 - from_day.weekday()) % 7
    if days == 0:
        days = 7
    return from_day + timedelta(days=days)


def _last_thursday(year: int, month: int) -> date:
    if month == 12:
        nxt = date(year + 1, 1, 1)
    else:
        nxt = date(year, month + 1, 1)
    d = nxt - timedelta(days=1)
    while d.weekday() != 3:  # Thursday
        d -= timedelta(days=1)
    return d


def list_expiries(symbol: str, as_of: Optional[date] = None, count: int = 6) -> list[dict]:
    """Return upcoming weekly + monthly expiry dates (paper NSE calendar)."""
    today = as_of or datetime.utcnow().date()
    out: list[dict] = []
    seen: set[str] = set()

    # Weeklies for indices; monthlies for all
    index = is_index(symbol)
    cur = today
    while len(out) < count:
        if index:
            th = _next_thursday(cur)
            key = th.isoformat()
            if key not in seen and th >= today:
                monthly = _last_thursday(th.year, th.month)
                kind = "monthly" if th == monthly else "weekly"
                out.append(
                    {
                        "expiry": key,
                        "label": th.strftime("%d-%b-%Y"),
                        "kind": kind,
                        "days_to_expiry": (th - today).days,
                    }
                )
                seen.add(key)
            cur = th
        else:
            # Stock options: monthly last Thursday
            y, m = cur.year, cur.month
            th = _last_thursday(y, m)
            if th < today:
                if m == 12:
                    y, m = y + 1, 1
                else:
                    m += 1
                th = _last_thursday(y, m)
            key = th.isoformat()
            if key not in seen:
                out.append(
                    {
                        "expiry": key,
                        "label": th.strftime("%d-%b-%Y"),
                        "kind": "monthly",
                        "days_to_expiry": (th - today).days,
                    }
                )
                seen.add(key)
            if m == 12:
                cur = date(y + 1, 1, 15)
            else:
                cur = date(y, m + 1, 15)

    return out[:count]


def _strike_step(symbol: str, spot: float) -> float:
    s = symbol.upper()
    if s in ("NIFTY", "NIFTY50"):
        return 50.0
    if s == "BANKNIFTY":
        return 100.0
    if spot >= 5000:
        return 100.0
    if spot >= 1000:
        return 20.0
    if spot >= 200:
        return 5.0
    return 2.5


def _bs_approx_premium(spot: float, strike: float, dte: int, iv: float, is_call: bool) -> float:
    """Rough Black-Scholes-ish premium for paper chain display."""
    t = max(dte, 1) / 365.0
    if t <= 0 or spot <= 0 or strike <= 0:
        return 0.05
    # Intrinsic + time value proxy
    if is_call:
        intrinsic = max(0.0, spot - strike)
    else:
        intrinsic = max(0.0, strike - spot)
    moneyness = abs(spot - strike) / spot
    time_val = spot * iv * math.sqrt(t) * 0.4 * math.exp(-3.0 * moneyness)
    return round(max(0.05, intrinsic + time_val), 2)


def build_option_chain(
    symbol: str,
    spot: float,
    expiry: str,
    atr: Optional[float] = None,
    strikes_each_side: int = 8,
) -> dict:
    today = datetime.utcnow().date()
    exp = date.fromisoformat(expiry)
    dte = max(0, (exp - today).days)
    step = _strike_step(symbol, spot)
    atm = round(spot / step) * step
    iv_base = 0.18
    if atr and spot > 0:
        iv_base = max(0.12, min(0.55, (atr / spot) * math.sqrt(252)))

    rows = []
    for i in range(-strikes_each_side, strikes_each_side + 1):
        strike = round(atm + i * step, 2)
        # IV smile: higher OTM
        moneyness = abs(spot - strike) / spot
        iv = round(iv_base * (1.0 + 1.5 * moneyness), 4)
        call_ltp = _bs_approx_premium(spot, strike, dte, iv, True)
        put_ltp = _bs_approx_premium(spot, strike, dte, iv, False)
        # Synthetic OI peaks near ATM
        oi_scale = max(1, int(10000 * math.exp(-8 * moneyness * moneyness)))
        call_oi = int(oi_scale * (0.9 + 0.2 * (i % 3)))
        put_oi = int(oi_scale * (1.1 - 0.15 * (i % 3)))
        rows.append(
            {
                "strike": strike,
                "call": {
                    "type": "CE",
                    "ltp": call_ltp,
                    "iv": round(iv * 100, 2),
                    "oi": call_oi,
                    "volume": int(call_oi * 0.08),
                    "change": round(call_ltp * 0.01 * (1 if i <= 0 else -1), 2),
                },
                "put": {
                    "type": "PE",
                    "ltp": put_ltp,
                    "iv": round(iv * 100 * 1.02, 2),
                    "oi": put_oi,
                    "volume": int(put_oi * 0.07),
                    "change": round(put_ltp * 0.01 * (1 if i >= 0 else -1), 2),
                },
                "is_atm": abs(strike - atm) < step * 0.1,
            }
        )

    call_oi_total = sum(r["call"]["oi"] for r in rows)
    put_oi_total = sum(r["put"]["oi"] for r in rows)
    pcr = round(put_oi_total / call_oi_total, 3) if call_oi_total else None
    # Max pain approx: strike minimizing combined OI pain
    best_strike = atm
    best_pain = float("inf")
    for r in rows:
        k = r["strike"]
        pain = 0.0
        for x in rows:
            pain += max(0.0, x["strike"] - k) * x["call"]["oi"]
            pain += max(0.0, k - x["strike"]) * x["put"]["oi"]
        if pain < best_pain:
            best_pain = pain
            best_strike = k

    mult = lot_multiplier(TradeType.OPTIONS, symbol)
    return {
        "symbol": symbol.upper(),
        "spot": round(spot, 2),
        "atm_strike": atm,
        "strike_step": step,
        "expiry": expiry,
        "expiry_label": exp.strftime("%d-%b-%Y"),
        "days_to_expiry": dte,
        "lot_size": mult,
        "pcr": pcr,
        "max_pain": best_strike,
        "iv_atm_pct": round(iv_base * 100, 2),
        "rows": rows,
        "note": "Paper option chain (synthetic CE/PE). Not a live NSE feed.",
    }


class FnoSearchService:
    def __init__(self, market_data: Optional[MarketDataService] = None):
        self.data = market_data or MarketDataService(use_live=True)

    def search(self, query: str, limit: int = 20) -> list[dict]:
        q = (query or "").strip().upper()
        if not q:
            symbols = SEARCH_UNIVERSE[:limit]
        else:
            exact = [s for s in SEARCH_UNIVERSE if s == q]
            starts = [s for s in SEARCH_UNIVERSE if s.startswith(q) and s != q]
            contains = [s for s in SEARCH_UNIVERSE if q in s and s not in exact and s not in starts]
            symbols = (exact + starts + contains)[:limit]
        out = []
        for sym in symbols:
            try:
                quote = self.data.get_quote(sym, "NSE")
                out.append(
                    {
                        "symbol": sym,
                        "exchange": "NSE",
                        "price": quote["price"],
                        "change_pct": quote.get("change_pct", 0),
                        "is_index": is_index(sym),
                        "fno": True,
                        "segment": "INDEX" if is_index(sym) else "EQ",
                    }
                )
            except Exception:
                out.append(
                    {
                        "symbol": sym,
                        "exchange": "NSE",
                        "price": None,
                        "change_pct": None,
                        "is_index": is_index(sym),
                        "fno": True,
                        "segment": "INDEX" if is_index(sym) else "EQ",
                    }
                )
        return out

    def overview(self, symbol: str) -> dict:
        sym = symbol.upper().replace(".NS", "").replace(".BO", "")
        quote = self.data.get_quote(sym, "NSE")
        spot = float(quote["price"])
        df = self.data.get_ohlc(sym, "NSE", period="3mo")
        atr = None
        try:
            from quantx.analysis.technical import TechnicalAnalyzer

            snap = TechnicalAnalyzer().analyze(df)
            atr = snap.atr
        except Exception:
            atr = spot * 0.015
        expiries = list_expiries(sym)
        fut_px = round(spot, 2)  # Yahoo spot — no synthetic contango
        return {
            "symbol": sym,
            "exchange": "NSE",
            "spot": spot,
            "change_pct": quote.get("change_pct", 0),
            "source": quote.get("source", "yahoo_finance"),
            "yahoo_symbol": quote.get("yahoo_symbol"),
            "futures": {
                "ltp": fut_px,
                "basis": 0.0,
                "basis_pct": 0.0,
                "lot_size": lot_multiplier(TradeType.FUTURES, sym),
                "note": "Paper futures marked to Yahoo Finance underlying spot",
            },
            "expiries": expiries,
            "default_expiry": expiries[0]["expiry"] if expiries else None,
            "is_index": is_index(sym),
            "atr": round(float(atr or 0), 2),
        }

    def chain(self, symbol: str, expiry: Optional[str] = None) -> dict:
        overview = self.overview(symbol)
        exp = expiry or overview.get("default_expiry")
        if not exp:
            raise ValueError("No expiry available")
        # validate expiry belongs to list
        allowed = {e["expiry"] for e in overview["expiries"]}
        if exp not in allowed:
            # still allow ISO date if parseable
            date.fromisoformat(exp)
        return build_option_chain(
            overview["symbol"],
            overview["spot"],
            exp,
            atr=overview.get("atr"),
        )
