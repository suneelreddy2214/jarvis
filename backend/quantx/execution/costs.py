"""NSE equity paper-trading cost model (approx Zerodha cash delivery / MIS)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from quantx.core.models import Side


Style = Literal["intraday", "swing"]


@dataclass
class TradeCosts:
    brokerage: float
    stt: float
    exchange_txn: float
    gst: float
    sebi: float
    stamp: float
    slippage: float
    total: float

    def as_dict(self) -> dict:
        return {
            "brokerage": round(self.brokerage, 2),
            "stt": round(self.stt, 2),
            "exchange_txn": round(self.exchange_txn, 2),
            "gst": round(self.gst, 2),
            "sebi": round(self.sebi, 2),
            "stamp": round(self.stamp, 2),
            "slippage": round(self.slippage, 2),
            "total": round(self.total, 2),
        }


class PaperCostModel:
    """
    Conservative Indian equity cost model for paper fills.
    Swing ≈ delivery; intraday ≈ MIS.
    """

    def __init__(
        self,
        slippage_bps: float = 5.0,
        brokerage_cap: float = 20.0,
        brokerage_rate: float = 0.0003,
    ):
        self.slippage_bps = slippage_bps
        self.brokerage_cap = brokerage_cap
        self.brokerage_rate = brokerage_rate

    def apply_slippage(self, price: float, side: Side, is_entry: bool = True) -> float:
        """Adverse slippage: buys fill higher, sells fill lower."""
        slip = price * (self.slippage_bps / 10_000)
        buying = (side == Side.BUY and is_entry) or (side == Side.SELL and not is_entry)
        return round(price + slip if buying else price - slip, 2)

    def estimate(
        self,
        entry: float,
        exit: float,
        quantity: int,
        side: Side,
        style: Style = "swing",
    ) -> TradeCosts:
        buy_turnover = (entry if side == Side.BUY else exit) * quantity
        sell_turnover = (exit if side == Side.BUY else entry) * quantity
        turnover = buy_turnover + sell_turnover

        brokerage = min(self.brokerage_cap, self.brokerage_rate * buy_turnover) + min(
            self.brokerage_cap, self.brokerage_rate * sell_turnover
        )

        if style == "intraday":
            stt = 0.00025 * sell_turnover  # sell side only
            stamp = 0.00003 * buy_turnover
        else:
            stt = 0.001 * sell_turnover  # delivery sell
            stamp = 0.00015 * buy_turnover

        exchange_txn = 0.0000297 * turnover
        sebi = 0.000001 * turnover
        gst = 0.18 * (brokerage + exchange_txn + sebi)

        slip_cost = abs(entry * quantity * (self.slippage_bps / 10_000)) + abs(
            exit * quantity * (self.slippage_bps / 10_000)
        )

        total = brokerage + stt + exchange_txn + gst + sebi + stamp + slip_cost
        return TradeCosts(
            brokerage=brokerage,
            stt=stt,
            exchange_txn=exchange_txn,
            gst=gst,
            sebi=sebi,
            stamp=stamp,
            slippage=slip_cost,
            total=total,
        )
