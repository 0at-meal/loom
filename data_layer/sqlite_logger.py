"""SQLite append-only outcome ledger and batch-buffered metrics logger (Phase 5 Ticket C).

Conforms strictly to docs/CONSTITUTION.md:
1. Tables are named in snake_case, plural: `transactions` and `acquirer_outcomes`.
2. Append-only enforcement: no UPDATE or DELETE paths exist in this module.
3. Database engine-level triggers abort any external UPDATE or DELETE attempts.
4. Provides analytical query methods for Phase 6 PSR-lift validation.
"""

from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import sqlite3
import time
from collections.abc import Sequence
from typing import Any

import aiosqlite

from data_layer.config import DataLayerConfig
from router_core.models import RoutingResult

logger = logging.getLogger("loom.data_layer.sqlite_logger")

SCHEMA_FILE_PATH = pathlib.Path(__file__).parent / "schema.sql"

# Embedded fallback schema ensuring zero-dependency bootstrapping
EMBEDDED_SCHEMA_SQL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA foreign_keys = ON;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS transactions (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id              TEXT NOT NULL UNIQUE,
    timestamp                   REAL NOT NULL,
    chosen_acquirer             TEXT NOT NULL,
    allocation_weight           REAL NOT NULL,
    status                      TEXT NOT NULL CHECK(status IN ('AUTHORIZED', 'DECLINED', 'ERROR')),
    authorized                  INTEGER NOT NULL CHECK(authorized IN (0, 1)),
    success                     INTEGER NOT NULL CHECK(success IN (0, 1)),
    decline_code                TEXT,
    routing_latency_ms          REAL NOT NULL,
    acquirer_latency_ms         REAL NOT NULL,
    total_latency_ms            REAL NOT NULL,
    smoothed_allocation_json    TEXT NOT NULL,
    target_allocation_json      TEXT,
    thompson_samples_json       TEXT NOT NULL,
    pid_diagnostics_json        TEXT,
    error_message               TEXT,
    created_at                  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE INDEX IF NOT EXISTS idx_transactions_timestamp
    ON transactions(timestamp);

CREATE INDEX IF NOT EXISTS idx_transactions_acquirer
    ON transactions(chosen_acquirer);

CREATE INDEX IF NOT EXISTS idx_transactions_status
    ON transactions(status);

CREATE INDEX IF NOT EXISTS idx_transactions_created_at
    ON transactions(created_at);

CREATE TABLE IF NOT EXISTS acquirer_outcomes (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id              TEXT NOT NULL,
    acquirer_id                 TEXT NOT NULL,
    timestamp                   REAL NOT NULL,
    success                     INTEGER NOT NULL CHECK(success IN (0, 1)),
    alpha                       REAL NOT NULL,
    beta                        REAL NOT NULL,
    health_score                REAL NOT NULL,
    expected_success_rate       REAL NOT NULL,
    success_count               INTEGER NOT NULL,
    failure_count               INTEGER NOT NULL,
    total_count                 INTEGER NOT NULL,
    created_at                  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY(transaction_id) REFERENCES transactions(transaction_id)
);

CREATE INDEX IF NOT EXISTS idx_acquirer_outcomes_acquirer_ts
    ON acquirer_outcomes(acquirer_id, timestamp);

CREATE INDEX IF NOT EXISTS idx_acquirer_outcomes_tx_id
    ON acquirer_outcomes(transaction_id);

CREATE TRIGGER IF NOT EXISTS prevent_transactions_update
BEFORE UPDATE ON transactions
BEGIN
    SELECT RAISE(
        ABORT,
        'Transactions table is append-only: UPDATE operations are strictly prohibited'
    );
END;

CREATE TRIGGER IF NOT EXISTS prevent_transactions_delete
BEFORE DELETE ON transactions
BEGIN
    SELECT RAISE(
        ABORT,
        'Transactions table is append-only: DELETE operations are strictly prohibited'
    );
END;

CREATE TRIGGER IF NOT EXISTS prevent_acquirer_outcomes_update
BEFORE UPDATE ON acquirer_outcomes
BEGIN
    SELECT RAISE(
        ABORT,
        'Acquirer outcomes table is append-only: UPDATE operations are strictly prohibited'
    );
END;

CREATE TRIGGER IF NOT EXISTS prevent_acquirer_outcomes_delete
BEFORE DELETE ON acquirer_outcomes
BEGIN
    SELECT RAISE(
        ABORT,
        'Acquirer outcomes table is append-only: DELETE operations are strictly prohibited'
    );
END;
"""


def load_schema_sql() -> str:
    """Load SQL DDL from schema.sql if available on disk; otherwise use embedded schema."""
    if SCHEMA_FILE_PATH.exists():
        try:
            return SCHEMA_FILE_PATH.read_text(encoding="utf-8")
        except OSError:
            pass
    return EMBEDDED_SCHEMA_SQL


def extract_row_tuples(
    result: RoutingResult,
) -> tuple[tuple[Any, ...], tuple[Any, ...]]:
    """Extract relational row parameters for transactions and acquirer_outcomes."""
    # Determine instantaneous allocation weight of the selected route
    weight: float = 1.0
    if result.smoothed_allocation and result.selected_acquirer in result.smoothed_allocation:
        weight = result.smoothed_allocation[result.selected_acquirer]
    elif result.target_allocation and result.selected_acquirer in result.target_allocation:
        weight = result.target_allocation[result.selected_acquirer]

    # Extract decline code or error message
    decline_code: str | None = None
    if result.response_payload is not None:
        decline_code = result.response_payload.decline_code
    elif result.error_message is not None:
        decline_code = result.error_message

    # Format PID diagnostics if present
    pid_diag_json: str | None = None
    if result.pid_diagnostics is not None:
        pid_diag_json = json.dumps(
            {
                "error": result.pid_diagnostics.error,
                "p_term": result.pid_diagnostics.p_term,
                "i_term": result.pid_diagnostics.i_term,
                "d_term": result.pid_diagnostics.d_term,
                "raw_delta": result.pid_diagnostics.raw_delta,
                "pre_projection_allocation": result.pid_diagnostics.pre_projection_allocation,
            }
        )

    tx_tuple = (
        result.transaction_id,
        result.timestamp,
        result.selected_acquirer,
        weight,
        result.status,
        1 if result.authorized else 0,
        1 if result.success else 0,
        decline_code,
        result.routing_latency_ms,
        result.acquirer_latency_ms,
        result.total_latency_ms,
        json.dumps(result.smoothed_allocation or {}),
        json.dumps(result.target_allocation) if result.target_allocation is not None else None,
        json.dumps(result.thompson_samples),
        pid_diag_json,
        result.error_message,
    )

    snap = result.state_snapshot
    outcome_tuple = (
        result.transaction_id,
        result.selected_acquirer,
        result.timestamp,
        1 if result.success else 0,
        snap.alpha,
        snap.beta,
        snap.health_score,
        snap.expected_success_rate,
        snap.success_count,
        snap.failure_count,
        snap.total_count,
    )

    return tx_tuple, outcome_tuple


SQL_INSERT_TRANSACTION = """
INSERT INTO transactions (
    transaction_id,
    timestamp,
    chosen_acquirer,
    allocation_weight,
    status,
    authorized,
    success,
    decline_code,
    routing_latency_ms,
    acquirer_latency_ms,
    total_latency_ms,
    smoothed_allocation_json,
    target_allocation_json,
    thompson_samples_json,
    pid_diagnostics_json,
    error_message
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""

SQL_INSERT_OUTCOME = """
INSERT INTO acquirer_outcomes (
    transaction_id,
    acquirer_id,
    timestamp,
    success,
    alpha,
    beta,
    health_score,
    expected_success_rate,
    success_count,
    failure_count,
    total_count
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
"""


class MetricsLogger:
    """Asynchronous, batch-buffered SQLite metrics logger for high-throughput routing."""

    def __init__(
        self,
        db_path: str | None = None,
        config: DataLayerConfig | None = None,
        batch_size: int | None = None,
        flush_interval_sec: float | None = None,
        max_queue_size: int | None = None,
        raise_on_error: bool = False,
    ) -> None:
        """Initialize async writer with in-memory buffer queue and batch flush thresholds."""
        self._config = config or DataLayerConfig()
        self._db_path = db_path or self._config.sqlite_db_path
        self._batch_size = batch_size or self._config.sqlite_batch_size
        self._flush_interval_sec = flush_interval_sec or self._config.sqlite_flush_interval_sec
        self._max_queue_size = max_queue_size or self._config.sqlite_max_queue_size
        self._raise_on_error = raise_on_error

        self._queue: asyncio.Queue[RoutingResult] = asyncio.Queue(maxsize=self._max_queue_size)
        self._conn: aiosqlite.Connection | None = None
        self._drain_task: asyncio.Task[None] | None = None
        self._started = False
        self._stopping = False
        self._write_lock = asyncio.Lock()
        self._total_written = 0

    @property
    def db_path(self) -> str:
        """Return target SQLite database path."""
        return self._db_path

    @property
    def total_written(self) -> int:
        """Return count of transactions written to SQLite storage."""
        return self._total_written

    async def start(self) -> None:
        """Initialize database schema and launch background queue consumer."""
        if self._started:
            return

        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row

        # Initialize schema and constitutional triggers
        schema_sql = load_schema_sql()
        await self._conn.executescript(schema_sql)
        await self._conn.commit()

        self._started = True
        self._stopping = False
        self._drain_task = asyncio.create_task(self._drain_loop())
        logger.debug(
            "MetricsLogger started (db=%s, batch_size=%d, flush_interval=%.3fs)",
            self._db_path,
            self._batch_size,
            self._flush_interval_sec,
        )

    def log_routing_result(self, result: RoutingResult) -> None:
        """Enqueue RoutingResult into buffer without blocking the transaction hot-path."""
        if not self._started or self._stopping:
            if self._raise_on_error:
                raise RuntimeError("MetricsLogger is not running or is shutting down")
            logger.warning(
                "MetricsLogger dropped record tx=%s (not started)",
                result.transaction_id,
            )
            return

        try:
            self._queue.put_nowait(result)
        except asyncio.QueueFull:
            logger.error(
                "MetricsLogger queue is full (size=%d), dropping tx=%s",
                self._max_queue_size,
                result.transaction_id,
            )
            if self._raise_on_error:
                raise

    async def log_routing_result_async(self, result: RoutingResult) -> None:
        """Asynchronously enqueue result, waiting if buffer is full."""
        if not self._started or self._stopping:
            if self._raise_on_error:
                raise RuntimeError("MetricsLogger is not running or is shutting down")
            return
        await self._queue.put(result)

    async def _drain_loop(self) -> None:
        """Background coroutine accumulating batches and flushing to SQLite."""
        while not self._stopping or not self._queue.empty():
            batch: list[RoutingResult] = []
            deadline = time.perf_counter() + self._flush_interval_sec

            while len(batch) < self._batch_size:
                timeout = max(0.001, deadline - time.perf_counter())
                try:
                    item = await asyncio.wait_for(self._queue.get(), timeout=timeout)
                    batch.append(item)
                    self._queue.task_done()
                except TimeoutError:
                    break

            if batch:
                await self._write_batch(batch)

    async def _write_batch(self, batch: Sequence[RoutingResult]) -> None:
        """Write a batch of RoutingResults within an atomic SQLite transaction."""
        if self._conn is None or not batch:
            return

        tx_rows = []
        outcome_rows = []
        for res in batch:
            t_row, o_row = extract_row_tuples(res)
            tx_rows.append(t_row)
            outcome_rows.append(o_row)

        async with self._write_lock:
            try:
                # Atomically commit both tables in a single transaction
                await self._conn.execute("BEGIN TRANSACTION;")
                await self._conn.executemany(SQL_INSERT_TRANSACTION, tx_rows)
                await self._conn.executemany(SQL_INSERT_OUTCOME, outcome_rows)
                await self._conn.commit()
                self._total_written += len(batch)
                logger.debug("Committed batch of %d records to SQLite", len(batch))
            except Exception as exc:
                try:
                    await self._conn.execute("ROLLBACK;")
                except (aiosqlite.Error, sqlite3.Error):
                    pass
                logger.error("Failed to insert batch of %d records: %s", len(batch), exc)
                if self._raise_on_error:
                    raise

    async def flush(self) -> None:
        """Flush all pending items from the in-memory queue to disk immediately."""
        items: list[RoutingResult] = []
        while not self._queue.empty():
            try:
                item = self._queue.get_nowait()
                items.append(item)
                self._queue.task_done()
            except asyncio.QueueEmpty:
                break

        if items:
            await self._write_batch(items)

    async def close(self) -> None:
        """Flush pending items, terminate background drain worker, and close connection."""
        if not self._started:
            return

        self._stopping = True
        await self.flush()

        if self._drain_task is not None:
            self._drain_task.cancel()
            try:
                await self._drain_task
            except asyncio.CancelledError:
                pass
            self._drain_task = None

        if self._conn is not None:
            await self._conn.close()
            self._conn = None

        self._started = False
        logger.debug("MetricsLogger closed successfully")

    async def __aenter__(self) -> MetricsLogger:
        await self.start()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

    # -------------------------------------------------------------------------
    # Analytical Query Methods for Phase 6 PSR-Lift Validation (Strictly Read-Only)
    # -------------------------------------------------------------------------

    async def get_transaction_count(self) -> int:
        """Return total number of transaction records logged in SQLite."""
        if self._conn is None:
            return 0
        async with self._conn.execute("SELECT COUNT(*) FROM transactions;") as cursor:
            row = await cursor.fetchone()
            return int(row[0]) if row else 0

    async def get_transaction(self, transaction_id: str) -> dict[str, Any] | None:
        """Read a single transaction row by transaction_id."""
        if self._conn is None:
            return None
        async with self._conn.execute(
            "SELECT * FROM transactions WHERE transaction_id = ?;",
            (transaction_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def get_all_transactions(
        self,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return list of transactions in chronological order."""
        if self._conn is None:
            return []
        query = "SELECT * FROM transactions ORDER BY id ASC"
        params: list[Any] = []
        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])

        async with self._conn.execute(query, params) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_outcomes_for_acquirer(self, acquirer_id: str) -> list[dict[str, Any]]:
        """Return chronological state outcomes for a specific acquirer."""
        if self._conn is None:
            return []
        async with self._conn.execute(
            "SELECT * FROM acquirer_outcomes WHERE acquirer_id = ? ORDER BY id ASC;",
            (acquirer_id,),
        ) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def get_psr_metrics(
        self,
        acquirer_id: str | None = None,
        start_time: float | None = None,
        end_time: float | None = None,
    ) -> dict[str, Any]:
        """Aggregate Payment Success Rate (PSR) and latency metrics for Phase 6 analysis."""
        if self._conn is None:
            return {
                "total_transactions": 0,
                "authorized_count": 0,
                "declined_count": 0,
                "psr": 0.0,
            }

        conditions = []
        params: list[Any] = []
        if acquirer_id is not None:
            conditions.append("chosen_acquirer = ?")
            params.append(acquirer_id)
        if start_time is not None:
            conditions.append("timestamp >= ?")
            params.append(start_time)
        if end_time is not None:
            conditions.append("timestamp <= ?")
            params.append(end_time)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        query = f"""
        SELECT
            COUNT(*) as total_count,
            COALESCE(SUM(authorized), 0) as authorized_count,
            COALESCE(SUM(CASE WHEN status = 'DECLINED' THEN 1 ELSE 0 END), 0) as declined_count,
            COALESCE(SUM(CASE WHEN status = 'ERROR' THEN 1 ELSE 0 END), 0) as error_count,
            COALESCE(AVG(allocation_weight), 0.0) as avg_allocation_weight,
            COALESCE(AVG(total_latency_ms), 0.0) as avg_total_latency_ms,
            COALESCE(AVG(acquirer_latency_ms), 0.0) as avg_acquirer_latency_ms,
            COALESCE(AVG(routing_latency_ms), 0.0) as avg_routing_latency_ms
        FROM transactions
        {where_clause};
        """

        async with self._conn.execute(query, params) as cursor:
            row = await cursor.fetchone()

        if not row or row["total_count"] == 0:
            return {
                "total_transactions": 0,
                "authorized_count": 0,
                "declined_count": 0,
                "error_count": 0,
                "psr": 0.0,
                "avg_allocation_weight": 0.0,
                "avg_total_latency_ms": 0.0,
                "avg_acquirer_latency_ms": 0.0,
                "avg_routing_latency_ms": 0.0,
            }

        total = int(row["total_count"])
        auth = int(row["authorized_count"])
        psr = auth / total if total > 0 else 0.0

        return {
            "total_transactions": total,
            "authorized_count": auth,
            "declined_count": int(row["declined_count"]),
            "error_count": int(row["error_count"]),
            "psr": psr,
            "avg_allocation_weight": float(row["avg_allocation_weight"]),
            "avg_total_latency_ms": float(row["avg_total_latency_ms"]),
            "avg_acquirer_latency_ms": float(row["avg_acquirer_latency_ms"]),
            "avg_routing_latency_ms": float(row["avg_routing_latency_ms"]),
        }


class SQLiteMetricsStore:
    """Synchronous SQLite ledger for direct logging, local CLI tooling, and testing."""

    def __init__(
        self,
        db_path: str = "loom_metrics.db",
        config: DataLayerConfig | None = None,
    ) -> None:
        """Initialize synchronous store and ensure schema is established."""
        self._config = config or DataLayerConfig()
        self._db_path = db_path
        self._conn = sqlite3.connect(self._db_path)
        self._conn.row_factory = sqlite3.Row

        # Setup schema and constitutional triggers
        self._conn.executescript(load_schema_sql())
        self._conn.commit()

    @property
    def db_path(self) -> str:
        """Return database path."""
        return self._db_path

    def log_routing_result(self, result: RoutingResult) -> None:
        """Synchronously log a single routing result in an atomic transaction."""
        t_row, o_row = extract_row_tuples(result)
        with self._conn:
            self._conn.execute(SQL_INSERT_TRANSACTION, t_row)
            self._conn.execute(SQL_INSERT_OUTCOME, o_row)

    def log_batch(self, results: Sequence[RoutingResult]) -> None:
        """Synchronously log a sequence of routing results within a single transaction."""
        if not results:
            return
        t_rows = []
        o_rows = []
        for r in results:
            t, o = extract_row_tuples(r)
            t_rows.append(t)
            o_rows.append(o)
        with self._conn:
            self._conn.executemany(SQL_INSERT_TRANSACTION, t_rows)
            self._conn.executemany(SQL_INSERT_OUTCOME, o_rows)

    def get_transaction_count(self) -> int:
        """Return count of transactions in storage."""
        cursor = self._conn.execute("SELECT COUNT(*) FROM transactions;")
        row = cursor.fetchone()
        return int(row[0]) if row else 0

    def get_transaction(self, transaction_id: str) -> dict[str, Any] | None:
        """Read a single transaction by transaction_id."""
        cursor = self._conn.execute(
            "SELECT * FROM transactions WHERE transaction_id = ?;",
            (transaction_id,),
        )
        row = cursor.fetchone()
        return dict(row) if row else None

    def get_all_transactions(
        self,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Return list of all transactions."""
        query = "SELECT * FROM transactions ORDER BY id ASC"
        params: list[Any] = []
        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        cursor = self._conn.execute(query, params)
        return [dict(r) for r in cursor.fetchall()]

    def get_outcomes_for_acquirer(self, acquirer_id: str) -> list[dict[str, Any]]:
        """Return state mutations for an acquirer."""
        cursor = self._conn.execute(
            "SELECT * FROM acquirer_outcomes WHERE acquirer_id = ? ORDER BY id ASC;",
            (acquirer_id,),
        )
        return [dict(r) for r in cursor.fetchall()]

    def get_psr_metrics(
        self,
        acquirer_id: str | None = None,
        start_time: float | None = None,
        end_time: float | None = None,
    ) -> dict[str, Any]:
        """Aggregate Payment Success Rate and latencies synchronously."""
        conditions = []
        params: list[Any] = []
        if acquirer_id is not None:
            conditions.append("chosen_acquirer = ?")
            params.append(acquirer_id)
        if start_time is not None:
            conditions.append("timestamp >= ?")
            params.append(start_time)
        if end_time is not None:
            conditions.append("timestamp <= ?")
            params.append(end_time)

        where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""

        query = f"""
        SELECT
            COUNT(*) as total_count,
            COALESCE(SUM(authorized), 0) as authorized_count,
            COALESCE(SUM(CASE WHEN status = 'DECLINED' THEN 1 ELSE 0 END), 0) as declined_count,
            COALESCE(SUM(CASE WHEN status = 'ERROR' THEN 1 ELSE 0 END), 0) as error_count,
            COALESCE(AVG(allocation_weight), 0.0) as avg_allocation_weight,
            COALESCE(AVG(total_latency_ms), 0.0) as avg_total_latency_ms,
            COALESCE(AVG(acquirer_latency_ms), 0.0) as avg_acquirer_latency_ms,
            COALESCE(AVG(routing_latency_ms), 0.0) as avg_routing_latency_ms
        FROM transactions
        {where_clause};
        """

        cursor = self._conn.execute(query, params)
        row = cursor.fetchone()

        if not row or row["total_count"] == 0:
            return {
                "total_transactions": 0,
                "authorized_count": 0,
                "declined_count": 0,
                "error_count": 0,
                "psr": 0.0,
                "avg_allocation_weight": 0.0,
                "avg_total_latency_ms": 0.0,
                "avg_acquirer_latency_ms": 0.0,
                "avg_routing_latency_ms": 0.0,
            }

        total = int(row["total_count"])
        auth = int(row["authorized_count"])
        psr = auth / total if total > 0 else 0.0

        return {
            "total_transactions": total,
            "authorized_count": auth,
            "declined_count": int(row["declined_count"]),
            "error_count": int(row["error_count"]),
            "psr": psr,
            "avg_allocation_weight": float(row["avg_allocation_weight"]),
            "avg_total_latency_ms": float(row["avg_total_latency_ms"]),
            "avg_acquirer_latency_ms": float(row["avg_acquirer_latency_ms"]),
            "avg_routing_latency_ms": float(row["avg_routing_latency_ms"]),
        }

    def close(self) -> None:
        """Close SQLite database connection."""
        self._conn.close()

    def __enter__(self) -> SQLiteMetricsStore:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()
