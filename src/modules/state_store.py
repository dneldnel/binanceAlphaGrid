from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from core.models import ExecutionPreview, SymbolConfig, SymbolState, TradeDecision, TradeFill


class StateStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._init_schema()

    def _init_schema(self) -> None:
        with self.conn:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS fills (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    level INTEGER NOT NULL,
                    price REAL NOT NULL,
                    base_qty REAL NOT NULL,
                    quote_value REAL NOT NULL,
                    realized_pnl REAL NOT NULL,
                    tx_hash TEXT,
                    message TEXT NOT NULL
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS positions (
                    symbol TEXT PRIMARY KEY,
                    updated_at TEXT NOT NULL,
                    status TEXT NOT NULL,
                    base_balance REAL NOT NULL,
                    quote_balance_usd REAL NOT NULL,
                    reserve_base_tokens REAL NOT NULL,
                    avg_cost_price REAL NOT NULL,
                    realized_pnl REAL NOT NULL,
                    unrealized_pnl REAL NOT NULL,
                    daily_trade_count INTEGER NOT NULL,
                    buy_done_count INTEGER NOT NULL,
                    sell_done_count INTEGER NOT NULL,
                    reference_price REAL NOT NULL,
                    last_mid_price REAL NOT NULL
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS pending_txs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    level INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    tx_hash TEXT,
                    amount_in_raw INTEGER,
                    amount_out_min_raw INTEGER,
                    expected_out_raw INTEGER,
                    approve_token_address TEXT,
                    path_json TEXT NOT NULL,
                    last_error TEXT
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS realized_pnl (
                    symbol TEXT PRIMARY KEY,
                    updated_at TEXT NOT NULL,
                    realized_pnl REAL NOT NULL
                )
                """
            )
        self._ensure_column("pending_txs", "last_error", "TEXT")

    def record_fill(self, fill: TradeFill) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO fills (
                    ts, symbol, side, level, price, base_qty, quote_value, realized_pnl, tx_hash, message
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._now_iso(),
                    fill.symbol,
                    fill.side,
                    fill.level,
                    fill.price,
                    fill.base_qty,
                    fill.quote_value,
                    fill.realized_pnl,
                    fill.tx_hash,
                    fill.message,
                ),
            )

    def sync_symbol_state(self, state: SymbolState) -> None:
        now = self._now_iso()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO positions (
                    symbol,
                    updated_at,
                    status,
                    base_balance,
                    quote_balance_usd,
                    reserve_base_tokens,
                    avg_cost_price,
                    realized_pnl,
                    unrealized_pnl,
                    daily_trade_count,
                    buy_done_count,
                    sell_done_count,
                    reference_price,
                    last_mid_price
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    updated_at=excluded.updated_at,
                    status=excluded.status,
                    base_balance=excluded.base_balance,
                    quote_balance_usd=excluded.quote_balance_usd,
                    reserve_base_tokens=excluded.reserve_base_tokens,
                    avg_cost_price=excluded.avg_cost_price,
                    realized_pnl=excluded.realized_pnl,
                    unrealized_pnl=excluded.unrealized_pnl,
                    daily_trade_count=excluded.daily_trade_count,
                    buy_done_count=excluded.buy_done_count,
                    sell_done_count=excluded.sell_done_count,
                    reference_price=excluded.reference_price,
                    last_mid_price=excluded.last_mid_price
                """,
                (
                    state.symbol,
                    now,
                    state.status,
                    state.base_balance,
                    state.quote_balance_usd,
                    state.reserve_base_tokens,
                    state.avg_cost_price,
                    state.realized_pnl,
                    state.unrealized_pnl,
                    state.daily_trade_count,
                    state.buy_done_count,
                    state.sell_done_count,
                    state.reference_price,
                    state.last_mid_price,
                ),
            )
            self.conn.execute(
                """
                INSERT INTO realized_pnl (symbol, updated_at, realized_pnl)
                VALUES (?, ?, ?)
                ON CONFLICT(symbol) DO UPDATE SET
                    updated_at=excluded.updated_at,
                    realized_pnl=excluded.realized_pnl
                """,
                (
                    state.symbol,
                    now,
                    state.realized_pnl,
                ),
            )

    def create_pending_tx(
        self,
        symbol: SymbolConfig,
        decision: TradeDecision,
        preview: ExecutionPreview,
    ) -> int | None:
        if preview.amount_in_raw is None or preview.amount_out_min_raw is None:
            return None

        now = self._now_iso()
        path_json = json.dumps(preview.path)
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO pending_txs (
                    created_at,
                    updated_at,
                    symbol,
                    side,
                    level,
                    status,
                    tx_hash,
                    amount_in_raw,
                    amount_out_min_raw,
                    expected_out_raw,
                    approve_token_address,
                    path_json,
                    last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    now,
                    now,
                    symbol.name,
                    decision.side,
                    decision.level,
                    "prepared",
                    None,
                    preview.amount_in_raw,
                    preview.amount_out_min_raw,
                    preview.expected_out_raw,
                    preview.approve_token_address,
                    path_json,
                    None,
                ),
            )
            return int(cursor.lastrowid)

    def mark_pending_tx_confirmed(self, pending_tx_id: int, tx_hash: str | None) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE pending_txs
                SET updated_at = ?, status = ?, tx_hash = ?
                WHERE id = ?
                """,
                (
                    self._now_iso(),
                    "confirmed",
                    tx_hash,
                    pending_tx_id,
                ),
            )

    def mark_pending_tx_failed(self, pending_tx_id: int, error: str) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE pending_txs
                SET updated_at = ?, status = ?, last_error = ?
                WHERE id = ?
                """,
                (
                    self._now_iso(),
                    "failed",
                    error,
                    pending_tx_id,
                ),
            )

    def load_position(self, symbol: str) -> sqlite3.Row | None:
        cursor = self.conn.execute(
            """
            SELECT
                symbol,
                status,
                base_balance,
                quote_balance_usd,
                reserve_base_tokens,
                avg_cost_price,
                realized_pnl,
                unrealized_pnl,
                daily_trade_count,
                buy_done_count,
                sell_done_count,
                reference_price,
                last_mid_price
            FROM positions
            WHERE symbol = ?
            """,
            (symbol,),
        )
        row = cursor.fetchone()
        return row

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {
            str(row["name"])
            for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column in columns:
            return
        with self.conn:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
