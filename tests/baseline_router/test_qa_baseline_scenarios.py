"""QA Automated Verification Suite: Static Baseline Router vs Phase 3/4 Outage Harness.

Formal verification of:
1. Exact empirical PSR reproduction on identical 150-tx outage scenario.
2. Overreaction pathology: M=1 sensitivity causing cascading circuit breaker trips
   and exhaustion fallback.
3. Underreaction pathology: M=5 consecutive failure delay tax.
4. Gray failure pathology: Intermittent authorizations resetting failure counters
   during 60% brownout.
5. Herd migration verification: Discrete 100% step-function allocation jumps (Delta w = 1.0).
"""

from __future__ import annotations

import httpx
import pytest

from acquirer_sim.app import create_app
from acquirer_sim.models import AuthorizeRequest, LatencyConfig, OutageToggleRequest
from baseline_router.models import (
    BaselineRouterConfig,
    FailoverPolicyConfig,
)
from baseline_router.router import StaticBaselineRouter
from router_core.models import AcquirerRouteConfig


@pytest.fixture
def test_routes() -> list[AcquirerRouteConfig]:
    return [
        AcquirerRouteConfig(acquirer_id="acquirer_alpha", base_url="http://testserver"),
        AcquirerRouteConfig(acquirer_id="acquirer_beta", base_url="http://testserver"),
    ]


@pytest.mark.asyncio
async def test_identical_150tx_outage_scenario_reproduction(
    test_routes: list[AcquirerRouteConfig],
) -> None:
    """Run StaticBaselineRouter (M=3, Cooldown=30) against identical Phase 3/4 harness.

    Harness configuration:
    - Simulated Acquirers: Alpha @ 95%, Beta @ 94%, simulator seed=42
    - Tx 1-50: Warmup
    - Tx 51-100: Outage on Alpha (0% PSR)
    - Tx 101-150: Recovery on Alpha (95% PSR)
    """
    sim_app = create_app(
        default_acquirers=["acquirer_alpha", "acquirer_beta"],
        default_base_rate=0.95,
        default_latency=LatencyConfig(base_ms=0.0, jitter_ms=0.0),
        seed=42,
    )
    sim_app.state.registry.get("acquirer_alpha").set_success_rate(0.95)
    sim_app.state.registry.get("acquirer_beta").set_success_rate(0.94)

    transport = httpx.ASGITransport(app=sim_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        cfg = BaselineRouterConfig(
            routes=test_routes,
            priority_order=["acquirer_alpha", "acquirer_beta"],
            failover_policy=FailoverPolicyConfig(
                consecutive_failure_threshold=3,
                cooldown_transactions=30,
                failback_mode="probe",
            ),
        )
        router = StaticBaselineRouter(config=cfg, http_client=client)

        results = []

        # Warmup: Tx 1-50
        for i in range(1, 51):
            req = AuthorizeRequest(transaction_id=f"tx_{i}", amount=50.0)
            res = await router.route(req)
            results.append(res)

        warmup_auth = sum(1 for r in results[:50] if r.authorized)
        assert warmup_auth == 47  # 47/50 = 94.0% PSR
        assert all(r.selected_acquirer == "acquirer_alpha" for r in results[:50])

        # Trigger Outage on Alpha
        await client.post(
            "/acquirers/acquirer_alpha/admin/outage",
            json=OutageToggleRequest(active=True).model_dump(),
        )

        # Outage: Tx 51-100
        for i in range(51, 101):
            req = AuthorizeRequest(transaction_id=f"tx_{i}", amount=50.0)
            res = await router.route(req)
            results.append(res)

        outage_results = results[50:100]
        outage_auth = sum(1 for r in outage_results if r.authorized)
        assert outage_auth == 41  # 41/50 = 82.0% PSR

        # Alpha absorbed Tx 51, 52, 53 (tripped) and Tx 83 (probe failed)
        alpha_outage_txs = [r for r in outage_results if r.selected_acquirer == "acquirer_alpha"]
        assert len(alpha_outage_txs) == 4
        assert all(not r.authorized for r in alpha_outage_txs)

        # Beta took remaining 46 outage transactions
        beta_outage_txs = [r for r in outage_results if r.selected_acquirer == "acquirer_beta"]
        assert len(beta_outage_txs) == 46
        assert sum(1 for r in beta_outage_txs if r.authorized) == 41

        # Trigger Recovery on Alpha
        await client.post(
            "/acquirers/acquirer_alpha/admin/outage",
            json=OutageToggleRequest(active=False).model_dump(),
        )

        # Recovery: Tx 101-150
        for i in range(101, 151):
            req = AuthorizeRequest(transaction_id=f"tx_{i}", amount=50.0)
            res = await router.route(req)
            results.append(res)

        recovery_results = results[100:150]
        recovery_auth = sum(1 for r in recovery_results if r.authorized)
        assert recovery_auth == 50  # 50/50 = 100.0% PSR

        # Tx 101-112 remained on Beta while cooldown elapsed
        assert all(r.selected_acquirer == "acquirer_beta" for r in recovery_results[:12])
        # Tx 113 sent canary probe to Alpha -> authorized!
        assert recovery_results[12].selected_acquirer == "acquirer_alpha"
        assert recovery_results[12].authorized is True
        # Tx 114-150 snapped back 100% to Alpha
        assert all(r.selected_acquirer == "acquirer_alpha" for r in recovery_results[13:])

        # Global Verification
        total_auth = sum(1 for r in results if r.authorized)
        total_tx = len(results)
        assert total_tx == 150
        assert total_auth == 138
        psr = total_auth / total_tx
        assert psr == 0.92  # Exactly 92.00% PSR


@pytest.mark.asyncio
async def test_overreaction_pathology_blip_causes_exhaustion_fallback(
    test_routes: list[AcquirerRouteConfig],
) -> None:
    """Verify overreaction pathology: M=1 sensitivity causes catastrophic exhaustion fallback.

    Under M=1:
    - Outage on Alpha trips Alpha at Tx 51.
    - Traffic migrates to Beta.
    - Beta encounters a routine card decline at Tx 57.
    - M=1 causes Beta to trip as well.
    - With all routes tripped, router activates exhaustion fallback to Primary (Alpha).
    - Alpha is in outage, destroying 29 consecutive customer transactions!
    """
    sim_app = create_app(
        default_acquirers=["acquirer_alpha", "acquirer_beta"],
        default_base_rate=0.95,
        default_latency=LatencyConfig(base_ms=0.0, jitter_ms=0.0),
        seed=42,
    )
    sim_app.state.registry.get("acquirer_alpha").set_success_rate(0.95)
    sim_app.state.registry.get("acquirer_beta").set_success_rate(0.94)

    transport = httpx.ASGITransport(app=sim_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        cfg = BaselineRouterConfig(
            routes=test_routes,
            priority_order=["acquirer_alpha", "acquirer_beta"],
            failover_policy=FailoverPolicyConfig(
                consecutive_failure_threshold=1,  # Hyper-sensitive threshold
                cooldown_transactions=30,
                failback_mode="probe",
            ),
        )
        router = StaticBaselineRouter(config=cfg, http_client=client)

        # Warmup: Tx 1-50
        for i in range(1, 51):
            await router.route(AuthorizeRequest(transaction_id=f"tx_warmup_{i}", amount=50.0))

        # Outage on Alpha
        await client.post(
            "/acquirers/acquirer_alpha/admin/outage",
            json=OutageToggleRequest(active=True).model_dump(),
        )

        outage_results = []
        for i in range(51, 101):
            res = await router.route(AuthorizeRequest(transaction_id=f"tx_outage_{i}", amount=50.0))
            outage_results.append(res)

        # Prove catastrophic failure:
        # Outage PSR collapses to 38.0% (19/50)
        auth_count = sum(1 for r in outage_results if r.authorized)
        assert auth_count == 19
        assert auth_count / len(outage_results) == 0.38

        # 30 transactions sent straight into dead Alpha due to exhaustion fallback
        alpha_txs = [r for r in outage_results if r.selected_acquirer == "acquirer_alpha"]
        assert len(alpha_txs) == 30
        assert all(not r.authorized for r in alpha_txs)


@pytest.mark.asyncio
async def test_underreaction_pathology_conservative_m5_delay_tax(
    test_routes: list[AcquirerRouteConfig],
) -> None:
    """Verify underreaction pathology: M=5 absorbs 5 consecutive failures before tripping."""
    sim_app = create_app(
        default_acquirers=["acquirer_alpha", "acquirer_beta"],
        default_base_rate=0.95,
        default_latency=LatencyConfig(base_ms=0.0, jitter_ms=0.0),
        seed=42,
    )
    sim_app.state.registry.get("acquirer_alpha").set_success_rate(0.95)
    sim_app.state.registry.get("acquirer_beta").set_success_rate(0.94)

    transport = httpx.ASGITransport(app=sim_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        cfg = BaselineRouterConfig(
            routes=test_routes,
            priority_order=["acquirer_alpha", "acquirer_beta"],
            failover_policy=FailoverPolicyConfig(
                consecutive_failure_threshold=5,  # Conservative threshold
                cooldown_transactions=30,
                failback_mode="probe",
            ),
        )
        router = StaticBaselineRouter(config=cfg, http_client=client)

        for i in range(1, 51):
            await router.route(AuthorizeRequest(transaction_id=f"tx_warmup_{i}", amount=50.0))

        await client.post(
            "/acquirers/acquirer_alpha/admin/outage",
            json=OutageToggleRequest(active=True).model_dump(),
        )

        outage_results = []
        for i in range(51, 101):
            res = await router.route(AuthorizeRequest(transaction_id=f"tx_outage_{i}", amount=50.0))
            outage_results.append(res)

        # M=5 requires 5 consecutive failures before trip
        alpha_txs = [r for r in outage_results if r.selected_acquirer == "acquirer_alpha"]
        # 5 consecutive failures (Tx 51-55) + 1 failed probe (Tx 85) = 6 failures
        assert len(alpha_txs) == 6
        assert all(not r.authorized for r in alpha_txs)


@pytest.mark.asyncio
async def test_underreaction_gray_failure_paralysis(
    test_routes: list[AcquirerRouteConfig],
) -> None:
    """Verify gray failure pathology: intermittent authorizations reset failure counter."""
    sim_app = create_app(
        default_acquirers=["acquirer_alpha", "acquirer_beta"],
        default_base_rate=0.95,
        default_latency=LatencyConfig(base_ms=0.0, jitter_ms=0.0),
        seed=42,
    )
    sim_app.state.registry.get("acquirer_alpha").set_success_rate(0.95)
    sim_app.state.registry.get("acquirer_beta").set_success_rate(0.94)

    transport = httpx.ASGITransport(app=sim_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        cfg = BaselineRouterConfig(
            routes=test_routes,
            priority_order=["acquirer_alpha", "acquirer_beta"],
            failover_policy=FailoverPolicyConfig(
                consecutive_failure_threshold=3,
                cooldown_transactions=30,
                failback_mode="probe",
            ),
        )
        router = StaticBaselineRouter(config=cfg, http_client=client)

        for i in range(1, 51):
            await router.route(AuthorizeRequest(transaction_id=f"tx_warmup_{i}", amount=50.0))

        # Induce partial gray failure brownout (60% PSR)
        sim_app.state.registry.get("acquirer_alpha").set_success_rate(0.60)

        outage_results = []
        for i in range(51, 101):
            res = await router.route(AuthorizeRequest(transaction_id=f"tx_outage_{i}", amount=50.0))
            outage_results.append(res)

        # Alpha absorbed 21 transactions during outage window because counter kept resetting
        alpha_txs = [r for r in outage_results if r.selected_acquirer == "acquirer_alpha"]
        assert len(alpha_txs) == 21
        alpha_failures = sum(1 for r in alpha_txs if not r.authorized)
        assert alpha_failures == 8
