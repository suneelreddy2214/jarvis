"""
Per-strategy performance learning (paper self-training).

Tracks wins/losses/PnL per strategy name and maintains adaptive weights
used by the strategy selector. Not a full ML model — multiplicative
online learning suitable for paper trading feedback loops.
"""

from __future__ import annotations

import math
import re
from typing import Any, Optional

from quantx.portfolio.db import Database

STRATEGY_TAG_RE = re.compile(r"\[([a-z0-9_]+)\]", re.I)


def extract_strategy_name(reason: str, fallback: str = "ta_default") -> str:
    if not reason:
        return fallback
    m = STRATEGY_TAG_RE.search(reason)
    if m:
        return m.group(1).lower()
    return fallback


class StrategyLearner:
    """Persist and update strategy weights from closed-trade outcomes."""

    STATE_KEY = "strategy_learning"

    def __init__(self, db: Optional[Database] = None):
        self.db = db or Database()

    def _load(self) -> dict[str, Any]:
        raw = self.db.get_state(self.STATE_KEY, None)
        if not isinstance(raw, dict):
            return {"strategies": {}, "updates": 0}
        raw.setdefault("strategies", {})
        raw.setdefault("updates", 0)
        return raw

    def _save(self, data: dict[str, Any]) -> None:
        self.db.set_state(self.STATE_KEY, data)

    def _ensure(self, store: dict, name: str) -> dict:
        if name not in store["strategies"]:
            store["strategies"][name] = {
                "trades": 0,
                "wins": 0,
                "losses": 0,
                "pnl": 0.0,
                "weight": 1.0,
            }
        return store["strategies"][name]

    def record_trade(self, strategy: str, pnl: float) -> dict:
        store = self._load()
        row = self._ensure(store, strategy)
        row["trades"] += 1
        row["pnl"] = round(float(row["pnl"]) + float(pnl), 2)
        if pnl > 0:
            row["wins"] += 1
            row["weight"] = min(3.0, float(row["weight"]) * 1.08)
        else:
            row["losses"] += 1
            row["weight"] = max(0.25, float(row["weight"]) * 0.92)
        store["updates"] = int(store.get("updates", 0)) + 1
        self._save(store)
        return row

    def sync_from_journal(self, journal: list) -> dict:
        """
        Merge journal-derived strategy stats into existing learning state.

        Preserves backend self-train / style:* weights — never wipes the store.
        """
        store = self._load()
        journal_stats: dict[str, dict] = {}
        for j in journal:
            name = extract_strategy_name(getattr(j, "reason", "") or "")
            if name.startswith("style:"):
                continue
            row = journal_stats.setdefault(
                name,
                {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0, "weight": 1.0},
            )
            pnl = float(getattr(j, "pnl", 0) or 0)
            row["trades"] += 1
            row["pnl"] = round(row["pnl"] + pnl, 2)
            if pnl > 0:
                row["wins"] += 1
            else:
                row["losses"] += 1
        for name, row in journal_stats.items():
            n = max(1, row["trades"])
            wr = row["wins"] / n
            avg = row["pnl"] / n
            score = (wr - 0.45) * 2.0 + math.tanh(avg / 5000.0)
            row["weight"] = round(max(0.25, min(3.0, 1.0 + score)), 3)
            # Prefer journal stats for live paper strategies, but keep style:* intact.
            existing = store["strategies"].get(name)
            if existing and int(existing.get("trades", 0)) > int(row["trades"]):
                # Keep richer backtest/self-train stats when they have more samples.
                continue
            store["strategies"][name] = row
        store["updates"] = max(
            int(store.get("updates", 0)),
            sum(int(r.get("trades", 0)) for r in store["strategies"].values()),
        )
        self._save(store)
        return store

    def weights(self) -> dict[str, float]:
        store = self._load()
        return {k: float(v.get("weight", 1.0)) for k, v in store["strategies"].items()}

    def leaderboard(self) -> list[dict]:
        store = self._load()
        rows = []
        for name, v in store["strategies"].items():
            trades = int(v.get("trades", 0))
            wins = int(v.get("wins", 0))
            rows.append(
                {
                    "strategy": name,
                    "trades": trades,
                    "wins": wins,
                    "losses": int(v.get("losses", 0)),
                    "win_rate": round(wins / trades * 100, 1) if trades else 0.0,
                    "pnl": round(float(v.get("pnl", 0)), 2),
                    "weight": round(float(v.get("weight", 1.0)), 3),
                }
            )
        rows.sort(key=lambda r: (-r["weight"], -r["pnl"]))
        return rows

    def as_dict(self) -> dict:
        store = self._load()
        return {
            "updates": store.get("updates", 0),
            "leaderboard": self.leaderboard(),
            "weights": self.weights(),
            "mode": "online_paper_learning",
            "note": (
                "Weights adapt from paper closes and backend trading-style self-train. "
                "Strategies with better win-rate/PnL get higher selection priority."
            ),
        }
