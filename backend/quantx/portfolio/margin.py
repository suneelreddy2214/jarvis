"""
Paper margin model for Stocks vs F&O (NSE-style approximations).

Not exchange SPAN — conservative paper estimates for visibility & risk checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from quantx.core.models import Position, Side, TradeType


# Default haircuts / margin fractions of notional (entry * qty * multiplier)
MARGIN_FRAC = {
    "EQUITY_CNC": 1.00,      # delivery — full cash block
    "EQUITY_MIS": 0.20,      # intraday equity
    "ETF": 1.00,
    "FUTURES": 0.12,         # ~12% SPAN+ELM proxy
    "OPTIONS_BUY": 1.00,     # premium fully paid
    "OPTIONS_SELL": 8.00,    # ~8× premium ≈ SPAN proxy when spot unavailable
}


@dataclass
class PositionMargin:
    position_id: Optional[int]
    symbol: str
    product: str  # Stocks | Futures | Options | ETF
    segment: str  # EQ | FO
    trade_type: str
    side: str
    quantity: int
    entry_price: float
    ltp: float
    multiplier: int
    notional: float
    margin_required: float
    margin_pct: float
    exposure: float
    unrealized_pnl: float
    capital_at_risk: float


@dataclass
class MarginBook:
    capital: float
    total_margin_used: float
    available_margin: float
    margin_utilization_pct: float
    stocks_margin: float
    futures_margin: float
    options_margin: float
    etf_margin: float
    stocks_exposure: float
    futures_exposure: float
    options_exposure: float
    open_stocks: int
    open_futures: int
    open_options: int
    open_etf: int
    positions: list[PositionMargin] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "capital": round(self.capital, 2),
            "total_margin_used": round(self.total_margin_used, 2),
            "available_margin": round(self.available_margin, 2),
            "margin_utilization_pct": round(self.margin_utilization_pct, 2),
            "by_segment": {
                "stocks": {
                    "label": "Stocks (EQ)",
                    "margin_used": round(self.stocks_margin, 2),
                    "exposure": round(self.stocks_exposure, 2),
                    "open_positions": self.open_stocks,
                },
                "futures": {
                    "label": "Futures (F&O)",
                    "margin_used": round(self.futures_margin, 2),
                    "exposure": round(self.futures_exposure, 2),
                    "open_positions": self.open_futures,
                },
                "options": {
                    "label": "Options (F&O)",
                    "margin_used": round(self.options_margin, 2),
                    "exposure": round(self.options_exposure, 2),
                    "open_positions": self.open_options,
                },
                "etf": {
                    "label": "ETFs",
                    "margin_used": round(self.etf_margin, 2),
                    "exposure": round(sum(p.exposure for p in self.positions if p.product == "ETF"), 2),
                    "open_positions": self.open_etf,
                },
            },
            "fo_total_margin": round(self.futures_margin + self.options_margin, 2),
            "fo_total_exposure": round(self.futures_exposure + self.options_exposure, 2),
            "positions": [
                {
                    "position_id": p.position_id,
                    "symbol": p.symbol,
                    "product": p.product,
                    "segment": p.segment,
                    "trade_type": p.trade_type,
                    "side": p.side,
                    "quantity": p.quantity,
                    "entry_price": p.entry_price,
                    "ltp": p.ltp,
                    "multiplier": p.multiplier,
                    "notional": round(p.notional, 2),
                    "margin_required": round(p.margin_required, 2),
                    "margin_pct": round(p.margin_pct * 100, 2),
                    "exposure": round(p.exposure, 2),
                    "unrealized_pnl": round(p.unrealized_pnl, 2),
                    "capital_at_risk": round(p.capital_at_risk, 2),
                }
                for p in self.positions
            ],
            "notes": self.notes,
        }


def classify_product(trade_type: TradeType | str) -> tuple[str, str, str]:
    """Return (product_label, segment, margin_key)."""
    tt = trade_type.value if isinstance(trade_type, TradeType) else str(trade_type)
    if tt == TradeType.FUTURES.value:
        return "Futures", "FO", "FUTURES"
    if tt == TradeType.OPTIONS.value:
        return "Options", "FO", "OPTIONS_BUY"  # side adjusted later
    if tt == TradeType.ETF.value:
        return "ETF", "EQ", "ETF"
    if tt == TradeType.INTRADAY.value:
        return "Stocks", "EQ", "EQUITY_MIS"
    # SWING / default delivery
    return "Stocks", "EQ", "EQUITY_CNC"


def lot_multiplier(trade_type: TradeType | str, symbol: str = "") -> int:
    tt = trade_type.value if isinstance(trade_type, TradeType) else str(trade_type)
    if tt in (TradeType.FUTURES.value, TradeType.OPTIONS.value):
        # Simplified NSE lot proxies for paper
        s = symbol.upper()
        if s in ("NIFTY", "NIFTY50"):
            return 25
        if s in ("BANKNIFTY",):
            return 15
        return 50  # stock futures/options proxy
    return 1


class MarginCalculator:
    def margin_for_position(self, p: Position) -> PositionMargin:
        product, segment, key = classify_product(p.trade_type)
        mult = lot_multiplier(p.trade_type, p.symbol)
        notional = float(p.entry_price) * int(p.quantity) * mult

        if key.startswith("OPTIONS"):
            # Buy premium blocked fully; sell uses higher margin proxy on notional
            if p.side == Side.BUY:
                key = "OPTIONS_BUY"
                frac = MARGIN_FRAC["OPTIONS_BUY"]
                # For long options, notional ≈ premium * qty * mult (entry is premium)
            else:
                key = "OPTIONS_SELL"
                frac = MARGIN_FRAC["OPTIONS_SELL"]
        else:
            frac = MARGIN_FRAC.get(key, 1.0)

        margin = notional * frac
        exposure = float(p.current_price) * int(p.quantity) * mult
        return PositionMargin(
            position_id=p.id,
            symbol=p.symbol,
            product=product,
            segment=segment,
            trade_type=p.trade_type.value if isinstance(p.trade_type, TradeType) else str(p.trade_type),
            side=p.side.value if isinstance(p.side, Side) else str(p.side),
            quantity=int(p.quantity),
            entry_price=float(p.entry_price),
            ltp=float(p.current_price),
            multiplier=mult,
            notional=notional,
            margin_required=margin,
            margin_pct=frac,
            exposure=exposure,
            unrealized_pnl=float(p.pnl or 0),
            capital_at_risk=float(p.capital_at_risk or 0),
        )

    def build_book(self, capital: float, positions: list[Position]) -> MarginBook:
        rows = [self.margin_for_position(p) for p in positions]
        stocks_m = sum(r.margin_required for r in rows if r.product == "Stocks")
        fut_m = sum(r.margin_required for r in rows if r.product == "Futures")
        opt_m = sum(r.margin_required for r in rows if r.product == "Options")
        etf_m = sum(r.margin_required for r in rows if r.product == "ETF")
        stocks_x = sum(r.exposure for r in rows if r.product == "Stocks")
        fut_x = sum(r.exposure for r in rows if r.product == "Futures")
        opt_x = sum(r.exposure for r in rows if r.product == "Options")
        used = stocks_m + fut_m + opt_m + etf_m
        avail = max(0.0, capital - used)
        util = (used / capital * 100) if capital else 0.0
        notes = [
            "Paper margins are NSE-style estimates (not live SPAN/broker RMS).",
            "Stocks SWING/ETF ≈ CNC 100% cash; INTRADAY ≈ MIS 20%.",
            "Futures ≈ 12% of notional; Options buy = premium; Options sell ≈ 8× premium (SPAN proxy).",
        ]
        return MarginBook(
            capital=capital,
            total_margin_used=used,
            available_margin=avail,
            margin_utilization_pct=util,
            stocks_margin=stocks_m,
            futures_margin=fut_m,
            options_margin=opt_m,
            etf_margin=etf_m,
            stocks_exposure=stocks_x,
            futures_exposure=fut_x,
            options_exposure=opt_x,
            open_stocks=sum(1 for r in rows if r.product == "Stocks"),
            open_futures=sum(1 for r in rows if r.product == "Futures"),
            open_options=sum(1 for r in rows if r.product == "Options"),
            open_etf=sum(1 for r in rows if r.product == "ETF"),
            positions=rows,
            notes=notes,
        )
