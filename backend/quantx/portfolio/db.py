"""SQLite persistence for portfolio, journal, and agent state."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator, Optional

from quantx.core.config import get_settings
from quantx.core.models import (
    Position,
    PositionStatus,
    Side,
    TradeJournalEntry,
    TradeType,
)


class Database:
    def __init__(self, path: Optional[Path] = None):
        settings = get_settings()
        self.path = path or settings.db_path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(str(self.path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS positions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    exchange TEXT NOT NULL,
                    side TEXT NOT NULL,
                    trade_type TEXT NOT NULL,
                    quantity INTEGER NOT NULL,
                    entry_price REAL NOT NULL,
                    current_price REAL NOT NULL,
                    stop_loss REAL NOT NULL,
                    target_1 REAL NOT NULL,
                    target_2 REAL NOT NULL,
                    trailing_stop REAL,
                    status TEXT NOT NULL,
                    pnl REAL DEFAULT 0,
                    pnl_pct REAL DEFAULT 0,
                    capital_at_risk REAL DEFAULT 0,
                    confidence REAL DEFAULT 0,
                    reason TEXT,
                    opened_at TEXT NOT NULL,
                    closed_at TEXT,
                    exit_price REAL,
                    exit_reason TEXT
                );

                CREATE TABLE IF NOT EXISTS journal (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    trade_type TEXT NOT NULL,
                    entry REAL NOT NULL,
                    exit REAL NOT NULL,
                    quantity INTEGER NOT NULL,
                    pnl REAL NOT NULL,
                    pnl_pct REAL NOT NULL,
                    reason TEXT,
                    mistakes TEXT,
                    market_condition TEXT,
                    confidence REAL,
                    supporting_indicators TEXT,
                    lessons TEXT,
                    opened_at TEXT,
                    closed_at TEXT
                );

                CREATE TABLE IF NOT EXISTS agent_state (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    client_order_id TEXT UNIQUE,
                    symbol TEXT,
                    side TEXT,
                    quantity INTEGER,
                    price REAL,
                    status TEXT,
                    message TEXT,
                    created_at TEXT
                );

                CREATE TABLE IF NOT EXISTS cycles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cycle_no INTEGER NOT NULL,
                    message TEXT,
                    mtm_closed INTEGER DEFAULT 0,
                    scanned INTEGER DEFAULT 0,
                    valid_count INTEGER DEFAULT 0,
                    executed_count INTEGER DEFAULT 0,
                    rejected_count INTEGER DEFAULT 0,
                    executed_json TEXT,
                    rejected_json TEXT,
                    created_at TEXT NOT NULL
                );
                """
            )

    # --- state ---
    def set_state(self, key: str, value: Any) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO agent_state(key, value, updated_at) VALUES(?,?,?)
                ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
                """,
                (key, json.dumps(value), datetime.utcnow().isoformat()),
            )

    def get_state(self, key: str, default: Any = None) -> Any:
        with self.connect() as conn:
            row = conn.execute("SELECT value FROM agent_state WHERE key=?", (key,)).fetchone()
            if not row:
                return default
            return json.loads(row["value"])

    # --- positions ---
    def insert_position(self, p: Position) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO positions(
                    symbol, exchange, side, trade_type, quantity, entry_price, current_price,
                    stop_loss, target_1, target_2, trailing_stop, status, pnl, pnl_pct,
                    capital_at_risk, confidence, reason, opened_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    p.symbol, p.exchange, p.side.value, p.trade_type.value, p.quantity,
                    p.entry_price, p.current_price, p.stop_loss, p.target_1, p.target_2,
                    p.trailing_stop, p.status.value, p.pnl, p.pnl_pct, p.capital_at_risk,
                    p.confidence, p.reason, p.opened_at.isoformat(),
                ),
            )
            return int(cur.lastrowid)

    def update_position(self, p: Position) -> None:
        assert p.id is not None
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE positions SET current_price=?, stop_loss=?, trailing_stop=?, status=?,
                    pnl=?, pnl_pct=?, closed_at=?, exit_price=?, exit_reason=?
                WHERE id=?
                """,
                (
                    p.current_price, p.stop_loss, p.trailing_stop, p.status.value,
                    p.pnl, p.pnl_pct,
                    p.closed_at.isoformat() if p.closed_at else None,
                    p.exit_price, p.exit_reason, p.id,
                ),
            )

    def list_positions(self, status: Optional[str] = "OPEN") -> list[Position]:
        with self.connect() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM positions WHERE status=? ORDER BY id DESC", (status,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM positions ORDER BY id DESC").fetchall()
        return [self._row_to_position(r) for r in rows]

    def get_position(self, position_id: int) -> Optional[Position]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM positions WHERE id=?", (position_id,)).fetchone()
        return self._row_to_position(row) if row else None

    def _row_to_position(self, row: sqlite3.Row) -> Position:
        return Position(
            id=row["id"],
            symbol=row["symbol"],
            exchange=row["exchange"],
            side=Side(row["side"]),
            trade_type=TradeType(row["trade_type"]),
            quantity=row["quantity"],
            entry_price=row["entry_price"],
            current_price=row["current_price"],
            stop_loss=row["stop_loss"],
            target_1=row["target_1"],
            target_2=row["target_2"],
            trailing_stop=row["trailing_stop"],
            status=PositionStatus(row["status"]),
            pnl=row["pnl"],
            pnl_pct=row["pnl_pct"],
            capital_at_risk=row["capital_at_risk"],
            confidence=row["confidence"],
            reason=row["reason"] or "",
            opened_at=datetime.fromisoformat(row["opened_at"]),
            closed_at=datetime.fromisoformat(row["closed_at"]) if row["closed_at"] else None,
            exit_price=row["exit_price"],
            exit_reason=row["exit_reason"],
        )

    # --- journal ---
    def insert_journal(self, e: TradeJournalEntry) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO journal(
                    symbol, side, trade_type, entry, exit, quantity, pnl, pnl_pct,
                    reason, mistakes, market_condition, confidence, supporting_indicators,
                    lessons, opened_at, closed_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    e.symbol, e.side.value, e.trade_type.value, e.entry, e.exit, e.quantity,
                    e.pnl, e.pnl_pct, e.reason, e.mistakes, e.market_condition, e.confidence,
                    json.dumps(e.supporting_indicators), e.lessons,
                    e.opened_at.isoformat(), e.closed_at.isoformat(),
                ),
            )
            return int(cur.lastrowid)

    def list_journal(self, limit: int = 100) -> list[TradeJournalEntry]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM journal ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        out = []
        for row in rows:
            out.append(
                TradeJournalEntry(
                    id=row["id"],
                    symbol=row["symbol"],
                    side=Side(row["side"]),
                    trade_type=TradeType(row["trade_type"]),
                    entry=row["entry"],
                    exit=row["exit"],
                    quantity=row["quantity"],
                    pnl=row["pnl"],
                    pnl_pct=row["pnl_pct"],
                    reason=row["reason"] or "",
                    mistakes=row["mistakes"] or "",
                    market_condition=row["market_condition"] or "",
                    confidence=row["confidence"] or 0,
                    supporting_indicators=json.loads(row["supporting_indicators"] or "[]"),
                    lessons=row["lessons"] or "",
                    opened_at=datetime.fromisoformat(row["opened_at"]),
                    closed_at=datetime.fromisoformat(row["closed_at"]),
                )
            )
        return out

    def record_order(
        self,
        client_order_id: str,
        symbol: str,
        side: str,
        quantity: int,
        price: float,
        status: str,
        message: str = "",
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO orders(client_order_id, symbol, side, quantity, price, status, message, created_at)
                VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    client_order_id, symbol, side, quantity, price, status, message,
                    datetime.utcnow().isoformat(),
                ),
            )

    def order_exists(self, client_order_id: str) -> bool:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM orders WHERE client_order_id=? AND status=?",
                (client_order_id, "FILLED"),
            ).fetchone()
        return row is not None

    def clear_orders(self) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM orders")

    def clear_journal(self) -> None:
        with self.connect() as conn:
            conn.execute("DELETE FROM journal")
            conn.execute("DELETE FROM positions")

    def list_orders(self, limit: int = 100) -> list[dict]:
        with self.connect() as conn:
            # Ensure cycles table exists for older DBs
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cycles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cycle_no INTEGER NOT NULL,
                    message TEXT,
                    mtm_closed INTEGER DEFAULT 0,
                    scanned INTEGER DEFAULT 0,
                    valid_count INTEGER DEFAULT 0,
                    executed_count INTEGER DEFAULT 0,
                    rejected_count INTEGER DEFAULT 0,
                    executed_json TEXT,
                    rejected_json TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            rows = conn.execute(
                "SELECT * FROM orders ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            {
                "id": r["id"],
                "client_order_id": r["client_order_id"],
                "symbol": r["symbol"],
                "side": r["side"],
                "quantity": r["quantity"],
                "price": r["price"],
                "status": r["status"],
                "message": r["message"] or "",
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def insert_cycle(self, entry: dict) -> int:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cycles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cycle_no INTEGER NOT NULL,
                    message TEXT,
                    mtm_closed INTEGER DEFAULT 0,
                    scanned INTEGER DEFAULT 0,
                    valid_count INTEGER DEFAULT 0,
                    executed_count INTEGER DEFAULT 0,
                    rejected_count INTEGER DEFAULT 0,
                    executed_json TEXT,
                    rejected_json TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            cur = conn.execute(
                """
                INSERT INTO cycles(
                    cycle_no, message, mtm_closed, scanned, valid_count,
                    executed_count, rejected_count, executed_json, rejected_json, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    entry.get("cycle_no", 0),
                    entry.get("message", ""),
                    entry.get("mtm_closed", 0),
                    entry.get("scanned", 0),
                    entry.get("valid_count", 0),
                    entry.get("executed_count", 0),
                    entry.get("rejected_count", 0),
                    json.dumps(entry.get("executed", [])),
                    json.dumps(entry.get("rejected", [])),
                    entry.get("created_at") or datetime.utcnow().isoformat() + "Z",
                ),
            )
            return int(cur.lastrowid)

    def list_cycles(self, limit: int = 50) -> list[dict]:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cycles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cycle_no INTEGER NOT NULL,
                    message TEXT,
                    mtm_closed INTEGER DEFAULT 0,
                    scanned INTEGER DEFAULT 0,
                    valid_count INTEGER DEFAULT 0,
                    executed_count INTEGER DEFAULT 0,
                    rejected_count INTEGER DEFAULT 0,
                    executed_json TEXT,
                    rejected_json TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            rows = conn.execute(
                "SELECT * FROM cycles ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        out = []
        for r in rows:
            out.append(
                {
                    "id": r["id"],
                    "cycle_no": r["cycle_no"],
                    "message": r["message"] or "",
                    "mtm_closed": r["mtm_closed"],
                    "scanned": r["scanned"],
                    "valid_count": r["valid_count"],
                    "executed_count": r["executed_count"],
                    "rejected_count": r["rejected_count"],
                    "executed": json.loads(r["executed_json"] or "[]"),
                    "rejected": json.loads(r["rejected_json"] or "[]"),
                    "created_at": r["created_at"],
                }
            )
        return out

    def clear_cycles(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cycles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    cycle_no INTEGER NOT NULL,
                    message TEXT,
                    mtm_closed INTEGER DEFAULT 0,
                    scanned INTEGER DEFAULT 0,
                    valid_count INTEGER DEFAULT 0,
                    executed_count INTEGER DEFAULT 0,
                    rejected_count INTEGER DEFAULT 0,
                    executed_json TEXT,
                    rejected_json TEXT,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute("DELETE FROM cycles")

