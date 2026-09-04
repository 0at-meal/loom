"""End-to-end pipeline integration tests wiring BanditRouter to AcquirerSimulator."""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI

from acquirer_sim.app import create_app
from acquirer_sim.models import AuthorizeRequest, LatencyConfig, OutageToggleRequest
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig


@pytest.fixture
def sim_service_app() -> FastAPI:
    """Spin up in-process simulated acquirer service with zero artificial latency."""
    return create_app(
        default_acquirers=["acquirer_alpha", "acquirer_beta"],
        default_base_rate=0.95,
        default_latency=LatencyConfig(base_ms=0.0, jitter_ms=0.0),
        seed=123,
    )


@pytest.mark.asyncio
class TestPipelineEndToEnd:
    """End-to-end integration test suite verifying closed-loop learning and hard-switching."""

    async def test_steady_state_allocation_favors_healthier_arm(
        self, sim_service_app: FastAPI
    ) -> None:
        """Verify healthier acquirer captures majority of volume in steady state."""
        # Set acquirer_beta to 70% PSR via simulator registry
        sim_service_app.state.registry.get("acquirer_beta").set_success_rate(0.70)

        transport = httpx.ASGITransport(app=sim_service_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
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
                seed=42,
            )
            router = BanditRouter(config=router_config, http_client=client)

            counts: dict[str, int] = {"acquirer_alpha": 0, "acquirer_beta": 0}
            for i in range(100):
                req = AuthorizeRequest(transaction_id=f"tx_steady_{i}", amount=50.0)
                res = await router.route(req)
                counts[res.selected_acquirer] += 1

            # Alpha (95%) should capture the vast majority of traffic over Beta (70%)
            assert counts["acquirer_alpha"] >= 80, f"Expected Alpha >= 80, got {counts}"
            assert counts["acquirer_beta"] > 0, "Expected at least 1 exploration probe to Beta"

            alpha_state = router.get_state("acquirer_alpha")
            beta_state = router.get_state("acquirer_beta")
            assert alpha_state.expected_success_rate > beta_state.expected_success_rate

    async def test_sudden_outage_causes_100_percent_hard_switch_to_backup(
        self, sim_service_app: FastAPI
    ) -> None:
        """Verify sudden outage on primary route causes a hard switch within 10 failures."""

        # Setup: Alpha starts healthy at 98%, Beta at 85%
        sim_service_app.state.registry.get("acquirer_alpha").set_success_rate(0.98)
        sim_service_app.state.registry.get("acquirer_beta").set_success_rate(0.85)

        transport = httpx.ASGITransport(app=sim_service_app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            router_config = RouterConfig(
                routes=[
                    AcquirerRouteConfig(
                        acquirer_id="acquirer_alpha",
                        base_url="http://testserver",
                        state_config=AcquirerStateConfig(decay_factor=0.90),
                    ),
                    AcquirerRouteConfig(
                        acquirer_id="acquirer_beta",
                        base_url="http://testserver",
                        state_config=AcquirerStateConfig(decay_factor=0.90),
                    ),
                ],
                seed=999,
            )
            router = BanditRouter(config=router_config, http_client=client)

            # Warm up: 30 transactions to establish Alpha dominance
            for i in range(30):
                req = AuthorizeRequest(transaction_id=f"tx_warmup_{i}", amount=30.0)
                await router.route(req)

            # Assert Alpha is established as primary route
            assert router.get_state("acquirer_alpha").expected_success_rate > 0.85

            # Trigger instantaneous outage on Alpha via Phase 2 Admin API
            outage_resp = await client.post(
                "/acquirers/acquirer_alpha/admin/outage",
                json=OutageToggleRequest(active=True).model_dump(),
            )
            assert outage_resp.status_code == 200

            # Execute outage stream: trace transaction-by-transaction routing choices
            routes_chosen: list[str] = []
            for i in range(30):
                req = AuthorizeRequest(transaction_id=f"tx_outage_{i}", amount=30.0)
                res = await router.route(req)
                routes_chosen.append(res.selected_acquirer)

            # Within the first 10 transactions after outage, router should switch to Beta
            first_switch_index = next(
                (idx for idx, acq in enumerate(routes_chosen) if acq == "acquirer_beta"),
                None,
            )
            assert first_switch_index is not None, "Router never switched away from dead Alpha"
            assert (
                first_switch_index < 10
            ), f"Router took too long to switch: {first_switch_index} calls"

            # In the final 15 transactions (outage sustained), Beta should capture >= 90% of traffic
            late_choices = routes_chosen[15:]
            beta_late_count = sum(1 for c in late_choices if c == "acquirer_beta")
            assert (
                beta_late_count >= 13
            ), f"Expected near 100% hard-switch to Beta, got: {late_choices}"

            # Verify Alpha's health and Beta parameters reflect the outage
            alpha_snap = router.get_state("acquirer_alpha")
            assert alpha_snap.health_score < 0.50
            assert alpha_snap.beta > alpha_snap.alpha
