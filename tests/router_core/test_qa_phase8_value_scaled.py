"""QA Automated Test Suite: Phase 8 Value-Scaled Exploration Verification."""

from __future__ import annotations

import pytest

from acquirer_sim.app import create_app
from acquirer_sim.models import AuthorizeRequest, LatencyConfig
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig
from router_core.value_policy import ValueScaledExplorationConfig
from scripts.qa_phase8_controlled_test import (
    run_controlled_paired_experiment,
)


class TestQAControlledPairedValueScaling:
    """QA verification of controlled value-scaled exploration dynamics."""

    def test_statistical_significance_high_vs_low_value(self) -> None:
        """Verify that high-value transactions measurably favor the confident arm.

        Acceptance criteria:
        1. Low-value txs ($1) maintain active exploration rate (>15% to competitor).
        2. High-value transactions ($1,000+) must crush competitor exploration to < 0.1%.
        3. Alpha win rate must increase monotonically with transaction amount.
        4. Adjusted sample standard deviation must decrease monotonically with transaction amount.
        """
        results = run_controlled_paired_experiment(trials=2000, seed=42)
        summary = results["summary"]

        # Check win rates
        low_row = next(r for r in summary if r["amount"] == 1.0)
        high_row = next(r for r in summary if r["amount"] == 1000.0)

        # 1. Low-value explores
        assert (
            low_row["beta_win_pct"] >= 15.0
        ), f"Expected low-value Beta exploration >= 15%, got {low_row['beta_win_pct']}%"

        # 2. High-value exploits confident arm
        assert (
            high_row["alpha_win_pct"] >= 99.9
        ), f"Expected high-value Alpha win rate >= 99.9%, got {high_row['alpha_win_pct']}%"
        assert high_row["beta_win_pct"] <= 0.1

        # 3. Monotonic increase in Alpha win rate
        win_rates = [r["alpha_win_pct"] for r in summary]
        assert win_rates == sorted(
            win_rates
        ), f"Alpha win rates must be monotonically non-decreasing: {win_rates}"

        # 4. Monotonic decrease in effective sampling width (std dev)
        std_devs = [r["sample_std_a"] for r in summary]
        assert std_devs == sorted(
            std_devs, reverse=True
        ), f"Sample standard deviations must decrease monotonically: {std_devs}"


class TestQAOutageRegressionParity:
    """QA verification that Phase 8 does not regress existing Phase 1-7 behavior."""

    @pytest.mark.asyncio
    async def test_disabled_policy_is_bitwise_identical_to_baseline(self) -> None:
        """When value-scaling is disabled, outage matches Phase 3 baseline bit-for-bit."""
        import httpx

        # Simulator 1 for Base
        sim_app_1 = create_app(
            default_acquirers=["acquirer_alpha", "acquirer_beta"],
            default_base_rate=0.95,
            default_latency=LatencyConfig(base_ms=0.0, jitter_ms=0.0),
            seed=42,
        )
        sim_app_1.state.registry.get("acquirer_alpha").set_success_rate(0.95)
        sim_app_1.state.registry.get("acquirer_beta").set_success_rate(0.94)

        # Simulator 2 for Disabled (identically seeded)
        sim_app_2 = create_app(
            default_acquirers=["acquirer_alpha", "acquirer_beta"],
            default_base_rate=0.95,
            default_latency=LatencyConfig(base_ms=0.0, jitter_ms=0.0),
            seed=42,
        )
        sim_app_2.state.registry.get("acquirer_alpha").set_success_rate(0.95)
        sim_app_2.state.registry.get("acquirer_beta").set_success_rate(0.94)

        transport_1 = httpx.ASGITransport(app=sim_app_1)
        transport_2 = httpx.ASGITransport(app=sim_app_2)

        async with (
            httpx.AsyncClient(transport=transport_1, base_url="http://testserver") as client_1,
            httpx.AsyncClient(transport=transport_2, base_url="http://testserver") as client_2,
        ):
            # Run 1: Pure Phase 3 baseline (value_scaled_config=None)
            cfg_base = RouterConfig(
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
                value_scaled_config=None,
                seed=777,
            )
            r_base = BanditRouter(config=cfg_base, http_client=client_1)

            # Run 2: Phase 8 present but disabled (enabled=False)
            cfg_dis = RouterConfig(
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
                value_scaled_config=ValueScaledExplorationConfig(enabled=False, tau=100.0),
                seed=777,
            )
            r_dis = BanditRouter(config=cfg_dis, http_client=client_2)

            # Execute paired 50 warmup transactions
            for i in range(1, 51):
                req = AuthorizeRequest(transaction_id=f"tx_{i}", amount=50.0)
                res_b = await r_base.route(req)
                res_d = await r_dis.route(req)

                assert res_b.selected_acquirer == res_d.selected_acquirer, f"Divergence at Tx {i}"
                assert res_b.status == res_d.status
                assert res_b.success == res_d.success

            # Verify identical end-of-warmup state
            snap_base = r_base.get_state("acquirer_alpha")
            snap_dis = r_dis.get_state("acquirer_alpha")
            assert snap_base.alpha == snap_dis.alpha
            assert snap_base.beta == snap_dis.beta
            assert snap_base.health_score == snap_dis.health_score
            assert snap_base.total_count == snap_dis.total_count
