"""Paper F&O helpers — lot sizing, premium proxies, MTM for options."""

from __future__ import annotations

import re
from typing import Optional

from quantx.core.models import Side, TradeType
from quantx.portfolio.margin import lot_multiplier

INDEX_UNDERLYINGS = {"NIFTY", "NIFTY50", "BANKNIFTY"}

# Yahoo / synthetic symbol aliases for index underlyings
INDEX_YAHOO = {
    "NIFTY": "^NSEI",
    "NIFTY50": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
}

DEFAULT_FO_UNIVERSE = [
    "NIFTY",
    "BANKNIFTY",
    "RELIANCE",
    "TCS",
    "INFY",
    "HDFCBANK",
    "ICICIBANK",
    "SBIN",
]


def is_index(symbol: str) -> bool:
    return symbol.upper().replace(".NS", "").replace(".BO", "") in INDEX_UNDERLYINGS


def normalize_fo_symbol(symbol: str) -> str:
    s = symbol.upper().replace(".NS", "").replace(".BO", "").replace("^", "")
    if s in ("NSEI", "NSEBANK"):
        return "NIFTY" if s == "NSEI" else "BANKNIFTY"
    return s


def paper_option_premium(spot: float, atr: float) -> float:
    """ATM option premium proxy when no live chain is available."""
    return round(max(spot * 0.004, atr * 0.35, 5.0), 2)


def paper_option_levels(premium: float) -> dict:
    """Long-option premium levels with RR ≥ 1:2."""
    entry = float(premium)
    stop = round(max(entry * 0.5, 1.0), 2)
    risk = entry - stop
    if risk <= 0:
        risk = entry * 0.5
        stop = round(entry - risk, 2)
    t1 = round(entry + 2.0 * risk, 2)
    t2 = round(entry + 3.0 * risk, 2)
    return {
        "entry": entry,
        "stop_loss": stop,
        "target_1": t1,
        "target_2": t2,
        "risk_reward": 2.0,
    }


def option_kind_for_side(underlying_side: Side) -> str:
    """Directional long options: BUY CE on bullish, BUY PE on bearish."""
    return "CE" if underlying_side == Side.BUY else "PE"


def encode_fo_meta(underlying: float, kind: str, strike: float, expiry: Optional[str] = None) -> str:
    base = f"[FO u={underlying:.2f} {kind} k={strike:.2f}"
    if expiry:
        return f"{base} e={expiry}]"
    return f"{base}]"


def parse_fo_meta(reason: str) -> Optional[dict]:
    m = re.search(
        r"\[FO u=(?P<u>[0-9.]+) (?P<kind>CE|PE|FUT) k=(?P<k>[0-9.]+)(?: e=(?P<e>\d{4}-\d{2}-\d{2}))?\]",
        reason or "",
    )
    if not m:
        return None
    out = {
        "underlying_entry": float(m.group("u")),
        "kind": m.group("kind"),
        "strike": float(m.group("k")),
    }
    if m.group("e"):
        out["expiry"] = m.group("e")
    return out


def mark_option_premium(
    entry_premium: float,
    underlying_entry: float,
    underlying_now: float,
    kind: str,
    delta: float = 0.5,
) -> float:
    """Simple ATM delta MTM for paper long options."""
    move = underlying_now - underlying_entry
    signed = move if kind == "CE" else -move
    return round(max(0.05, entry_premium + delta * signed), 2)


def pnl_multiplier(trade_type: TradeType | str, symbol: str) -> int:
    return lot_multiplier(trade_type, symbol)
