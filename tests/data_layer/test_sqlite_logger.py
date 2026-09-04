"""Comprehensive test suite for SQLite append-only outcome ledger (Phase 5 Ticket C).

Validates:
1. Engine-level constitutional triggers blocking UPDATE and DELETE.
2. Complete field capture for Phase 6 PSR-lift comparison.
3. Pipeline guarantee: 1 row per transaction, zero duplicates, zero gaps.
4. Micro-batched async writer lifecycle (enqueue, batch flush, shutdown persistence).
5. Phase 6 analytical read queries and PSR metric aggregations.
6. Module immutability: zero UPDATE or DELETE queries exist in data_layer/sqlite_logger.py.
"""

from __future__ import annotations

import ast
import pathlib
import sqlite3
import time
from typing import Literal

import aiosqlite
import httpx
import pytest

from acquirer_sim.models import AuthorizeRequest, AuthorizeResponse
from data_layer.sqlite_logger import (
    MetricsLogger,
    SQLiteMetricsStore,
    extract_row_tuples,
)
from router_core.models import AcquirerRouteConfig, RouterConfig, RoutingResult
from router_core.pid import PIDConfig, PIDDiagnostics
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig, AcquirerStateSnapshot


def _create_mock_routing_result(
    tx_id: str = "tx_0001",
    acquirer_id: str = "acquirer_alpha",
    success: bool = True,
    smoothed_weight: float = 0.80,
    timestamp: float | None = None,
) -> RoutingResult:
    """Helper to generate a fully populated RoutingResult for tests."""
    ts = timestamp if timestamp is not None else time.time()
    status: Literal["AUTHORIZED", "DECLINED", "ERROR"] = "AUTHORIZED" if success else "DECLINED"
    snap = AcquirerStateSnapshot(
        acquirer_id=acquirer_id,
        alpha=3.0 if success else 1.0,
        beta=1.0 if success else 3.0,
        health_score=0.95 if success else 0.40,
        alpha_prior=1.0,
        beta_prior=1.0,
        success_count=2 if success else 0,
        failure_count=0 if success else 2,
        total_count=2,
        last_updated_at=ts,
    )
    payload = (
        AuthorizeResponse(
            authorized=True,
            status="AUTHORIZED",
            acquirer_id=acquirer_id,
            transaction_id=tx_id,
            simulated_latency_ms=16.5,
            timestamp=ts,
            decline_code=None,
        )
        if success
        else AuthorizeResponse(
            authorized=False,
            status="DECLINED",
            acquirer_id=acquirer_id,
            transaction_id=tx_id,
            simulated_latency_ms=14.0,
            timestamp=ts,
            decline_code="INSUFFICIENT_FUNDS",
        )
    )
    return RoutingResult(
        transaction_id=tx_id,
        selected_acquirer=acquirer_id,
        thompson_samples={"acquirer_alpha": 0.88, "acquirer_beta": 0.62},
        status=status,
        authorized=success,
        success=success,
        response_payload=payload,
        error_message=None if success else "Transaction declined",
        routing_latency_ms=0.15,
        acquirer_latency_ms=16.5,
        total_latency_ms=16.65,
        state_snapshot=snap,
        smoothed_allocation={
            "acquirer_alpha": smoothed_weight,
            "acquirer_beta": round(1.0 - smoothed_weight, 4),
        },
        target_allocation={"acquirer_alpha": 1.0, "acquirer_beta": 0.0},
        pid_diagnostics=PIDDiagnostics(
            error={"acquirer_alpha": 0.20, "acquirer_beta": -0.20},
            p_term={"acquirer_alpha": 0.04, "acquirer_beta": -0.04},
            i_term={"acquirer_alpha": 0.01, "acquirer_beta": -0.01},
            d_term={"acquirer_alpha": 0.0, "acquirer_beta": 0.0},
            raw_delta={"acquirer_alpha": 0.05, "acquirer_beta": -0.05},
            pre_projection_allocation={"acquirer_alpha": 0.85, "acquirer_beta": 0.15},
        ),
        timestamp=ts,
    )


class TestConstitutionalAppendOnlyEnforcement:
    """Validates engine-level and code-level immutability guarantees per CONSTITUTION.md."""

    def test_schema_triggers_prevent_update_on_transactions(self, tmp_path: pathlib.Path) -> None:
        """Verify executing an UPDATE on transactions raises a constitutional abort error."""
        db_file = str(tmp_path / "test_triggers.db")
        store = SQLiteMetricsStore(db_path=db_file)
        res = _create_mock_routing_result(tx_id="tx_immut_01")
        store.log_routing_result(res)

        # Attempting UPDATE must be aborted by SQLite engine trigger
        conn = sqlite3.connect(db_file)
        with pytest.raises(sqlite3.IntegrityError, match="Transactions table is append-only"):
            conn.execute(
                "UPDATE transactions SET status = 'DECLINED' WHERE transaction_id = 'tx_immut_01';"
            )

        conn.close()
        store.close()

    def test_schema_triggers_prevent_delete_on_transactions(self, tmp_path: pathlib.Path) -> None:
        """Verify executing a DELETE on transactions raises a constitutional abort error."""
        db_file = str(tmp_path / "test_triggers.db")
        store = SQLiteMetricsStore(db_path=db_file)
        res = _create_mock_routing_result(tx_id="tx_immut_02")
        store.log_routing_result(res)

        conn = sqlite3.connect(db_file)
        with pytest.raises(sqlite3.IntegrityError, match="Transactions table is append-only"):
            conn.execute("DELETE FROM transactions WHERE transaction_id = 'tx_immut_02';")

        conn.close()
        store.close()

    def test_schema_triggers_prevent_update_and_delete_on_acquirer_outcomes(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Verify UPDATE or DELETE on acquirer_outcomes raises a constitutional abort error."""
        db_file = str(tmp_path / "test_triggers.db")
        store = SQLiteMetricsStore(db_path=db_file)
        res = _create_mock_routing_result(tx_id="tx_immut_03")
        store.log_routing_result(res)

        conn = sqlite3.connect(db_file)
        with pytest.raises(sqlite3.IntegrityError, match="Acquirer outcomes table is append-only"):
            conn.execute(
                "UPDATE acquirer_outcomes SET health_score = 0.0 "
                "WHERE transaction_id = 'tx_immut_03';"
            )

        with pytest.raises(sqlite3.IntegrityError, match="Acquirer outcomes table is append-only"):
            conn.execute("DELETE FROM acquirer_outcomes WHERE transaction_id = 'tx_immut_03';")

        conn.close()
        store.close()

    def test_codebase_contains_no_update_or_delete_statements(self) -> None:
        """Verify data_layer/sqlite_logger.py contains zero UPDATE or DELETE queries or methods."""
        source = (pathlib.Path("data_layer") / "sqlite_logger.py").read_text(encoding="utf-8")
        tree = ast.parse(source)

        # 1. Assert no function or method contains update or delete in its identifier
        func_names = [
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        ]
        bad_funcs = [
            name for name in func_names if "UPDATE" in name.upper() or "DELETE" in name.upper()
        ]
        assert not bad_funcs, f"Found mutable function/method in sqlite_logger.py: {bad_funcs}"

        # 2. Assert no SQL DML string passed to execute/executemany contains UPDATE or DELETE
        executed_sqls = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                func = node.func
                if isinstance(func, ast.Attribute) and func.attr in (
                    "execute",
                    "executemany",
                    "executescript",
                ):
                    if (
                        node.args
                        and isinstance(node.args[0], ast.Constant)
                        and isinstance(node.args[0].value, str)
                    ):
                        executed_sqls.append(node.args[0].value)

        bad_sqls = [
            sql
            for sql in executed_sqls
            if ("UPDATE " in sql.upper() or "DELETE " in sql.upper())
            and "TRIGGER" not in sql.upper()
        ]
        assert not bad_sqls, f"Found illegal mutating SQL in sqlite_logger.py: {bad_sqls}"


class TestSchemaFieldCaptureCompleteness:
    """Validates that every field required for Phase 6 PSR-lift comparison is stored."""

    def test_extracted_row_tuples_capture_all_required_columns(self) -> None:
        """Verify extract_row_tuples extracts all 16 transaction columns and 11 outcome columns."""
        res = _create_mock_routing_result(
            tx_id="tx_extract_01",
            success=False,
            smoothed_weight=0.72,
        )
        tx_row, outcome_row = extract_row_tuples(res)

        # 16 columns for transactions
        assert len(tx_row) == 16
        assert tx_row[0] == "tx_extract_01"  # transaction_id
        assert tx_row[1] == res.timestamp  # timestamp
        assert tx_row[2] == "acquirer_alpha"  # chosen_acquirer
        assert tx_row[3] == 0.72  # allocation_weight
        assert tx_row[4] == "DECLINED"  # status
        assert tx_row[5] == 0  # authorized
        assert tx_row[6] == 0  # success
        assert tx_row[7] == "INSUFFICIENT_FUNDS"  # decline_code
        assert tx_row[8] == 0.15  # routing_latency_ms
        assert tx_row[9] == 16.5  # acquirer_latency_ms
        assert tx_row[10] == 16.65  # total_latency_ms

        # 11 columns for acquirer_outcomes
        assert len(outcome_row) == 11
        assert outcome_row[0] == "tx_extract_01"  # transaction_id
        assert outcome_row[1] == "acquirer_alpha"  # acquirer_id
        assert outcome_row[2] == res.timestamp  # timestamp
        assert outcome_row[3] == 0  # success
        assert outcome_row[4] == 1.0  # alpha
        assert outcome_row[5] == 3.0  # beta
        assert outcome_row[6] == 0.40  # health_score

    def test_database_persistence_fidelity(self, tmp_path: pathlib.Path) -> None:
        """Verify logged transaction can be queried back with complete type and value fidelity."""
        db_file = str(tmp_path / "test_fidelity.db")
        store = SQLiteMetricsStore(db_path=db_file)
        res = _create_mock_routing_result(tx_id="tx_fidel_01", success=True, smoothed_weight=0.85)
        store.log_routing_result(res)

        tx = store.get_transaction("tx_fidel_01")
        assert tx is not None
        assert tx["transaction_id"] == "tx_fidel_01"
        assert tx["chosen_acquirer"] == "acquirer_alpha"
        assert tx["allocation_weight"] == 0.85
        assert tx["status"] == "AUTHORIZED"
        assert tx["authorized"] == 1
        assert tx["success"] == 1
        assert tx["decline_code"] is None
        assert tx["routing_latency_ms"] == 0.15
        assert tx["acquirer_latency_ms"] == 16.5
        assert tx["total_latency_ms"] == 16.65
        assert tx["created_at"] is not None

        outcomes = store.get_outcomes_for_acquirer("acquirer_alpha")
        assert len(outcomes) == 1
        assert outcomes[0]["transaction_id"] == "tx_fidel_01"
        assert outcomes[0]["alpha"] == 3.0
        assert outcomes[0]["beta"] == 1.0
        assert outcomes[0]["health_score"] == 0.95

        store.close()


class TestPipelineSingleRowZeroDuplicatesZeroGaps:
    """Validates pipeline contract: exactly 1 log row per transaction, no duplicates, no gaps."""

    @pytest.mark.asyncio
    async def test_100_transactions_produce_exactly_100_rows_without_duplicates_or_gaps(
        self,
        tmp_path: pathlib.Path,
    ) -> None:
        """Verify 100 transactions through BanditRouter produce 100 rows with no gaps or dups."""
        db_file = str(tmp_path / "test_pipeline_100.db")
        logger = MetricsLogger(db_path=db_file, batch_size=20, flush_interval_sec=0.02)
        await logger.start()

        routes = [
            AcquirerRouteConfig(
                acquirer_id="acquirer_alpha",
                base_url="https://mock-acquirer-alpha.internal",
                state_config=AcquirerStateConfig(alpha_prior=1.0, beta_prior=1.0, decay_factor=0.9),
            ),
            AcquirerRouteConfig(
                acquirer_id="acquirer_beta",
                base_url="https://mock-acquirer-beta.internal",
                state_config=AcquirerStateConfig(alpha_prior=1.0, beta_prior=1.0, decay_factor=0.9),
            ),
        ]
        router_config = RouterConfig(
            routes=routes,
            seed=42,
            pid_config=PIDConfig(kp=0.2, ki=0.01, kd=0.05),
        )

        call_count = 0

        def mock_transport(req: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            # Every 4th transaction is declined
            is_auth = (call_count % 4) != 0
            resp_data = {
                "authorized": is_auth,
                "status": "AUTHORIZED" if is_auth else "DECLINED",
                "acquirer_id": "acquirer_alpha",
                "transaction_id": f"tx_{call_count:04d}",
                "simulated_latency_ms": 12.0,
                "timestamp": time.time(),
                "decline_code": None if is_auth else "CARD_EXPIRED",
            }
            return httpx.Response(200, json=resp_data)

        async with httpx.AsyncClient(transport=httpx.MockTransport(mock_transport)) as client:
            router = BanditRouter(
                config=router_config,
                http_client=client,
                metrics_logger=logger,
            )

            num_tx = 100
            expected_tx_ids = [f"tx_{i:04d}" for i in range(1, num_tx + 1)]

            for tx_id in expected_tx_ids:
                req = AuthorizeRequest(
                    transaction_id=tx_id,
                    amount=50.0,
                    currency="USD",
                    merchant_id="merchant_test",
                )
                res = await router.route(req)
                assert res.transaction_id == tx_id

        # Flush logger to disk
        await logger.flush()

        # Verification 1: Exactly 100 transaction rows in SQLite
        count = await logger.get_transaction_count()
        assert count == num_tx, f"Row count mismatch: expected {num_tx}, got {count}"

        # Verification 2: Check all transaction IDs in order
        all_tx = await logger.get_all_transactions()
        actual_tx_ids = [r["transaction_id"] for r in all_tx]

        # Verification 3: Zero duplicates
        assert len(set(actual_tx_ids)) == num_tx, "Duplicate transaction_ids in SQLite ledger!"

        # Verification 4: Zero gaps (exact sequence match)
        assert actual_tx_ids == expected_tx_ids, "Gaps or sequence divergence in transaction log!"

        # Verification 5: Exactly 100 acquirer_outcome rows linked
        async with aiosqlite.connect(db_file) as conn:
            cursor = await conn.execute("SELECT COUNT(*) FROM acquirer_outcomes;")
            row = await cursor.fetchone()
            assert row is not None and row[0] == num_tx

        await logger.close()

    def test_duplicate_transaction_id_is_rejected(self, tmp_path: pathlib.Path) -> None:
        """Verify inserting a duplicate transaction_id violates the UNIQUE constraint."""
        db_file = str(tmp_path / "test_duplicate.db")
        store = SQLiteMetricsStore(db_path=db_file)
        res = _create_mock_routing_result(tx_id="tx_dup_01")
        store.log_routing_result(res)

        # Attempt duplicate insert
        with pytest.raises(sqlite3.IntegrityError, match="UNIQUE constraint failed"):
            store.log_routing_result(res)

        store.close()


class TestMetricsLoggerBatchingAndLifecycle:
    """Validates the micro-batched background queue writer and graceful shutdown."""

    @pytest.mark.asyncio
    async def test_shutdown_flush_preserves_all_records(self, tmp_path: pathlib.Path) -> None:
        """Verify closing MetricsLogger immediately after a burst flushes all buffered records."""
        db_file = str(tmp_path / "test_shutdown.db")
        logger = MetricsLogger(db_path=db_file, batch_size=25, flush_interval_sec=1.0)
        await logger.start()

        num_records = 70
        for i in range(1, num_records + 1):
            res = _create_mock_routing_result(tx_id=f"tx_burst_{i:04d}")
            logger.log_routing_result(res)

        # Close immediately while records are still queued
        await logger.close()

        # Reopen and verify all 70 records were persisted
        async with aiosqlite.connect(db_file) as conn:
            cursor = await conn.execute("SELECT COUNT(*) FROM transactions;")
            row = await cursor.fetchone()
            assert row is not None and row[0] == num_records


class TestPhase6AnalyticalQueries:
    """Validates analytical read queries used for Phase 6 PSR-lift validation."""

    @pytest.mark.asyncio
    async def test_psr_metrics_aggregation_calculation(self, tmp_path: pathlib.Path) -> None:
        """Verify get_psr_metrics accurately computes PSR, decline breakdown, and latencies."""
        db_file = str(tmp_path / "test_psr.db")
        logger = MetricsLogger(db_path=db_file)
        await logger.start()

        base_ts = 1756970000.0
        # Log 70 authorized transactions on acquirer_alpha
        for i in range(1, 71):
            res = _create_mock_routing_result(
                tx_id=f"tx_psr_auth_{i:03d}",
                acquirer_id="acquirer_alpha",
                success=True,
                smoothed_weight=0.70,
                timestamp=base_ts + i,
            )
            logger.log_routing_result(res)

        # Log 30 declined transactions on acquirer_beta
        for i in range(1, 31):
            res = _create_mock_routing_result(
                tx_id=f"tx_psr_decl_{i:03d}",
                acquirer_id="acquirer_beta",
                success=False,
                smoothed_weight=0.30,
                timestamp=base_ts + 70 + i,
            )
            logger.log_routing_result(res)

        await logger.flush()

        # Aggregate overall metrics
        metrics = await logger.get_psr_metrics()
        assert metrics["total_transactions"] == 100
        assert metrics["authorized_count"] == 70
        assert metrics["declined_count"] == 30
        assert metrics["error_count"] == 0
        assert metrics["psr"] == 0.70  # 70 / 100 = 70% PSR

        # Aggregate by acquirer route
        alpha_metrics = await logger.get_psr_metrics(acquirer_id="acquirer_alpha")
        assert alpha_metrics["total_transactions"] == 70
        assert alpha_metrics["authorized_count"] == 70
        assert alpha_metrics["psr"] == 1.0  # 100% PSR for Alpha

        beta_metrics = await logger.get_psr_metrics(acquirer_id="acquirer_beta")
        assert beta_metrics["total_transactions"] == 30
        assert beta_metrics["authorized_count"] == 0
        assert beta_metrics["psr"] == 0.0  # 0% PSR for Beta

        # Aggregate by time window
        window_metrics = await logger.get_psr_metrics(
            start_time=base_ts + 1,
            end_time=base_ts + 50,
        )
        assert window_metrics["total_transactions"] == 50
        assert window_metrics["authorized_count"] == 50
        assert window_metrics["psr"] == 1.0

        await logger.close()
