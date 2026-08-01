"""LLM chat / loss-review tests (no external network required)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from quantx.core.chat import QuantXLLMChat


_STORE: dict = {}


def _store(op, key, value=None):
    if op == "get":
        return _STORE.get(key, value)
    if op == "set":
        _STORE[key] = value
        return True
    return None


def _ctx():
    return {
        "portfolio": {"capital": 950000, "total_pnl": -50000, "unrealized_pnl": 0, "open_positions": 0, "trading_halted": False, "mode": "paper", "drawdown_pct": 5},
        "risk": {"can_trade": True, "reasons": [], "drawdown_pct": 5, "daily_loss_used_pct": 1, "consecutive_losses": 2, "open_positions": 0, "risk_budget_remaining": 9500},
        "positions": [
            {
                "symbol": "TCS",
                "side": "SELL",
                "quantity": 20,
                "entry_price": 3600,
                "exit_price": 3700,
                "current_price": 3700,
                "pnl": -2000,
                "pnl_pct": -2.7,
                "status": "CLOSED",
                "stop_loss": 3680,
                "target_1": 3400,
                "exit_reason": "Stop loss / trailing stop hit",
                "reason": "SHORT setup weak ADX",
            },
            {
                "symbol": "INFY",
                "side": "BUY",
                "quantity": 30,
                "entry_price": 1500,
                "exit_price": 1540,
                "pnl": 1200,
                "pnl_pct": 2.6,
                "status": "CLOSED",
                "exit_reason": "Target 1 hit",
            },
        ],
        "orders": [],
        "cycles": [
            {
                "cycle_no": 4,
                "message": "rejected 1",
                "valid_count": 1,
                "scanned": 8,
                "executed_count": 0,
                "rejected_count": 1,
                "executed": [],
                "rejected": [{"symbol": "RELIANCE", "reason": "Already in position — no averaging"}],
                "created_at": "2026-08-01T10:00:00Z",
            }
        ],
        "journal": [
            {"symbol": "TCS", "pnl": -2000, "mistakes": "Stop too tight", "lessons": "Use ATR stop", "market_condition": "Stop loss"}
        ],
        "performance": {"total_fees": 300, "win_rate": 50, "wins": 1, "losses": 1},
        "pnl": {"summary": {"capital": 950000, "total_pnl": -50000}},
        "paper": {"running": True, "cycles": 4, "executed": 2, "rejected": 3, "last_message": "ok"},
        "emergency": {"kill_switch": False, "messages": []},
        "macro": {"india_vix": 18.5},
        "config": {"risk": {"max_risk_per_trade_pct": 1}},
    }


def test_llm_config_without_key():
    _STORE.clear()
    bot = QuantXLLMChat(_ctx, settings_store=_store)
    cfg = bot.get_llm_config()
    assert cfg["has_api_key"] is False
    assert cfg["mode"] == "fallback_rules"


def test_set_api_key_enables_llm_flag():
    _STORE.clear()
    bot = QuantXLLMChat(_ctx, settings_store=_store)
    cfg = bot.set_api_key("test-key", provider="groq", model="llama-3.3-70b-versatile")
    assert cfg["has_api_key"] is True
    assert cfg["provider"] == "groq"
    assert cfg["enabled"] is True


def test_fallback_loss_review_without_network():
    _STORE.clear()
    bot = QuantXLLMChat(_ctx, settings_store=_store)
    res = bot.review_losses()
    assert res["mode"] == "fallback_rules"
    assert "TCS" in res["content"]
    assert res["analysis_stats"]["losses"] == 1
