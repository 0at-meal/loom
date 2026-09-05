"""Comprehensive unit and integration test suite for the static baseline router.

Validates:
1. Active-passive priority selection under normal healthy conditions.
2. Hardcoded failover when the primary acquirer experiences an outage.
3. Tertiary failover across multi-acquirer topologies.
4. Exhaustion fallback when all candidate routes are tripped.
5. Canary probe and snapback cooldown recovery lifecycles.
6. Sliding window failure rate threshold mode.
7. Transport and HTTP 503 error handling.
8. Exact schema parity and transaction logging into Phase 5 SQLite ledger.
"""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any

import httpx
import pytest

from acquirer_sim.app import create_app
from acquirer_sim.models import AuthorizeRequest
from baseline_router.models import (
    BaselineRouterConfig,
    FailoverPolicyConfig,
    FailoverThresholdType,
    RouteHealthStatus,
)
from baseline_router.router import StaticBaselineRouter
from data_layer.sqlite_logger import SQLiteMetricsStore
from router_core.models import AcquirerRouteConfig


def _create_mock_transport(
    outcomes: dict[str, list[dict[str, Any]]],
) -> httpx.MockTransport:
    """Helper to create a mock HTTP transport returning predefined JSON responses."""
    indexes: dict[str, int] = {k: 0 for k in outcomes}

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        parts = path.strip("/").split("/")
        if len(parts) >= 2 and parts[0] == "acquirers":
            aid = parts[1]
        else:
            aid = "unknown"

        if aid in outcomes:
            idx = indexes[aid]
            if idx < len(outcomes[aid]):
                spec = outcomes[aid][idx]
                indexes[aid] += 1
            else:
                spec = outcomes[aid][-1]  # Repeat last outcome

            status_code = spec.get("status_code", 200)
            if status_code == 200:
                body = {
                    "transaction_id": "tx_mock",
                    "acquirer_id": aid,
                    "status": "AUTHORIZED" if spec.get("authorized", True) else "DECLINED",
                    "authorized": spec.get("authorized", True),
                    "authorization_code": "AUTH_123" if spec.get("authorized", True) else None,
                    "decline_code": None if spec.get("authorized", True) else "DO_NOT_HONOR",
                    "decline_message": None if spec.get("authorized", True) else "Declined",
                    "simulated_latency_ms": 15.0,
                    "timestamp": time.time(),
                }
                return httpx.Response(status_code=200, json=body)
            elif status_code == 503:
                return httpx.Response(status_code=503, text="Service Unavailable")
            else:
                return httpx.Response(status_code=status_code, text="Error")

        return httpx.Response(status_code=404, text="Not Found")

    return httpx.MockTransport(handler)


@pytest.fixture
def standard_config() -> BaselineRouterConfig:
    """Create standard 2-acquirer configuration with Alpha as Primary and Beta as Secondary."""
    routes = [
        AcquirerRouteConfig(
            acquirer_id="acquirer_alpha",
            base_url="http://mock-alpha",
        ),
        AcquirerRouteConfig(
            acquirer_id="acquirer_beta",
            base_url="http://mock-beta",
        ),
    ]
    return BaselineRouterConfig(
        routes=routes,
        priority_order=["acquirer_alpha", "acquirer_beta"],
        failover_policy=FailoverPolicyConfig(
            consecutive_failure_threshold=3,
            cooldown_transactions=5,
            failback_mode="probe",
        ),
    )


@pytest.fixture
def tertiary_config() -> BaselineRouterConfig:
    """Create 3-acquirer configuration with Alpha, Beta, Gamma."""
    routes = [
        AcquirerRouteConfig(acquirer_id="acquirer_alpha", base_url="http://mock-alpha"),
        AcquirerRouteConfig(acquirer_id="acquirer_beta", base_url="http://mock-beta"),
        AcquirerRouteConfig(acquirer_id="acquirer_gamma", base_url="http://mock-gamma"),
    ]
    return BaselineRouterConfig(
        routes=routes,
        priority_order=["acquirer_alpha", "acquirer_beta", "acquirer_gamma"],
        failover_policy=FailoverPolicyConfig(
            consecutive_failure_threshold=3,
            cooldown_transactions=50,  # Prevent probe during initial cascade test
            failback_mode="probe",
        ),
    )


@pytest.mark.asyncio
async def test_normal_priority_routing(standard_config: BaselineRouterConfig) -> None:
    """Test that all traffic routes to Primary Acquirer Alpha under healthy conditions."""
    outcomes = {
        "acquirer_alpha": [{"authorized": True}] * 5,
        "acquirer_beta": [{"authorized": True}] * 5,
    }
    client = httpx.AsyncClient(transport=_create_mock_transport(outcomes))
    router = StaticBaselineRouter(config=standard_config, http_client=client)

    for i in range(5):
        req = AuthorizeRequest(transaction_id=f"tx_{i}", amount=100.0)
        res = await router.route(req)
        assert res.selected_acquirer == "acquirer_alpha"
        assert res.authorized is True
        assert res.status == "AUTHORIZED"
        assert res.smoothed_allocation is not None
        assert res.smoothed_allocation[res.selected_acquirer] == 1.0
        assert res.smoothed_allocation == {"acquirer_alpha": 1.0, "acquirer_beta": 0.0}

    state_a = router.get_route_state("acquirer_alpha")
    state_b = router.get_route_state("acquirer_beta")
    assert state_a.success_count == 5
    assert state_a.total_count == 5
    assert state_a.status == RouteHealthStatus.HEALTHY
    assert state_b.total_count == 0


@pytest.mark.asyncio
async def test_hardcoded_failover_on_outage(standard_config: BaselineRouterConfig) -> None:
    """Test Primary trips after M=3 consecutive failures and fails over 100% to Secondary."""
    outcomes = {
        "acquirer_alpha": [{"authorized": False}] * 3,
        "acquirer_beta": [{"authorized": True}] * 5,
    }
    client = httpx.AsyncClient(transport=_create_mock_transport(outcomes))
    router = StaticBaselineRouter(config=standard_config, http_client=client)

    # Transactions 1, 2, 3: Should route to Alpha and fail
    for i in range(1, 4):
        req = AuthorizeRequest(transaction_id=f"tx_{i}", amount=50.0)
        res = await router.route(req)
        assert res.selected_acquirer == "acquirer_alpha"
        assert res.authorized is False
        assert res.status == "DECLINED"

    # Verify Alpha is now TRIPPED
    state_a = router.get_route_state("acquirer_alpha")
    assert state_a.status == RouteHealthStatus.TRIPPED
    assert state_a.consecutive_failures == 3
    assert state_a.tripped_at_tx == 3

    # Transaction 4: Should immediately fail over 100% to Acquirer Beta
    req_4 = AuthorizeRequest(transaction_id="tx_4", amount=50.0)
    res_4 = await router.route(req_4)
    assert res_4.selected_acquirer == "acquirer_beta"
    assert res_4.authorized is True
    assert res_4.status == "AUTHORIZED"
    assert res_4.smoothed_allocation == {"acquirer_alpha": 0.0, "acquirer_beta": 1.0}

    # Transaction 5: Continues to route to Acquirer Beta
    req_5 = AuthorizeRequest(transaction_id="tx_5", amount=50.0)
    res_5 = await router.route(req_5)
    assert res_5.selected_acquirer == "acquirer_beta"
    assert res_5.authorized is True


@pytest.mark.asyncio
async def test_tertiary_failover_and_exhaustion(tertiary_config: BaselineRouterConfig) -> None:
    """Test cascading failover from Alpha -> Beta -> Gamma, and exhaustion fallback."""
    outcomes = {
        "acquirer_alpha": [{"authorized": False}] * 10,
        "acquirer_beta": [{"authorized": False}] * 10,
        "acquirer_gamma": [{"authorized": False}] * 10,
    }
    client = httpx.AsyncClient(transport=_create_mock_transport(outcomes))
    router = StaticBaselineRouter(config=tertiary_config, http_client=client)

    # 1. Trip Alpha (Tx 1..3)
    for i in range(1, 4):
        res = await router.route(AuthorizeRequest(transaction_id=f"tx_{i}", amount=10.0))
        assert res.selected_acquirer == "acquirer_alpha"

    assert router.get_route_state("acquirer_alpha").status == RouteHealthStatus.TRIPPED

    # 2. Trip Beta (Tx 4..6)
    for i in range(4, 7):
        res = await router.route(AuthorizeRequest(transaction_id=f"tx_{i}", amount=10.0))
        assert res.selected_acquirer == "acquirer_beta"

    assert router.get_route_state("acquirer_beta").status == RouteHealthStatus.TRIPPED

    # 3. Trip Gamma (Tx 7..9)
    for i in range(7, 10):
        res = await router.route(AuthorizeRequest(transaction_id=f"tx_{i}", amount=10.0))
        assert res.selected_acquirer == "acquirer_gamma"

    assert router.get_route_state("acquirer_gamma").status == RouteHealthStatus.TRIPPED

    # 4. All routes are now TRIPPED; verify exhaustion fallback routes to Primary Alpha
    res_10 = await router.route(AuthorizeRequest(transaction_id="tx_10", amount=10.0))
    assert res_10.selected_acquirer == "acquirer_alpha"


@pytest.mark.asyncio
async def test_cooldown_and_canary_probe_success(standard_config: BaselineRouterConfig) -> None:
    """Test cooldown expiry triggering canary probe in PROBATION and successful recovery."""
    outcomes = {
        "acquirer_alpha": [
            {"authorized": False},
            {"authorized": False},
            {"authorized": False},
            {"authorized": True},  # Probe on Tx 8 succeeds
            {"authorized": True},  # Continues on Alpha
        ],
        "acquirer_beta": [{"authorized": True}] * 10,
    }
    client = httpx.AsyncClient(transport=_create_mock_transport(outcomes))
    router = StaticBaselineRouter(config=standard_config, http_client=client)

    # Tx 1, 2, 3 -> Alpha fails (trips at Tx 3, cooldown = 5)
    for i in range(1, 4):
        await router.route(AuthorizeRequest(transaction_id=f"tx_{i}", amount=10.0))

    assert router.get_route_state("acquirer_alpha").status == RouteHealthStatus.TRIPPED

    # Tx 4, 5, 6, 7 -> Beta
    for i in range(4, 8):
        res = await router.route(AuthorizeRequest(transaction_id=f"tx_{i}", amount=10.0))
        assert res.selected_acquirer == "acquirer_beta"

    # Tx 8: Elapsed is 8 - 3 = 5 >= 5 -> Probe dispatched to Alpha
    res_8 = await router.route(AuthorizeRequest(transaction_id="tx_8", amount=10.0))
    assert res_8.selected_acquirer == "acquirer_alpha"
    assert res_8.authorized is True

    # After probe success, Alpha should be restored to HEALTHY
    state_a = router.get_route_state("acquirer_alpha")
    assert state_a.status == RouteHealthStatus.HEALTHY
    assert state_a.consecutive_failures == 0
    assert state_a.tripped_at_tx is None

    # Tx 9: Next transaction routes to Alpha normally
    res_9 = await router.route(AuthorizeRequest(transaction_id="tx_9", amount=10.0))
    assert res_9.selected_acquirer == "acquirer_alpha"
    assert res_9.authorized is True


@pytest.mark.asyncio
async def test_cooldown_and_canary_probe_failure(standard_config: BaselineRouterConfig) -> None:
    """Test that a failed canary probe immediately reverts to TRIPPED and resets cooldown."""
    outcomes = {
        "acquirer_alpha": [
            {"authorized": False},  # Tx 1
            {"authorized": False},  # Tx 2
            {"authorized": False},  # Tx 3 (trips at Tx 3)
            {"authorized": False},  # Probe at Tx 8 fails!
        ],
        "acquirer_beta": [{"authorized": True}] * 10,
    }
    client = httpx.AsyncClient(transport=_create_mock_transport(outcomes))
    router = StaticBaselineRouter(config=standard_config, http_client=client)

    # Tx 1..3: Alpha fails and trips
    for i in range(1, 4):
        await router.route(AuthorizeRequest(transaction_id=f"tx_{i}", amount=10.0))

    # Tx 4..7: Beta serves traffic
    for i in range(4, 8):
        res = await router.route(AuthorizeRequest(transaction_id=f"tx_{i}", amount=10.0))
        assert res.selected_acquirer == "acquirer_beta"

    # Tx 8: Probe to Alpha fails
    res_8 = await router.route(AuthorizeRequest(transaction_id="tx_8", amount=10.0))
    assert res_8.selected_acquirer == "acquirer_alpha"
    assert res_8.authorized is False

    # Route should revert to TRIPPED and reset tripped_at_tx to 8
    state_a = router.get_route_state("acquirer_alpha")
    assert state_a.status == RouteHealthStatus.TRIPPED
    assert state_a.tripped_at_tx == 8

    # Tx 9: Should immediately return to Beta
    res_9 = await router.route(AuthorizeRequest(transaction_id="tx_9", amount=10.0))
    assert res_9.selected_acquirer == "acquirer_beta"
    assert res_9.authorized is True


@pytest.mark.asyncio
async def test_failback_snapback_mode() -> None:
    """Test snapback failback mode abruptly restoring 100% traffic without canary probation."""
    routes = [
        AcquirerRouteConfig(acquirer_id="acquirer_alpha", base_url="http://mock-alpha"),
        AcquirerRouteConfig(acquirer_id="acquirer_beta", base_url="http://mock-beta"),
    ]
    cfg = BaselineRouterConfig(
        routes=routes,
        priority_order=["acquirer_alpha", "acquirer_beta"],
        failover_policy=FailoverPolicyConfig(
            consecutive_failure_threshold=2,
            cooldown_transactions=3,
            failback_mode="snapback",
        ),
    )

    outcomes = {
        "acquirer_alpha": [
            {"authorized": False},  # Tx 1
            {"authorized": False},  # Tx 2 (trips at Tx 2)
            {"authorized": True},  # Tx 5 (snapback)
        ],
        "acquirer_beta": [{"authorized": True}] * 10,
    }
    client = httpx.AsyncClient(transport=_create_mock_transport(outcomes))
    router = StaticBaselineRouter(config=cfg, http_client=client)

    # Tx 1, 2: Alpha fails
    await router.route(AuthorizeRequest(transaction_id="tx_1", amount=10.0))
    await router.route(AuthorizeRequest(transaction_id="tx_2", amount=10.0))
    assert router.get_route_state("acquirer_alpha").status == RouteHealthStatus.TRIPPED

    # Tx 3, 4: Beta serves (elapsed: 3-2=1, 4-2=2 < 3)
    res_3 = await router.route(AuthorizeRequest(transaction_id="tx_3", amount=10.0))
    assert res_3.selected_acquirer == "acquirer_beta"
    res_4 = await router.route(AuthorizeRequest(transaction_id="tx_4", amount=10.0))
    assert res_4.selected_acquirer == "acquirer_beta"

    # Tx 5: Cooldown elapsed (5 - 2 = 3 >= 3). Snaps back to Alpha!
    res_5 = await router.route(AuthorizeRequest(transaction_id="tx_5", amount=10.0))
    assert res_5.selected_acquirer == "acquirer_alpha"
    assert res_5.authorized is True
    assert router.get_route_state("acquirer_alpha").status == RouteHealthStatus.HEALTHY


@pytest.mark.asyncio
async def test_sliding_window_failure_rate_threshold() -> None:
    """Test failover tripping under WINDOW_FAILURE_RATE threshold mode."""
    routes = [
        AcquirerRouteConfig(acquirer_id="acquirer_alpha", base_url="http://mock-alpha"),
        AcquirerRouteConfig(acquirer_id="acquirer_beta", base_url="http://mock-beta"),
    ]
    cfg = BaselineRouterConfig(
        routes=routes,
        priority_order=["acquirer_alpha", "acquirer_beta"],
        failover_policy=FailoverPolicyConfig(
            threshold_type=FailoverThresholdType.WINDOW_FAILURE_RATE,
            window_size=5,
            window_failure_rate_threshold=0.40,  # 2 failures out of 5 = 40%
            cooldown_transactions=10,
        ),
    )

    # Alpha: Success, Fail, Success, Fail, Fail -> Window has 3/5 failures (60% >= 40%) -> Trips!
    outcomes = {
        "acquirer_alpha": [
            {"authorized": True},  # Tx 1
            {"authorized": False},  # Tx 2
            {"authorized": True},  # Tx 3
            {"authorized": False},  # Tx 4
            {"authorized": False},  # Tx 5 (Window has [1, 0, 1, 0, 0] -> rate = 3/5 = 60% >= 40%)
        ],
        "acquirer_beta": [{"authorized": True}] * 5,
    }
    client = httpx.AsyncClient(transport=_create_mock_transport(outcomes))
    router = StaticBaselineRouter(config=cfg, http_client=client)

    for i in range(1, 6):
        res = await router.route(AuthorizeRequest(transaction_id=f"tx_{i}", amount=10.0))
        assert res.selected_acquirer == "acquirer_alpha"

    # Verify Alpha tripped
    assert router.get_route_state("acquirer_alpha").status == RouteHealthStatus.TRIPPED

    # Tx 6: Should fail over to Beta
    res_6 = await router.route(AuthorizeRequest(transaction_id="tx_6", amount=10.0))
    assert res_6.selected_acquirer == "acquirer_beta"


@pytest.mark.asyncio
async def test_http_503_and_transport_error_handling(standard_config: BaselineRouterConfig) -> None:
    """Test HTTP 503 and transport exceptions map to ERROR status and trigger failover."""
    outcomes: dict[str, list[dict[str, Any]]] = {
        "acquirer_alpha": [
            {"status_code": 503},
            {"status_code": 503},
            {"status_code": 503},  # Trips on 3rd 503
        ],
        "acquirer_beta": [{"authorized": True}] * 5,
    }
    client = httpx.AsyncClient(transport=_create_mock_transport(outcomes))
    router = StaticBaselineRouter(config=standard_config, http_client=client)

    for i in range(1, 4):
        res = await router.route(AuthorizeRequest(transaction_id=f"tx_{i}", amount=10.0))
        assert res.selected_acquirer == "acquirer_alpha"
        assert res.status == "ERROR"
        assert res.authorized is False
        assert res.success is False
        assert "503" in (res.error_message or "")

    assert router.get_route_state("acquirer_alpha").status == RouteHealthStatus.TRIPPED

    # Failover to Beta
    res_4 = await router.route(AuthorizeRequest(transaction_id="tx_4", amount=10.0))
    assert res_4.selected_acquirer == "acquirer_beta"
    assert res_4.authorized is True


@pytest.mark.asyncio
async def test_sqlite_logging_parity_and_schema_conformance(
    standard_config: BaselineRouterConfig,
    tmp_path: Any,
) -> None:
    """Test that StaticBaselineRouter logs directly into the Phase 5 SQLite schema.

    Verifies:
    1. Zero constraint violations or schema differences.
    2. Every transaction emits 1 row in transactions and 1 row in acquirer_outcomes.
    3. Analytical query get_psr_metrics() executes identically.
    """
    db_path = str(tmp_path / "baseline_test.db")
    store = SQLiteMetricsStore(db_path=db_path)

    outcomes = {
        "acquirer_alpha": [
            {"authorized": True},  # Tx 1
            {"authorized": False},  # Tx 2
            {"authorized": False},  # Tx 3
            {"authorized": False},  # Tx 4 (trips)
        ],
        "acquirer_beta": [
            {"authorized": True},  # Tx 5
            {"authorized": True},  # Tx 6
        ],
    }
    client = httpx.AsyncClient(transport=_create_mock_transport(outcomes))
    router = StaticBaselineRouter(
        config=standard_config,
        http_client=client,
        metrics_logger=store,
    )

    for i in range(1, 7):
        req = AuthorizeRequest(transaction_id=f"tx_{i:04d}", amount=100.0)
        await router.route(req)

    # 1. Assert exact row counts in transactions and acquirer_outcomes
    assert store.get_transaction_count() == 6

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    cursor.execute("SELECT COUNT(*) FROM acquirer_outcomes;")
    outcomes_count = cursor.fetchone()[0]
    assert outcomes_count == 6

    # 2. Inspect transactions table schema and fields
    cursor.execute("SELECT * FROM transactions ORDER BY id ASC;")
    tx_rows = cursor.fetchall()
    assert len(tx_rows) == 6

    # Check Tx 1 (Alpha Authorized)
    row_1 = dict(tx_rows[0])
    assert row_1["transaction_id"] == "tx_0001"
    assert row_1["chosen_acquirer"] == "acquirer_alpha"
    assert row_1["allocation_weight"] == 1.0
    assert row_1["status"] == "AUTHORIZED"
    assert row_1["authorized"] == 1
    assert row_1["success"] == 1
    assert row_1["pid_diagnostics_json"] is None
    assert json.loads(row_1["smoothed_allocation_json"]) == {
        "acquirer_alpha": 1.0,
        "acquirer_beta": 0.0,
    }
    assert json.loads(row_1["thompson_samples_json"]) == {
        "acquirer_alpha": 1.0,
        "acquirer_beta": 0.0,
    }

    # Check Tx 4 (Alpha Tripped)
    row_4 = dict(tx_rows[3])
    assert row_4["transaction_id"] == "tx_0004"
    assert row_4["chosen_acquirer"] == "acquirer_alpha"
    assert row_4["status"] == "DECLINED"
    assert row_4["authorized"] == 0
    assert row_4["decline_code"] == "DO_NOT_HONOR"

    # Check Tx 5 (Failed over to Beta)
    row_5 = dict(tx_rows[4])
    assert row_5["transaction_id"] == "tx_0005"
    assert row_5["chosen_acquirer"] == "acquirer_beta"
    assert row_5["status"] == "AUTHORIZED"
    assert row_5["authorized"] == 1
    assert json.loads(row_5["smoothed_allocation_json"]) == {
        "acquirer_alpha": 0.0,
        "acquirer_beta": 1.0,
    }

    # 3. Inspect acquirer_outcomes table
    cursor.execute("SELECT * FROM acquirer_outcomes ORDER BY id ASC;")
    outcome_rows = cursor.fetchall()
    row_out_1 = dict(outcome_rows[0])
    assert row_out_1["acquirer_id"] == "acquirer_alpha"
    assert row_out_1["success"] == 1
    assert row_out_1["alpha"] == 2.0  # 1.0 + 1 success
    assert row_out_1["beta"] == 1.0  # 1.0 + 0 failures
    assert row_out_1["health_score"] == 1.0
    assert row_out_1["success_count"] == 1
    assert row_out_1["total_count"] == 1

    conn.close()

    # 4. Assert Phase 5 Analytical Query get_psr_metrics() executes identically
    psr_metrics = store.get_psr_metrics()
    assert psr_metrics["total_transactions"] == 6
    assert psr_metrics["authorized_count"] == 3  # Tx 1, Tx 5, Tx 6
    assert psr_metrics["declined_count"] == 3  # Tx 2, Tx 3, Tx 4
    assert psr_metrics["error_count"] == 0
    assert psr_metrics["psr"] == 0.50  # 3 / 6 = 50%
    assert psr_metrics["avg_allocation_weight"] == 1.0

    # Route-specific PSR
    alpha_metrics = store.get_psr_metrics(acquirer_id="acquirer_alpha")
    assert alpha_metrics["total_transactions"] == 4
    assert alpha_metrics["authorized_count"] == 1
    assert alpha_metrics["psr"] == 0.25

    beta_metrics = store.get_psr_metrics(acquirer_id="acquirer_beta")
    assert beta_metrics["total_transactions"] == 2
    assert beta_metrics["authorized_count"] == 2
    assert beta_metrics["psr"] == 1.00

    store.close()


@pytest.mark.asyncio
async def test_end_to_end_with_phase2_simulator_service() -> None:
    """End-to-end integration test calling real Phase 2 acquirer_sim FastAPI app over HTTP."""
    alpha_app = create_app(
        default_acquirers=["acquirer_alpha"],
        default_base_rate=1.0,
    )
    beta_app = create_app(
        default_acquirers=["acquirer_beta"],
        default_base_rate=1.0,
    )

    transport_alpha = httpx.ASGITransport(app=alpha_app)
    transport_beta = httpx.ASGITransport(app=beta_app)

    async def dispatch(request: httpx.Request) -> httpx.Response:
        host = str(request.url.host)
        if "sim-alpha" in host:
            return await transport_alpha.handle_async_request(request)
        elif "sim-beta" in host:
            return await transport_beta.handle_async_request(request)
        return httpx.Response(status_code=404)

    client = httpx.AsyncClient(
        transport=httpx.MockTransport(dispatch),
        base_url="http://localhost",
    )

    routes = [
        AcquirerRouteConfig(
            acquirer_id="acquirer_alpha",
            base_url="http://sim-alpha:8001",
        ),
        AcquirerRouteConfig(
            acquirer_id="acquirer_beta",
            base_url="http://sim-beta:8002",
        ),
    ]
    cfg = BaselineRouterConfig(
        routes=routes,
        priority_order=["acquirer_alpha", "acquirer_beta"],
        failover_policy=FailoverPolicyConfig(consecutive_failure_threshold=2),
    )

    router = StaticBaselineRouter(config=cfg, http_client=client)

    # 1. Normal traffic to Alpha
    res_1 = await router.route(AuthorizeRequest(transaction_id="tx_e2e_1", amount=100.0))
    assert res_1.selected_acquirer == "acquirer_alpha"
    assert res_1.authorized is True

    # 2. Trigger Outage on Alpha via admin API
    admin_client = httpx.AsyncClient(
        transport=transport_alpha,
        base_url="http://sim-alpha:8001",
    )
    outage_resp = await admin_client.post(
        "/acquirers/acquirer_alpha/admin/outage",
        json={"active": True, "behavior": "RETURN_DECLINE"},
    )
    assert outage_resp.status_code == 200

    # 3. Two failures on Alpha to trip circuit breaker (threshold = 2)
    res_2 = await router.route(AuthorizeRequest(transaction_id="tx_e2e_2", amount=100.0))
    assert res_2.selected_acquirer == "acquirer_alpha"
    assert res_2.authorized is False

    res_3 = await router.route(AuthorizeRequest(transaction_id="tx_e2e_3", amount=100.0))
    assert res_3.selected_acquirer == "acquirer_alpha"
    assert res_3.authorized is False

    assert router.get_route_state("acquirer_alpha").status == RouteHealthStatus.TRIPPED

    # 4. Next transaction fails over to Beta and succeeds
    res_4 = await router.route(AuthorizeRequest(transaction_id="tx_e2e_4", amount=100.0))
    assert res_4.selected_acquirer == "acquirer_beta"
    assert res_4.authorized is True

    await admin_client.aclose()
    await router.close()
