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
                "SELECT 1 FROM orders WHERE client_order_id=?", (client_order_id,)
            ).fetchone()
        return row is not None
