"""Chat assistant tests."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from quantx.core.chat import QuantXChat


def _ctx():
    return {
        "portfolio": {
            "capital": 1_000_000,
            "total_pnl": -1200,
            "unrealized_pnl": -500,
            "open_positions": 2,
            "trading_halted": False,
            "halt_reason": None,
            "mode": "paper",
            "available_margin": 900000,
            "used_margin": 100000,
            "drawdown_pct": 1.2,
        },
        "risk": {
            "can_trade": True,
            "reasons": [],
            "drawdown_pct": 1.2,
            "daily_loss_used_pct": 0.1,
            "consecutive_losses": 0,
            "open_positions": 2,
            "risk_budget_remaining": 10000,
        },
        "positions": [
            {
                "symbol": "RELIANCE",
                "side": "BUY",
                "quantity": 10,
                "entry_price": 2800,
                "current_price": 2790,
                "pnl": -100,
                "pnl_pct": -0.35,
                "status": "OPEN",
                "stop_loss": 2740,
                "target_1": 2920,
            }
        ],
        "orders": [
            {
                "symbol": "RELIANCE",
                "side": "BUY",
                "quantity": 10,
                "price": 2800,
                "status": "FILLED",
                "message": "paper fill",
                "created_at": "2026-08-01T10:00:00",
            }
        ],
        "cycles": [
            {
                "cycle_no": 3,
                "message": "Cycle #3: valid 1/10, executed 0, rejected 1",
                "valid_count": 1,
                "scanned": 10,
                "executed_count": 0,
                "rejected_count": 1,
                "executed": [],
                "rejected": [{"symbol": "TCS", "reason": "Already in position — no averaging"}],
            }
        ],
        "journal": [],
        "performance": {"total_fees": 200, "win_rate": 0, "wins": 0, "losses": 0},
        "pnl": {"summary": {"capital": 1000000, "total_pnl": -1200, "unrealized_pnl": -500, "closed_realized_pnl": -700, "total_fees": 200, "win_rate": 0, "wins": 0, "losses": 1, "open_positions": 2, "closed_trades": 1}},
        "paper": {"running": True, "cycles": 3, "executed": 1, "rejected": 2, "last_message": "Cycle #3"},
        "emergency": {"kill_switch": False, "max_drawdown_lock": False, "manual_override": False, "messages": []},
        "macro": {"nifty_change_pct": 0.4, "india_vix": 14.2},
        "config": {"risk": {"max_risk_per_trade_pct": 1, "max_daily_loss_pct": 2, "max_drawdown_pct": 10, "max_consecutive_losses": 3, "max_open_positions": 5}},
        "watchlist_symbols": ["RELIANCE", "TCS"],
    }


def test_chat_pnl():
    bot = QuantXChat(_ctx)
    res = bot.ask("What's my P&L?")
    assert res["role"] == "assistant"
    assert "P&L" in res["content"] or "Capital" in res["content"]


def test_chat_positions():
    bot = QuantXChat(_ctx)
    res = bot.ask("Show open positions")
    assert "RELIANCE" in res["content"]


def test_chat_rejections():
    bot = QuantXChat(_ctx)
    res = bot.ask("Why were trades rejected?")
    assert "TCS" in res["content"] or "reject" in res["content"].lower()


def test_chat_symbol():
    bot = QuantXChat(_ctx)
    res = bot.ask("Status of RELIANCE")
    assert "RELIANCE" in res["content"]
