"""Pathology verification tests for static baseline router.

Confirms:
1. Herd Migration: Instantaneous 100% step-function reallocation upon circuit breaker trip.
2. Slow Failover / Outage Delay Tax: Router absorbs exactly M consecutive failures on total outage.
3. Gray Failure Blind Spot: Intermittent successes reset the failure counter,
   causing severe failure absorption without tripping the static circuit breaker.
"""

from __future__ import annotations

import httpx
import pytest

from acquirer_sim.models import AuthorizeRequest
from baseline_router.models import (
    BaselineRouterConfig,
    FailoverPolicyConfig,
    RouteHealthStatus,
)
from baseline_router.router import StaticBaselineRouter
from router_core.models import AcquirerRouteConfig


def _create_scripted_transport(
    alpha_outcomes: list[bool],
    beta_outcomes: list[bool],
) -> httpx.MockTransport:
    """Helper to mock deterministic success/failure sequences for Alpha and Beta."""
    alpha_idx = 0
    beta_idx = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal alpha_idx, beta_idx
        path = request.url.path
        if "acquirer_alpha" in path:
            authorized = alpha_outcomes[alpha_idx] if alpha_idx < len(alpha_outcomes) else True
            alpha_idx += 1
            aid = "acquirer_alpha"
        else:
            authorized = beta_outcomes[beta_idx] if beta_idx < len(beta_outcomes) else True
            beta_idx += 1
            aid = "acquirer_beta"

        body = {
            "transaction_id": "tx_mock",
            "acquirer_id": aid,
            "status": "AUTHORIZED" if authorized else "DECLINED",
            "authorized": authorized,
            "authorization_code": "AUTH_OK" if authorized else None,
            "decline_code": None if authorized else "DO_NOT_HONOR",
            "simulated_latency_ms": 10.0,
            "timestamp": 1756973000.0,
        }
        return httpx.Response(status_code=200, json=body)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_herd_migration_instant_step_jump() -> None:
    """Test that static router executes an instantaneous 100% step jump (Delta w = 1.0)."""
    routes = [
        AcquirerRouteConfig(acquirer_id="acquirer_alpha", base_url="http://mock-alpha"),
        AcquirerRouteConfig(acquirer_id="acquirer_beta", base_url="http://mock-beta"),
    ]
    cfg = BaselineRouterConfig(
        routes=routes,
        priority_order=["acquirer_alpha", "acquirer_beta"],
        failover_policy=FailoverPolicyConfig(consecutive_failure_threshold=3),
    )

    # Alpha: 3 failures, Beta: all successes
    client = httpx.AsyncClient(
        transport=_create_scripted_transport(
            alpha_outcomes=[False, False, False],
            beta_outcomes=[True] * 5,
        )
    )
    router = StaticBaselineRouter(config=cfg, http_client=client)

    allocations_alpha: list[float] = []
    for i in range(1, 5):
        res = await router.route(AuthorizeRequest(transaction_id=f"tx_{i}", amount=100.0))
        assert res.smoothed_allocation is not None
        allocations_alpha.append(res.smoothed_allocation["acquirer_alpha"])

    # Tx 1, 2, 3: 100% to Alpha
    assert allocations_alpha[0] == 1.0
    assert allocations_alpha[1] == 1.0
    assert allocations_alpha[2] == 1.0
    # Tx 4: 0% to Alpha, 100% to Beta (Discrete Cliff Jump!)
    assert allocations_alpha[3] == 0.0

    # Calculate single-step allocation deltas:
    deltas = [abs(allocations_alpha[k] - allocations_alpha[k - 1]) for k in range(1, 4)]
    max_step_jump = max(deltas)

    # Pure step jump of 100.0% (unlike Loom's smoothed PID < 15.0%)
    assert max_step_jump == 1.0


@pytest.mark.asyncio
async def test_slow_failover_cliff_outage_tax() -> None:
    """Test that on total cliff outage, router absorbs exactly M failures before tripping."""
    m_threshold = 4
    routes = [
        AcquirerRouteConfig(acquirer_id="acquirer_alpha", base_url="http://mock-alpha"),
        AcquirerRouteConfig(acquirer_id="acquirer_beta", base_url="http://mock-beta"),
    ]
    cfg = BaselineRouterConfig(
        routes=routes,
        priority_order=["acquirer_alpha", "acquirer_beta"],
        failover_policy=FailoverPolicyConfig(consecutive_failure_threshold=m_threshold),
    )

    client = httpx.AsyncClient(
        transport=_create_scripted_transport(
            alpha_outcomes=[False] * 10,
            beta_outcomes=[True] * 10,
        )
    )
    router = StaticBaselineRouter(config=cfg, http_client=client)

    absorbed_failures = 0
    for i in range(1, 10):
        res = await router.route(AuthorizeRequest(transaction_id=f"tx_{i}", amount=100.0))
        if res.selected_acquirer == "acquirer_alpha" and not res.authorized:
            absorbed_failures += 1

    # Exactly M consecutive customer failures absorbed on Alpha before traffic moved
    assert absorbed_failures == m_threshold
    assert router.get_route_state("acquirer_alpha").status == RouteHealthStatus.TRIPPED


@pytest.mark.asyncio
async def test_gray_failure_counter_reset_blind_spot() -> None:
    """Test intermittent gray failure (40% failure rate) resets consecutive counters.

    Demonstrates that the static router stays locked to the failing route, bleeding volume
    without tripping the circuit breaker.
    """
    routes = [
        AcquirerRouteConfig(acquirer_id="acquirer_alpha", base_url="http://mock-alpha"),
        AcquirerRouteConfig(acquirer_id="acquirer_beta", base_url="http://mock-beta"),
    ]
    cfg = BaselineRouterConfig(
        routes=routes,
        priority_order=["acquirer_alpha", "acquirer_beta"],
        failover_policy=FailoverPolicyConfig(consecutive_failure_threshold=3),
    )

    # Sequence of 2 failures, 1 success, 2 failures, 1 success... (never reaches 3 consecutive!)
    # Total: 15 transactions, 10 failures, 5 successes on Alpha
    pattern = [
        False,
        False,
        True,
        False,
        False,
        True,
        False,
        False,
        True,
        False,
        False,
        True,
        False,
        False,
        True,
    ]
    client = httpx.AsyncClient(
        transport=_create_scripted_transport(
            alpha_outcomes=pattern,
            beta_outcomes=[True] * 20,
        )
    )
    router = StaticBaselineRouter(config=cfg, http_client=client)

    for i in range(15):
        res = await router.route(AuthorizeRequest(transaction_id=f"tx_{i}", amount=100.0))
        # Static router NEVER trips because every 3rd tx succeeds and resets the counter!
        assert res.selected_acquirer == "acquirer_alpha"

    state_a = router.get_route_state("acquirer_alpha")
    # Router remained locked on Alpha despite absorbing 10 failures out of 15!
    assert state_a.status == RouteHealthStatus.HEALTHY
    assert state_a.failure_count == 10
    assert state_a.success_count == 5
    assert state_a.total_count == 15
    # Still HEALTHY, blind to the 66% failure rate
    assert state_a.consecutive_failures == 0
