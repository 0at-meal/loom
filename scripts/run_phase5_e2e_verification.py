"""QA Verification Script: Phase 5 End-to-End Pipeline & Data Layer Audit.

Executes the Phase 3/4 150-transaction outage scenario end-to-end against the
complete Phase 5 Data Layer (Redis state persistence, Redis Pub/Sub event broadcast,
and SQLite append-only metrics logging).

Audits and verifies:
1. Redis Health State Persistence: point-in-time beliefs match post-run state.
2. Redis Pub/Sub Delivery: every routing event received in monotonic sequence (0 drops).
3. SQLite Ledger Completeness: 150 rows, zero gaps, zero duplicates, active triggers.
4. Process Restart Durability: 100% state and ledger recovery across environment reboot.

Usage:
    python scripts/run_phase5_e2e_verification.py [--standalone] [--db-path DB_PATH]
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sqlite3
import sys
import tempfile
import time
from typing import Any

import fakeredis
import httpx

from acquirer_sim.app import create_app
from acquirer_sim.models import AuthorizeRequest, LatencyConfig, OutageToggleRequest
from data_layer.config import DataLayerConfig
from data_layer.models import RoutingEvent
from data_layer.redis_pubsub import EventPublisher, EventSubscriber
from data_layer.redis_state import RedisBanditStateRegistry, RedisStateStore
from data_layer.sqlite_logger import MetricsLogger, SQLiteMetricsStore, load_schema_sql
from router_core.models import AcquirerRouteConfig, RouterConfig, RoutingResult
from router_core.pid import PIDConfig
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig


async def run_phase5_verification(
    standalone: bool = True,
    db_path: str | None = None,
) -> int:
    """Execute complete Phase 5 QA verification gauntlet."""
    print("=" * 80)
    print(" LOOM PHASE 5 DATA LAYER END-TO-END QA VERIFICATION GAUNTLET")
    print(" Target: Full Pipeline (Phases 1-4) backed by Redis + Pub/Sub + SQLite")
    print(" Scenario: 150 Transactions (50 Warmup -> 50 Outage -> 50 Recovery)")
    print(f" Mode: {'In-Memory Standalone (fakeredis)' if standalone else 'Live Infrastructure'}")
    print("=" * 80)

    # 1. Setup Database Path
    is_temp_db = db_path is None
    if is_temp_db:
        fd, actual_db_path = tempfile.mkstemp(suffix=".db")
        os.close(fd)
        if os.path.exists(actual_db_path):
            os.unlink(actual_db_path)
    else:
        actual_db_path = str(db_path)
        if os.path.exists(actual_db_path):
            os.unlink(actual_db_path)

    # Bootstrap schema
    conn = sqlite3.connect(actual_db_path)
    conn.executescript(load_schema_sql())
    conn.commit()
    conn.close()

    # 2. Setup Redis Server & Clients
    fake_server = fakeredis.FakeServer() if standalone else None
    redis_state: Any
    redis_pub: Any
    redis_sub: Any
    if standalone:
        redis_state = fakeredis.FakeRedis(server=fake_server, decode_responses=True)
        redis_pub = fakeredis.FakeRedis(server=fake_server, decode_responses=True)
        redis_sub = fakeredis.FakeRedis(server=fake_server, decode_responses=True)
    else:
        import redis as real_redis

        redis_state = real_redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)
        redis_pub = real_redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)
        redis_sub = real_redis.Redis(host="localhost", port=6379, db=0, decode_responses=True)

    config = DataLayerConfig(
        sqlite_db_path=actual_db_path,
        redis_channel_routing="events:routing",
        redis_channel_health="events:health",
    )

    # 3. Setup Pub/Sub Subscriber
    subscriber = EventSubscriber(
        redis_client=redis_sub,
        channels=["events:routing"],
        config=config,
    )
    # Drain initial subscription frame
    _ = subscriber.get_raw_message(timeout=0.05)

    # 4. Setup Publisher, State Registry, and Metrics Logger
    publisher = EventPublisher(
        redis_client=redis_pub,
        routing_channel="events:routing",
        raise_on_error=True,
        config=config,
    )
    registry = RedisBanditStateRegistry(
        redis_client=redis_state,
        config=config,
    )
    metrics_logger = MetricsLogger(
        db_path=actual_db_path,
        batch_size=10,
        flush_interval_sec=0.02,
        raise_on_error=True,
    )
    await metrics_logger.start()

    # 5. Setup Simulator App & Router
    sim_app = create_app(
        default_acquirers=["acquirer_alpha", "acquirer_beta"],
        default_base_rate=0.95,
        default_latency=LatencyConfig(base_ms=0.0, jitter_ms=0.0),
        seed=42,
    )
    sim_app.state.registry.get("acquirer_alpha").set_success_rate(0.95)
    sim_app.state.registry.get("acquirer_beta").set_success_rate(0.94)

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

    transport = httpx.ASGITransport(app=sim_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as http_client:
        router = BanditRouter(
            config=router_config,
            http_client=http_client,
            registry=registry,
            event_publisher=publisher,
            metrics_logger=metrics_logger,
        )

        all_results: list[RoutingResult] = []

        # ----------------------------------------------------------------------
        # STAGE 1: Warmup Phase (50 Transactions)
        # ----------------------------------------------------------------------
        print("\n[STAGE 1] WARMUP PHASE (Tx 1-50): Alpha @ 95% vs Beta @ 94%")
        t_start_warmup = time.perf_counter()
        for i in range(1, 51):
            req = AuthorizeRequest(transaction_id=f"tx_warmup_{i:03d}", amount=50.0)
            res = await router.route(req)
            all_results.append(res)

        snap_a = router.get_state("acquirer_alpha")
        snap_b = router.get_state("acquirer_beta")
        leader = "acquirer_alpha" if snap_a.health_score >= snap_b.health_score else "acquirer_beta"
        backup = "acquirer_beta" if leader == "acquirer_alpha" else "acquirer_alpha"
        print(
            f"  Warmup Complete in {(time.perf_counter() - t_start_warmup) * 1000:.1f}ms. "
            f"Leader: {leader} (Alpha={snap_a.health_score:.3f}, Beta={snap_b.health_score:.3f})"
        )

        # ----------------------------------------------------------------------
        # STAGE 2: Outage Trigger on Leader
        # ----------------------------------------------------------------------
        print(f"\n[STAGE 2] OUTAGE TRIGGER: Simulating sudden failure on leader ({leader})")
        outage_resp = await http_client.post(
            f"/acquirers/{leader}/admin/outage",
            json=OutageToggleRequest(active=True).model_dump(),
        )
        assert outage_resp.status_code == 200
        print(f"  Outage confirmed on {leader} (effective_rate=0.0)")

        # ----------------------------------------------------------------------
        # STAGE 3: Outage Phase (50 Transactions)
        # ----------------------------------------------------------------------
        print("\n[STAGE 3] OUTAGE EXECUTION (Tx 51-100): Traffic shedding via PID control")
        t_start_outage = time.perf_counter()
        for i in range(51, 101):
            req = AuthorizeRequest(transaction_id=f"tx_outage_{i:03d}", amount=50.0)
            res = await router.route(req)
            all_results.append(res)

        outage_choices = [r.selected_acquirer for r in all_results[50:100]]
        leader_outage_count = outage_choices.count(leader)
        backup_outage_count = outage_choices.count(backup)
        print(
            f"  Outage Complete in {(time.perf_counter() - t_start_outage) * 1000:.1f}ms. "
            f"Dispatched: {leader}={leader_outage_count}, {backup}={backup_outage_count}"
        )

        # ----------------------------------------------------------------------
        # STAGE 4: Clear Outage on Leader
        # ----------------------------------------------------------------------
        print(f"\n[STAGE 4] OUTAGE CLEARED: Restoring {leader} to 95% capacity")
        rec_resp = await http_client.post(
            f"/acquirers/{leader}/admin/outage",
            json=OutageToggleRequest(active=False).model_dump(),
        )
        assert rec_resp.status_code == 200
        print(f"  Recovery confirmed on {leader} (effective_rate=0.95)")

        # ----------------------------------------------------------------------
        # STAGE 5: Recovery Phase (50 Transactions)
        # ----------------------------------------------------------------------
        print("\n[STAGE 5] RECOVERY EXECUTION (Tx 101-150): Probing via 3% floor & belief update")
        t_start_rec = time.perf_counter()
        for i in range(101, 151):
            req = AuthorizeRequest(transaction_id=f"tx_recovery_{i:03d}", amount=50.0)
            res = await router.route(req)
            all_results.append(res)

        rec_choices = [r.selected_acquirer for r in all_results[100:150]]
        leader_rec_count = rec_choices.count(leader)
        print(
            f"  Recovery Complete in {(time.perf_counter() - t_start_rec) * 1000:.1f}ms. "
            f"Leader received {leader_rec_count} transactions."
        )

        # Flush logger buffers to SQLite disk
        await metrics_logger.flush()
        await metrics_logger.close()

        # Drain Pub/Sub messages
        received_events: list[RoutingEvent] = []
        while True:
            evt = subscriber.get_event(timeout=0.05)
            if evt is None:
                break
            received_events.append(evt)
        subscriber.close()
        publisher.close()

    # ==========================================================================
    # RIGOROUS AUDIT & VERIFICATION
    # ==========================================================================
    print("\n" + "=" * 80)
    print(" QA VERIFICATION AUDIT RESULTS")
    print("=" * 80)

    audit_passed = True

    # 1. Pipeline Execution Count
    tx_total = len(all_results)
    if tx_total == 150:
        print(f"  [PASS] Total Pipeline Executions: {tx_total}/150")
    else:
        print(f"  [FAIL] Expected 150 executions, got {tx_total}")
        audit_passed = False

    # 2. Redis State Persistence
    state_store = RedisStateStore(redis_client=redis_state, config=config)
    stored_a = state_store.read_snapshot("acquirer_alpha")
    stored_b = state_store.read_snapshot("acquirer_beta")
    final_a = router.get_state("acquirer_alpha")
    final_b = router.get_state("acquirer_beta")

    if (
        stored_a is not None
        and stored_b is not None
        and stored_a.alpha == final_a.alpha
        and stored_a.beta == final_a.beta
        and stored_a.health_score == final_a.health_score
        and stored_b.alpha == final_b.alpha
        and stored_b.beta == final_b.beta
        and stored_b.health_score == final_b.health_score
    ):
        print(
            "  [PASS] Redis State Persistence Verified:\n"
            f"         Alpha: alpha={stored_a.alpha:.3f}, beta={stored_a.beta:.3f}, "
            f"health={stored_a.health_score:.4f}\n"
            f"         Beta:  alpha={stored_b.alpha:.3f}, beta={stored_b.beta:.3f}, "
            f"health={stored_b.health_score:.4f}"
        )
    else:
        print("  [FAIL] Redis state does not match router final state!")
        audit_passed = False

    # 3. Redis Pub/Sub Zero-Drop & Monotonicity
    evt_count = len(received_events)
    monotonic_ok = all(e.sequence_number == i for i, e in enumerate(received_events, start=1))
    id_match_ok = all(
        e.transaction_id == r.transaction_id
        for e, r in zip(received_events, all_results, strict=True)
    )

    if evt_count == 150 and monotonic_ok and id_match_ok:
        print(
            "  [PASS] Redis Pub/Sub Telemetry Stream:\n"
            f"         Received {evt_count}/150 events "
            "(Zero Drops, Monotonic Seq 1..150, 100% ID match)"
        )
    else:
        print(
            f"  [FAIL] Pub/Sub mismatch: count={evt_count}, "
            f"monotonic={monotonic_ok}, id_match={id_match_ok}"
        )
        audit_passed = False

    # 4. SQLite Append-Only Metrics Ledger
    metrics_store = SQLiteMetricsStore(db_path=actual_db_path)
    sqlite_tx_count = metrics_store.get_transaction_count()

    conn = sqlite3.connect(actual_db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM acquirer_outcomes;")
    sqlite_outcome_count = int(cursor.fetchone()[0])

    cursor.execute("SELECT transaction_id FROM transactions ORDER BY id ASC;")
    db_tx_ids = [r[0] for r in cursor.fetchall()]
    expected_tx_ids = [r.transaction_id for r in all_results]
    gap_free = db_tx_ids == expected_tx_ids

    # Verify triggers
    trigger_abort_count = 0
    try:
        conn.execute("UPDATE transactions SET status = 'DECLINED' WHERE id = 1;")
    except sqlite3.IntegrityError:
        trigger_abort_count += 1

    try:
        conn.execute("DELETE FROM transactions WHERE id = 1;")
    except sqlite3.IntegrityError:
        trigger_abort_count += 1

    try:
        conn.execute("UPDATE acquirer_outcomes SET success = 0 WHERE id = 1;")
    except sqlite3.IntegrityError:
        trigger_abort_count += 1

    try:
        conn.execute("DELETE FROM acquirer_outcomes WHERE id = 1;")
    except sqlite3.IntegrityError:
        trigger_abort_count += 1

    conn.close()

    psr_stats = metrics_store.get_psr_metrics()
    metrics_store.close()

    if (
        sqlite_tx_count == 150
        and sqlite_outcome_count == 150
        and gap_free
        and trigger_abort_count == 4
    ):
        print(
            "  [PASS] SQLite Append-Only Metrics Ledger:\n"
            f"         transactions={sqlite_tx_count}, acquirer_outcomes={sqlite_outcome_count}\n"
            f"         Zero gaps, zero duplicates. PSR={psr_stats['psr'] * 100:.1f}%\n"
            "         Constitutional Abort Triggers: 4/4 Verified Active"
        )
    else:
        print(
            f"  [FAIL] SQLite ledger mismatch: txs={sqlite_tx_count}, "
            f"outcomes={sqlite_outcome_count}, gap_free={gap_free}, "
            f"triggers_blocked={trigger_abort_count}/4"
        )
        audit_passed = False

    # 5. Process / Environment Restart Durability
    print("\n[*] Simulating Process & Environment Reboot...")
    reboot_redis_client: Any
    if standalone:
        reboot_redis_client = fakeredis.FakeRedis(server=fake_server, decode_responses=True)
    else:
        reboot_redis_client = real_redis.Redis(
            host="localhost",
            port=6379,
            db=0,
            decode_responses=True,
        )

    reboot_registry = RedisBanditStateRegistry(
        redis_client=reboot_redis_client,
        config=config,
    )
    reboot_router = BanditRouter(
        config=router_config,
        registry=reboot_registry,
    )

    reboot_snap_a = reboot_router.get_state("acquirer_alpha")
    reboot_snap_b = reboot_router.get_state("acquirer_beta")

    reboot_metrics_store = SQLiteMetricsStore(db_path=actual_db_path)
    reboot_tx_count = reboot_metrics_store.get_transaction_count()

    conn = sqlite3.connect(actual_db_path)
    cursor = conn.cursor()
    cursor.execute("PRAGMA integrity_check;")
    db_integrity = cursor.fetchone()[0]
    conn.close()
    reboot_metrics_store.close()

    state_restored = (
        reboot_snap_a.alpha == final_a.alpha
        and reboot_snap_a.beta == final_a.beta
        and reboot_snap_a.health_score == final_a.health_score
        and reboot_snap_b.alpha == final_b.alpha
        and reboot_snap_b.beta == final_b.beta
        and reboot_snap_b.health_score == final_b.health_score
    )

    if state_restored and reboot_tx_count == 150 and db_integrity == "ok":
        print(
            "  [PASS] Process Restart Durability:\n"
            "         Redis State Hydrated: 100% parameter match (Zero State Loss)\n"
            "         SQLite Log Durability: 150/150 records intact (PRAGMA integrity_check: ok)"
        )
    else:
        print(
            f"  [FAIL] Restart verification failed: state_restored={state_restored}, "
            f"tx_count={reboot_tx_count}, integrity={db_integrity}"
        )
        audit_passed = False

    # Clean up temporary database files
    if is_temp_db:
        for p in [actual_db_path, f"{actual_db_path}-wal", f"{actual_db_path}-shm"]:
            if os.path.exists(p):
                try:
                    os.unlink(p)
                except OSError:
                    pass

    print("=" * 80)
    if audit_passed:
        print(" VERDICT: PASSED - PHASE 5 DATA LAYER MEETS ALL ACCEPTANCE CRITERIA")
        print("=" * 80)
        return 0
    else:
        print(" VERDICT: FAILED - ONE OR MORE PHASE 5 ACCEPTANCE CRITERIA VIOLATED")
        print("=" * 80)
        return 1


def main() -> None:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Phase 5 QA Verification Gauntlet")
    parser.add_argument(
        "--standalone",
        action="store_true",
        default=True,
        help="Use in-memory fakeredis server (zero external containers).",
    )
    parser.add_argument(
        "--live",
        action="store_false",
        dest="standalone",
        help="Use live external Redis on localhost:6379.",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Custom SQLite database path (default: temporary file).",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(run_phase5_verification(standalone=args.standalone, db_path=args.db_path)))


if __name__ == "__main__":
    main()
