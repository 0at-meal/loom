"""Phase 5 End-to-End Pipeline QA Verification Test Suite.

Executes the complete Phase 1-4 routing pipeline backed by Phase 5 Data Layer
against the 150-transaction outage scenario (Warmup -> Outage -> Recovery).

Verifies:
1. End-to-end execution without errors or regressions.
2. Redis health state persistence across every routing step.
3. Redis Pub/Sub zero-drop delivery and strict monotonic event ordering.
4. SQLite append-only metrics ledger completeness, gap-freedom, and trigger integrity.
5. Process restart durability: complete state recovery without loss or corruption.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
from collections.abc import Generator

import fakeredis
import httpx
import pytest

from acquirer_sim.app import create_app
from acquirer_sim.models import AuthorizeRequest, LatencyConfig, OutageToggleRequest
from data_layer.config import DataLayerConfig
from data_layer.models import RoutingEvent
from data_layer.redis_pubsub import EventPublisher, EventSubscriber
from data_layer.redis_state import RedisBanditStateRegistry, RedisStateStore
from data_layer.sqlite_logger import MetricsLogger, SQLiteMetricsStore, load_schema_sql
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig


@pytest.fixture
def fake_redis_server() -> fakeredis.FakeServer:
    """Fixture providing a shared in-memory FakeServer."""
    return fakeredis.FakeServer()


@pytest.fixture
def temp_metrics_db() -> Generator[str, None, None]:
    """Fixture providing an isolated temporary SQLite database file."""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    if os.path.exists(path):
        os.unlink(path)

    # Initialize schema with triggers and indexes
    conn = sqlite3.connect(path)
    conn.executescript(load_schema_sql())
    conn.commit()
    conn.close()

    yield path

    # Cleanup temporary database files
    for p in [path, f"{path}-wal", f"{path}-shm"]:
        if os.path.exists(p):
            try:
                os.unlink(p)
            except OSError:
                pass


@pytest.mark.asyncio
class TestPhase5EndToEndPipelineQA:
    """Rigorous QA verification of Phase 5 Data Layer integrated into the full routing pipeline."""

    async def test_full_pipeline_outage_scenario_e2e(
        self,
        fake_redis_server: fakeredis.FakeServer,
        temp_metrics_db: str,
    ) -> None:
        """Run 150-tx outage scenario end-to-end and audit state, Pub/Sub, SQLite, and restart."""
        # 1. Setup Acquirer Simulator (Alpha @ 95%, Beta @ 94%)
        sim_app = create_app(
            default_acquirers=["acquirer_alpha", "acquirer_beta"],
            default_base_rate=0.95,
            default_latency=LatencyConfig(base_ms=0.0, jitter_ms=0.0),
            seed=42,
        )
        sim_app.state.registry.get("acquirer_alpha").set_success_rate(0.95)
        sim_app.state.registry.get("acquirer_beta").set_success_rate(0.94)

        transport = httpx.ASGITransport(app=sim_app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as http_client:
            # 2. Setup Phase 5 Redis State & Pub/Sub
            redis_state_client = fakeredis.FakeRedis(
                server=fake_redis_server, decode_responses=True
            )
            redis_pub_client = fakeredis.FakeRedis(server=fake_redis_server, decode_responses=True)
            redis_sub_client = fakeredis.FakeRedis(server=fake_redis_server, decode_responses=True)

            config = DataLayerConfig(
                sqlite_db_path=temp_metrics_db,
                redis_channel_routing="events:routing",
                redis_channel_health="events:health",
            )

            # Initialize subscriber before routing starts
            subscriber = EventSubscriber(
                redis_client=redis_sub_client,
                channels=["events:routing"],
                config=config,
            )
            # Drain initial subscription confirmation frame
            _ = subscriber.get_raw_message(timeout=0.05)

            # Initialize publisher and state registry
            publisher = EventPublisher(
                redis_client=redis_pub_client,
                routing_channel="events:routing",
                raise_on_error=True,
                config=config,
            )
            registry = RedisBanditStateRegistry(
                redis_client=redis_state_client,
                config=config,
            )

            # 3. Setup Phase 5 Asynchronous Batch-Buffered Metrics Logger
            metrics_logger = MetricsLogger(
                db_path=temp_metrics_db,
                batch_size=10,
                flush_interval_sec=0.02,
                raise_on_error=True,
            )
            await metrics_logger.start()

            # 4. Setup BanditRouter with Phase 4 PID and Phase 5 Data Layer hooks
            pid_config = PIDConfig(
                kp=0.12,
                ki=0.005,
                kd=0.25,
                integral_max=1.0,
                min_allocation=0.03,
            )
            router_config = RouterConfig(
                routes=[
                    AcquirerRouteConfig(
                        acquirer_id="acquirer_alpha",
                        base_url="http://testserver",
                        state_config=AcquirerStateConfig(decay_factor=0.95),
                    ),
                    AcquirerRouteConfig(
                        acquirer_id="acquirer_beta",
                        base_url="http://testserver",
                        state_config=AcquirerStateConfig(decay_factor=0.95),
                    ),
                ],
                seed=777,
                pid_config=pid_config,
            )
            router = BanditRouter(
                config=router_config,
                http_client=http_client,
                registry=registry,
                event_publisher=publisher,
                metrics_logger=metrics_logger,
            )

            # ------------------------------------------------------------------
            # STAGE 1: Warmup Phase (50 Transactions: 1 to 50)
            # ------------------------------------------------------------------
            warmup_results = []
            for i in range(1, 51):
                req = AuthorizeRequest(transaction_id=f"tx_warmup_{i:03d}", amount=50.0)
                res = await router.route(req)
                warmup_results.append(res)

            snap_a = router.get_state("acquirer_alpha")
            snap_b = router.get_state("acquirer_beta")
            assert snap_a.total_count + snap_b.total_count == 50
            leader = (
                "acquirer_alpha" if snap_a.health_score >= snap_b.health_score else "acquirer_beta"
            )

            # ------------------------------------------------------------------
            # STAGE 2: Trigger Outage on Leader
            # ------------------------------------------------------------------
            outage_resp = await http_client.post(
                f"/acquirers/{leader}/admin/outage",
                json=OutageToggleRequest(active=True).model_dump(),
            )
            assert outage_resp.status_code == 200

            # ------------------------------------------------------------------
            # STAGE 3: Outage Phase (50 Transactions: 51 to 100)
            # ------------------------------------------------------------------
            outage_results = []
            for i in range(51, 101):
                req = AuthorizeRequest(transaction_id=f"tx_outage_{i:03d}", amount=50.0)
                res = await router.route(req)
                outage_results.append(res)

            # ------------------------------------------------------------------
            # STAGE 4: Clear Outage on Leader
            # ------------------------------------------------------------------
            rec_resp = await http_client.post(
                f"/acquirers/{leader}/admin/outage",
                json=OutageToggleRequest(active=False).model_dump(),
            )
            assert rec_resp.status_code == 200

            # ------------------------------------------------------------------
            # STAGE 5: Recovery Phase (50 Transactions: 101 to 150)
            # ------------------------------------------------------------------
            recovery_results = []
            for i in range(101, 151):
                req = AuthorizeRequest(transaction_id=f"tx_recovery_{i:03d}", amount=50.0)
                res = await router.route(req)
                recovery_results.append(res)

            # Ensure all pending batches in MetricsLogger are flushed to disk
            await metrics_logger.flush()
            await metrics_logger.close()

            # Drain all published telemetry events from subscriber
            received_events: list[RoutingEvent] = []
            while True:
                evt = subscriber.get_event(timeout=0.05)
                if evt is None:
                    break
                received_events.append(evt)
            subscriber.close()
            publisher.close()

            # ==================================================================
            # VERIFICATION 1: Total Pipeline Execution Integrity
            # ==================================================================
            all_results = warmup_results + outage_results + recovery_results
            assert len(all_results) == 150
            tx_ids = [r.transaction_id for r in all_results]
            assert len(set(tx_ids)) == 150, "Every transaction ID must be unique"

            # ==================================================================
            # VERIFICATION 2: Redis Health State Persistence
            # ==================================================================
            state_store = RedisStateStore(redis_client=redis_state_client, config=config)
            stored_a = state_store.read_snapshot("acquirer_alpha")
            stored_b = state_store.read_snapshot("acquirer_beta")
            final_a = router.get_state("acquirer_alpha")
            final_b = router.get_state("acquirer_beta")

            assert stored_a is not None
            assert stored_b is not None
            assert stored_a.alpha == final_a.alpha
            assert stored_a.beta == final_a.beta
            assert stored_a.health_score == final_a.health_score
            assert stored_a.total_count == final_a.total_count
            assert stored_b.alpha == final_b.alpha
            assert stored_b.beta == final_b.beta
            assert stored_b.health_score == final_b.health_score
            assert stored_b.total_count == final_b.total_count

            # Verify Redis keys exist explicitly
            assert redis_state_client.exists(state_store.health_key("acquirer_alpha"))
            assert redis_state_client.exists(state_store.beta_key("acquirer_alpha"))
            assert redis_state_client.exists(state_store.health_key("acquirer_beta"))
            assert redis_state_client.exists(state_store.beta_key("acquirer_beta"))
            assert "acquirer_alpha" in redis_state_client.smembers(state_store.acquirers_key())
            assert "acquirer_beta" in redis_state_client.smembers(state_store.acquirers_key())

            # ==================================================================
            # VERIFICATION 3: Redis Pub/Sub Delivery & Monotonicity
            # ==================================================================
            assert (
                len(received_events) == 150
            ), f"Pub/Sub subscriber must receive all 150 events (got {len(received_events)})"

            for idx, event in enumerate(received_events, start=1):
                # Check monotonic sequence numbers
                assert event.sequence_number == idx, f"Sequence gap at event {idx}"
                expected_result = all_results[idx - 1]
                assert event.transaction_id == expected_result.transaction_id
                assert event.selected_acquirer == expected_result.selected_acquirer
                assert event.status == expected_result.status
                assert event.authorized == expected_result.authorized
                assert event.success == expected_result.success
                assert event.thompson_samples == expected_result.thompson_samples
                assert event.smoothed_allocation == expected_result.smoothed_allocation
                assert event.updated_state["alpha"] == expected_result.state_snapshot.alpha
                assert event.updated_state["beta"] == expected_result.state_snapshot.beta

            # ==================================================================
            # VERIFICATION 4: SQLite Append-Only Metrics Ledger
            # ==================================================================
            metrics_store = SQLiteMetricsStore(db_path=temp_metrics_db)

            # Row count check
            tx_count = metrics_store.get_transaction_count()
            assert tx_count == 150, f"SQLite transactions table must hold 150 rows (got {tx_count})"

            # Acquirer outcomes check
            conn = sqlite3.connect(temp_metrics_db)
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM acquirer_outcomes;")
            outcome_count = int(cursor.fetchone()[0])
            assert (
                outcome_count == 150
            ), f"Acquirer outcomes must hold 150 rows (got {outcome_count})"

            # Gap check & uniqueness check
            cursor.execute("SELECT transaction_id FROM transactions ORDER BY id ASC;")
            db_tx_ids = [r[0] for r in cursor.fetchall()]
            assert (
                db_tx_ids == tx_ids
            ), "Database transaction IDs must match routing order with zero gaps"

            # Check PSR computation matches ledger
            psr_stats = metrics_store.get_psr_metrics()
            assert psr_stats["total_transactions"] == 150
            expected_auth_count = sum(1 for r in all_results if r.authorized)
            assert psr_stats["authorized_count"] == expected_auth_count

            # Verify append-only constitutional triggers are strictly enforced
            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                conn.execute("UPDATE transactions SET status = 'DECLINED' WHERE id = 1;")

            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                conn.execute("DELETE FROM transactions WHERE id = 1;")

            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                conn.execute("UPDATE acquirer_outcomes SET success = 0 WHERE id = 1;")

            with pytest.raises(sqlite3.IntegrityError, match="append-only"):
                conn.execute("DELETE FROM acquirer_outcomes WHERE id = 1;")

            conn.close()
            metrics_store.close()

            # ==================================================================
            # VERIFICATION 5: Environment & Process Restart Durability
            # ==================================================================
            # Simulate cold process restart: instantiate brand new registry & router
            # reading from the same Redis server and SQLite file.
            new_redis_client = fakeredis.FakeRedis(server=fake_redis_server, decode_responses=True)
            restarted_registry = RedisBanditStateRegistry(
                redis_client=new_redis_client,
                config=config,
            )
            restarted_router = BanditRouter(
                config=router_config,
                http_client=http_client,
                registry=restarted_registry,
            )

            # State must match exactly post-restart
            restarted_a = restarted_router.get_state("acquirer_alpha")
            restarted_b = restarted_router.get_state("acquirer_beta")

            assert restarted_a.alpha == final_a.alpha
            assert restarted_a.beta == final_a.beta
            assert restarted_a.health_score == final_a.health_score
            assert restarted_a.total_count == final_a.total_count
            assert restarted_b.alpha == final_b.alpha
            assert restarted_b.beta == final_b.beta
            assert restarted_b.health_score == final_b.health_score
            assert restarted_b.total_count == final_b.total_count

            # SQLite ledger must remain completely intact post-restart
            restarted_metrics_store = SQLiteMetricsStore(db_path=temp_metrics_db)
            assert restarted_metrics_store.get_transaction_count() == 150
            post_restart_tx = restarted_metrics_store.get_transaction("tx_outage_075")
            assert post_restart_tx is not None
            assert post_restart_tx["transaction_id"] == "tx_outage_075"

            # Integrity check on SQLite file
            conn = sqlite3.connect(temp_metrics_db)
            cursor = conn.cursor()
            cursor.execute("PRAGMA integrity_check;")
            integrity_result = cursor.fetchone()[0]
            assert integrity_result == "ok"
            conn.close()
            restarted_metrics_store.close()
