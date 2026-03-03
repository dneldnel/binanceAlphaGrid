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
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    submitted_at TEXT,
                    confirmed_at TEXT,
                    next_retry_at TEXT,
                    approve_token_address TEXT,
                    approve_tx_hash TEXT,
                    approve_nonce INTEGER,
                    approve_gas_price_wei INTEGER,
                    swap_tx_hash TEXT,
                    swap_nonce INTEGER,
                    swap_gas_price_wei INTEGER,
                    receipt_status INTEGER,
                    estimated_gas_usd REAL,
                    fill_price REAL,
                    fill_base_qty REAL,
                    fill_quote_value REAL,
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
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS execution_failures (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    pending_tx_id INTEGER,
                    message TEXT NOT NULL
                )
                """
            )
            self.conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_fills_symbol_ts
                ON fills(symbol, ts)
                """
            )
            self.conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_execution_failures_symbol_ts
                ON execution_failures(symbol, ts)
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS execution_attempts (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    pending_tx_id INTEGER,
                    estimated_gas_usd REAL NOT NULL,
                    approve_tx_hash TEXT,
                    approve_nonce INTEGER,
                    approve_gas_price_wei INTEGER,
                    swap_tx_hash TEXT,
                    swap_nonce INTEGER,
                    swap_gas_price_wei INTEGER,
                    actual_gas_usd REAL
                )
                """
            )
            self.conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_execution_attempts_symbol_ts
                ON execution_attempts(symbol, ts)
                """
            )
        self._ensure_pending_tx_columns()

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
                    attempt_count,
                    submitted_at,
                    confirmed_at,
                    next_retry_at,
                    approve_token_address,
                    approve_tx_hash,
                    approve_nonce,
                    approve_gas_price_wei,
                    swap_tx_hash,
                    swap_nonce,
                    swap_gas_price_wei,
                    receipt_status,
                    estimated_gas_usd,
                    fill_price,
                    fill_base_qty,
                    fill_quote_value,
                    path_json,
                    last_error
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    0,
                    None,
                    None,
                    None,
                    preview.approve_token_address,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    preview.estimated_gas_usd,
                    None,
                    None,
                    None,
                    path_json,
                    None,
                ),
            )
            return int(cursor.lastrowid)

    def mark_pending_tx_submitted(self, pending_tx_id: int) -> None:
        now = self._now_iso()
        with self.conn:
            self.conn.execute(
                """
                UPDATE pending_txs
                SET updated_at = ?,
                    status = ?,
                    submitted_at = ?,
                    attempt_count = attempt_count + 1,
                    last_error = NULL
                WHERE id = ?
                """,
                (
                    now,
                    "submitted",
                    now,
                    pending_tx_id,
                ),
            )

    def mark_pending_tx_confirmed(self, pending_tx_id: int, fill: TradeFill) -> None:
        now = self._now_iso()
        with self.conn:
            self.conn.execute(
                """
                UPDATE pending_txs
                SET updated_at = ?,
                    status = ?,
                    tx_hash = ?,
                    swap_tx_hash = ?,
                    approve_tx_hash = COALESCE(?, approve_tx_hash),
                    approve_nonce = COALESCE(?, approve_nonce),
                    approve_gas_price_wei = COALESCE(?, approve_gas_price_wei),
                    swap_nonce = COALESCE(?, swap_nonce),
                    swap_gas_price_wei = COALESCE(?, swap_gas_price_wei),
                    receipt_status = ?,
                    confirmed_at = ?,
                    fill_price = ?,
                    fill_base_qty = ?,
                    fill_quote_value = ?,
                    next_retry_at = NULL,
                    last_error = NULL
                WHERE id = ?
                """,
                (
                    now,
                    "confirmed",
                    fill.tx_hash,
                    fill.tx_hash,
                    fill.approve_tx_hash,
                    fill.approve_nonce,
                    fill.approve_gas_price_wei,
                    fill.tx_nonce,
                    fill.tx_gas_price_wei,
                    1,
                    now,
                    fill.price,
                    fill.base_qty,
                    fill.quote_value,
                    pending_tx_id,
                ),
            )

    def mark_pending_tx_retryable(
        self,
        pending_tx_id: int,
        error: str,
        *,
        next_retry_at: str | None,
        approve_tx_hash: str | None = None,
        approve_nonce: int | None = None,
        approve_gas_price_wei: int | None = None,
        swap_tx_hash: str | None = None,
        swap_nonce: int | None = None,
        swap_gas_price_wei: int | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE pending_txs
                SET updated_at = ?,
                    status = ?,
                    tx_hash = COALESCE(?, tx_hash),
                    approve_tx_hash = COALESCE(?, approve_tx_hash),
                    approve_nonce = COALESCE(?, approve_nonce),
                    approve_gas_price_wei = COALESCE(?, approve_gas_price_wei),
                    swap_tx_hash = COALESCE(?, swap_tx_hash),
                    swap_nonce = COALESCE(?, swap_nonce),
                    swap_gas_price_wei = COALESCE(?, swap_gas_price_wei),
                    next_retry_at = ?,
                    last_error = ?
                WHERE id = ?
                """,
                (
                    self._now_iso(),
                    "retryable",
                    swap_tx_hash,
                    approve_tx_hash,
                    approve_nonce,
                    approve_gas_price_wei,
                    swap_tx_hash,
                    swap_nonce,
                    swap_gas_price_wei,
                    next_retry_at,
                    error,
                    pending_tx_id,
                ),
            )

    def mark_pending_tx_failed(
        self,
        pending_tx_id: int,
        error: str,
        *,
        approve_tx_hash: str | None = None,
        approve_nonce: int | None = None,
        approve_gas_price_wei: int | None = None,
        swap_tx_hash: str | None = None,
        swap_nonce: int | None = None,
        swap_gas_price_wei: int | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE pending_txs
                SET updated_at = ?,
                    status = ?,
                    tx_hash = COALESCE(?, tx_hash),
                    approve_tx_hash = COALESCE(?, approve_tx_hash),
                    approve_nonce = COALESCE(?, approve_nonce),
                    approve_gas_price_wei = COALESCE(?, approve_gas_price_wei),
                    swap_tx_hash = COALESCE(?, swap_tx_hash),
                    swap_nonce = COALESCE(?, swap_nonce),
                    swap_gas_price_wei = COALESCE(?, swap_gas_price_wei),
                    next_retry_at = NULL,
                    last_error = ?
                WHERE id = ?
                """,
                (
                    self._now_iso(),
                    "failed",
                    swap_tx_hash,
                    approve_tx_hash,
                    approve_nonce,
                    approve_gas_price_wei,
                    swap_tx_hash,
                    swap_nonce,
                    swap_gas_price_wei,
                    error,
                    pending_tx_id,
                ),
            )

    def mark_pending_tx_orphaned(self, pending_tx_id: int, reason: str) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE pending_txs
                SET updated_at = ?, status = ?, next_retry_at = NULL, last_error = ?
                WHERE id = ?
                """,
                (
                    self._now_iso(),
                    "orphaned",
                    reason,
                    pending_tx_id,
                ),
            )

    def mark_pending_tx_inflight(
        self,
        pending_tx_id: int,
        note: str,
        *,
        approve_tx_hash: str | None = None,
        approve_nonce: int | None = None,
        approve_gas_price_wei: int | None = None,
        swap_tx_hash: str | None = None,
        swap_nonce: int | None = None,
        swap_gas_price_wei: int | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE pending_txs
                SET updated_at = ?,
                    status = ?,
                    tx_hash = COALESCE(?, tx_hash),
                    approve_tx_hash = COALESCE(?, approve_tx_hash),
                    approve_nonce = COALESCE(?, approve_nonce),
                    approve_gas_price_wei = COALESCE(?, approve_gas_price_wei),
                    swap_tx_hash = COALESCE(?, swap_tx_hash),
                    swap_nonce = COALESCE(?, swap_nonce),
                    swap_gas_price_wei = COALESCE(?, swap_gas_price_wei),
                    next_retry_at = NULL,
                    last_error = ?
                WHERE id = ?
                """,
                (
                    self._now_iso(),
                    "submitted",
                    swap_tx_hash,
                    approve_tx_hash,
                    approve_nonce,
                    approve_gas_price_wei,
                    swap_tx_hash,
                    swap_nonce,
                    swap_gas_price_wei,
                    note,
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

    def load_pending_txs(self, statuses: tuple[str, ...]) -> list[sqlite3.Row]:
        if not statuses:
            return []
        placeholders = ", ".join("?" for _ in statuses)
        cursor = self.conn.execute(
            f"""
            SELECT
                id,
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
                attempt_count,
                submitted_at,
                confirmed_at,
                next_retry_at,
                approve_token_address,
                approve_tx_hash,
                approve_nonce,
                approve_gas_price_wei,
                swap_tx_hash,
                swap_nonce,
                swap_gas_price_wei,
                receipt_status,
                estimated_gas_usd,
                fill_price,
                fill_base_qty,
                fill_quote_value,
                path_json,
                last_error
            FROM pending_txs
            WHERE status IN ({placeholders})
            ORDER BY created_at ASC, id ASC
            """,
            statuses,
        )
        return list(cursor.fetchall())

    def count_open_pending_txs(
        self,
        *,
        symbol: str | None = None,
    ) -> int:
        statuses = ("prepared", "submitted", "retryable", "orphaned")
        placeholders = ", ".join("?" for _ in statuses)
        params: list[str] = list(statuses)
        symbol_clause = ""
        if symbol is not None:
            symbol_clause = " AND symbol = ?"
            params.append(symbol)
        cursor = self.conn.execute(
            f"""
            SELECT COUNT(*)
            FROM pending_txs
            WHERE status IN ({placeholders}){symbol_clause}
            """,
            tuple(params),
        )
        return int(cursor.fetchone()[0])

    def record_execution_failure(
        self,
        *,
        symbol: str,
        side: str,
        message: str,
        pending_tx_id: int | None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO execution_failures (ts, symbol, side, pending_tx_id, message)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    self._now_iso(),
                    symbol,
                    side,
                    pending_tx_id,
                    message,
                ),
            )

    def record_execution_attempt(
        self,
        *,
        symbol: str,
        side: str,
        estimated_gas_usd: float,
        pending_tx_id: int | None,
    ) -> int:
        with self.conn:
            cursor = self.conn.execute(
                """
                INSERT INTO execution_attempts (
                    ts,
                    symbol,
                    side,
                    pending_tx_id,
                    estimated_gas_usd,
                    approve_tx_hash,
                    approve_nonce,
                    approve_gas_price_wei,
                    swap_tx_hash,
                    swap_nonce,
                    swap_gas_price_wei,
                    actual_gas_usd
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._now_iso(),
                    symbol,
                    side,
                    pending_tx_id,
                    estimated_gas_usd,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                    None,
                ),
            )
            return int(cursor.lastrowid)

    def update_execution_attempt_result(
        self,
        execution_attempt_id: int,
        *,
        approve_tx_hash: str | None = None,
        approve_nonce: int | None = None,
        approve_gas_price_wei: int | None = None,
        swap_tx_hash: str | None = None,
        swap_nonce: int | None = None,
        swap_gas_price_wei: int | None = None,
        actual_gas_usd: float | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE execution_attempts
                SET approve_tx_hash = COALESCE(?, approve_tx_hash),
                    approve_nonce = COALESCE(?, approve_nonce),
                    approve_gas_price_wei = COALESCE(?, approve_gas_price_wei),
                    swap_tx_hash = COALESCE(?, swap_tx_hash),
                    swap_nonce = COALESCE(?, swap_nonce),
                    swap_gas_price_wei = COALESCE(?, swap_gas_price_wei),
                    actual_gas_usd = COALESCE(?, actual_gas_usd)
                WHERE id = ?
                """,
                (
                    approve_tx_hash,
                    approve_nonce,
                    approve_gas_price_wei,
                    swap_tx_hash,
                    swap_nonce,
                    swap_gas_price_wei,
                    actual_gas_usd,
                    execution_attempt_id,
                ),
            )

    def update_latest_execution_attempt_for_pending_tx(
        self,
        pending_tx_id: int,
        *,
        approve_tx_hash: str | None = None,
        approve_nonce: int | None = None,
        approve_gas_price_wei: int | None = None,
        swap_tx_hash: str | None = None,
        swap_nonce: int | None = None,
        swap_gas_price_wei: int | None = None,
        actual_gas_usd: float | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                UPDATE execution_attempts
                SET approve_tx_hash = COALESCE(?, approve_tx_hash),
                    approve_nonce = COALESCE(?, approve_nonce),
                    approve_gas_price_wei = COALESCE(?, approve_gas_price_wei),
                    swap_tx_hash = COALESCE(?, swap_tx_hash),
                    swap_nonce = COALESCE(?, swap_nonce),
                    swap_gas_price_wei = COALESCE(?, swap_gas_price_wei),
                    actual_gas_usd = COALESCE(?, actual_gas_usd)
                WHERE id = (
                    SELECT id
                    FROM execution_attempts
                    WHERE pending_tx_id = ?
                    ORDER BY id DESC
                    LIMIT 1
                )
                """,
                (
                    approve_tx_hash,
                    approve_nonce,
                    approve_gas_price_wei,
                    swap_tx_hash,
                    swap_nonce,
                    swap_gas_price_wei,
                    actual_gas_usd,
                    pending_tx_id,
                ),
            )

    def count_fills_for_symbol_since(self, symbol: str, since_ts: str) -> int:
        cursor = self.conn.execute(
            """
            SELECT COUNT(*)
            FROM fills
            WHERE symbol = ? AND ts >= ?
            """,
            (symbol, since_ts),
        )
        return int(cursor.fetchone()[0])

    def count_execution_failures_for_symbol_since(self, symbol: str, since_ts: str) -> int:
        cursor = self.conn.execute(
            """
            SELECT COUNT(*)
            FROM execution_failures
            WHERE symbol = ? AND ts >= ?
            """,
            (symbol, since_ts),
        )
        return int(cursor.fetchone()[0])

    def sum_realized_pnl_since(self, since_ts: str) -> float:
        cursor = self.conn.execute(
            """
            SELECT COALESCE(SUM(realized_pnl), 0.0)
            FROM fills
            WHERE ts >= ?
            """,
            (since_ts,),
        )
        return float(cursor.fetchone()[0] or 0.0)

    def sum_execution_attempt_gas_since(self, since_ts: str) -> float:
        cursor = self.conn.execute(
            """
            SELECT COALESCE(SUM(COALESCE(actual_gas_usd, 0.0)), 0.0)
            FROM execution_attempts
            WHERE ts >= ?
            """,
            (since_ts,),
        )
        return float(cursor.fetchone()[0] or 0.0)

    def _now_iso(self) -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _ensure_pending_tx_columns(self) -> None:
        self._ensure_column("pending_txs", "last_error", "TEXT")
        self._ensure_column("pending_txs", "attempt_count", "INTEGER NOT NULL DEFAULT 0")
        self._ensure_column("pending_txs", "submitted_at", "TEXT")
        self._ensure_column("pending_txs", "confirmed_at", "TEXT")
        self._ensure_column("pending_txs", "next_retry_at", "TEXT")
        self._ensure_column("pending_txs", "approve_tx_hash", "TEXT")
        self._ensure_column("pending_txs", "approve_nonce", "INTEGER")
        self._ensure_column("pending_txs", "approve_gas_price_wei", "INTEGER")
        self._ensure_column("pending_txs", "swap_tx_hash", "TEXT")
        self._ensure_column("pending_txs", "swap_nonce", "INTEGER")
        self._ensure_column("pending_txs", "swap_gas_price_wei", "INTEGER")
        self._ensure_column("pending_txs", "receipt_status", "INTEGER")
        self._ensure_column("pending_txs", "estimated_gas_usd", "REAL")
        self._ensure_column("pending_txs", "fill_price", "REAL")
        self._ensure_column("pending_txs", "fill_base_qty", "REAL")
        self._ensure_column("pending_txs", "fill_quote_value", "REAL")
        self._ensure_execution_attempt_columns()

    def _ensure_execution_attempt_columns(self) -> None:
        self._ensure_column("execution_attempts", "approve_tx_hash", "TEXT")
        self._ensure_column("execution_attempts", "approve_nonce", "INTEGER")
        self._ensure_column("execution_attempts", "approve_gas_price_wei", "INTEGER")
        self._ensure_column("execution_attempts", "swap_tx_hash", "TEXT")
        self._ensure_column("execution_attempts", "swap_nonce", "INTEGER")
        self._ensure_column("execution_attempts", "swap_gas_price_wei", "INTEGER")
        self._ensure_column("execution_attempts", "actual_gas_usd", "REAL")

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {
            str(row["name"])
            for row in self.conn.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column in columns:
            return
        with self.conn:
            self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
