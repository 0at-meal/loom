"""Ledger records the outcome kind and approval belief; old databases are migrated (F-04)."""

from __future__ import annotations

import pathlib
import sqlite3

import aiosqlite
import pytest

from data_layer.sqlite_logger import MetricsLogger, SQLiteMetricsStore
from tests.data_layer.test_sqlite_logger import _create_mock_routing_result

# The ledger as created before PR #5: no outcome or approval columns.
OLD_SCHEMA = """
CREATE TABLE transactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id TEXT NOT NULL UNIQUE,
    timestamp REAL NOT NULL,
    chosen_acquirer TEXT NOT NULL,
    allocation_weight REAL NOT NULL,
    status TEXT NOT NULL,
    authorized INTEGER NOT NULL,
    success INTEGER NOT NULL,
    decline_code TEXT,
    routing_latency_ms REAL NOT NULL,
    acquirer_latency_ms REAL NOT NULL,
    total_latency_ms REAL NOT NULL,
    smoothed_allocation_json TEXT NOT NULL,
    target_allocation_json TEXT,
    thompson_samples_json TEXT NOT NULL,
    pid_diagnostics_json TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE TABLE acquirer_outcomes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id TEXT NOT NULL,
    acquirer_id TEXT NOT NULL,
    timestamp REAL NOT NULL,
    success INTEGER NOT NULL,
    alpha REAL NOT NULL,
    beta REAL NOT NULL,
    health_score REAL NOT NULL,
    expected_success_rate REAL NOT NULL,
    success_count INTEGER NOT NULL,
    failure_count INTEGER NOT NULL,
    total_count INTEGER NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
INSERT INTO transactions (transaction_id, timestamp, chosen_acquirer, allocation_weight,
    status, authorized, success, routing_latency_ms, acquirer_latency_ms, total_latency_ms,
    smoothed_allocation_json, thompson_samples_json)
VALUES ('tx_old', 1.0, 'acquirer_alpha', 1.0, 'AUTHORIZED', 1, 1, 0, 0, 0, '{}', '{}');
"""


def _columns(db_file: str, table: str) -> set[str]:
    with sqlite3.connect(db_file) as conn:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


@pytest.mark.asyncio
async def test_new_ledger_records_outcome_and_approval(tmp_path: pathlib.Path) -> None:
    """A logged result carries its outcome kind and both beliefs."""
    db_file = str(tmp_path / "ledger.db")
    logger = MetricsLogger(db_path=db_file, flush_interval_sec=0.01)
    await logger.start()
    logger.log_routing_result(_create_mock_routing_result(tx_id="tx_new"))
    await logger.close()

    async with aiosqlite.connect(db_file) as conn:
        cur = await conn.execute("SELECT outcome FROM transactions WHERE transaction_id='tx_new'")
        assert (await cur.fetchone()) == ("AUTHORIZED",)
        cur = await conn.execute(
            "SELECT approval_alpha, approval_beta, expected_psr FROM acquirer_outcomes"
        )
        row = await cur.fetchone()
    assert row is not None
    approval_alpha, approval_beta, expected_psr = row
    assert approval_alpha >= 1.0 and approval_beta >= 1.0
    assert 0.0 < expected_psr < 1.0


@pytest.mark.asyncio
async def test_old_ledger_is_migrated_in_place(tmp_path: pathlib.Path) -> None:
    """Opening a pre-PR-5 ledger adds the new columns and keeps existing rows."""
    db_file = str(tmp_path / "old.db")
    with sqlite3.connect(db_file) as conn:
        conn.executescript(OLD_SCHEMA)

    logger = MetricsLogger(db_path=db_file, flush_interval_sec=0.01)
    await logger.start()
    logger.log_routing_result(_create_mock_routing_result(tx_id="tx_after"))
    await logger.close()

    assert "outcome" in _columns(db_file, "transactions")
    assert {"approval_alpha", "approval_beta", "expected_psr"} <= _columns(
        db_file, "acquirer_outcomes"
    )
    with sqlite3.connect(db_file) as conn:
        rows = dict(conn.execute("SELECT transaction_id, outcome FROM transactions"))
    assert rows == {"tx_old": None, "tx_after": "AUTHORIZED"}


def test_sync_store_migrates_too(tmp_path: pathlib.Path) -> None:
    """The synchronous store used by the benchmark applies the same migration."""
    db_file = str(tmp_path / "old_sync.db")
    with sqlite3.connect(db_file) as conn:
        conn.executescript(OLD_SCHEMA)
    SQLiteMetricsStore(db_path=db_file).close()
    assert "outcome" in _columns(db_file, "transactions")


def test_schema_has_a_single_source() -> None:
    """schema.sql is the only schema definition and ships with the package."""
    import data_layer.sqlite_logger as sl

    assert not hasattr(sl, "EMBEDDED_SCHEMA_SQL")
    assert "outcome" in sl.load_schema_sql()
    pyproject = pathlib.Path(__file__).resolve().parents[2] / "pyproject.toml"
    assert 'data_layer = ["schema.sql"]' in pyproject.read_text(encoding="utf-8")
