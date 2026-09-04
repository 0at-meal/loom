"""QA Automated Test Suite: Phase 4 PID Easing Comparison and Integral Windup Stress.

Covers:
1. Side-by-side baseline vs PID comparison on identical Phase 3 outage scenario.
2. Long-duration outage (200 tx) stress test evaluating accumulator bounding.
3. Proof that Ticket A's anti-windup bound (I_max=1.0) prevents recovery paralysis and overshoot.
4. Proof that anti-windup prevents permanent integrator drift from exploration floor error.
"""

from __future__ import annotations

import httpx
import pytest

from acquirer_sim.app import create_app
from acquirer_sim.models import AuthorizeRequest, LatencyConfig, OutageToggleRequest
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig, PIDState, calculate_pid_step
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig


@pytest.mark.asyncio
class TestQAPhase4BaselineComparison:
    """Rigorous QA comparison of raw Thompson Sampling vs PID-smoothed routing."""

    async def _execute_isolated_run(
        self, pid_config: PIDConfig | None
    ) -> tuple[list[float], list[str]]:
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
            cfg = RouterConfig(
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
                pid_config=pid_config,
                seed=777,
            )
            router = BanditRouter(config=cfg, http_client=client)
            allocs: list[float] = []
            routes: list[str] = []

            for i in range(1, 51):
                res = await router.route(AuthorizeRequest(transaction_id=f"w_{i}", amount=50.0))
                if res.smoothed_allocation is not None:
                    allocs.append(res.smoothed_allocation["acquirer_alpha"])
                else:
                    allocs.append(1.0 if res.selected_acquirer == "acquirer_alpha" else 0.0)
                routes.append(res.selected_acquirer)

            await client.post(
                "/acquirers/acquirer_alpha/admin/outage",
                json=OutageToggleRequest(active=True).model_dump(),
            )
            for i in range(51, 101):
                res = await router.route(AuthorizeRequest(transaction_id=f"o_{i}", amount=50.0))
                if res.smoothed_allocation is not None:
                    allocs.append(res.smoothed_allocation["acquirer_alpha"])
                else:
                    allocs.append(1.0 if res.selected_acquirer == "acquirer_alpha" else 0.0)
                routes.append(res.selected_acquirer)

            await client.post(
                "/acquirers/acquirer_alpha/admin/outage",
                json=OutageToggleRequest(active=False).model_dump(),
            )
            for i in range(101, 151):
                res = await router.route(AuthorizeRequest(transaction_id=f"r_{i}", amount=50.0))
                if res.smoothed_allocation is not None:
                    allocs.append(res.smoothed_allocation["acquirer_alpha"])
                else:
                    allocs.append(1.0 if res.selected_acquirer == "acquirer_alpha" else 0.0)
                routes.append(res.selected_acquirer)

            return allocs, routes

    async def test_direct_comparison_allocation_easing_and_starvation(self) -> None:
        """Run identical 150-tx outage scenario with and without PID and verify metrics."""
        baseline_allocs, baseline_routes = await self._execute_isolated_run(pid_config=None)

        pid_cfg = PIDConfig(
            kp=0.12,
            ki=0.005,
            kd=0.25,
            integral_max=1.0,
            min_allocation=0.03,
            actuation_mode="deficit",
        )
        pid_allocs, pid_routes = await self._execute_isolated_run(pid_config=pid_cfg)

        # Quantitative Assertions:
        # A. Abruptness: Baseline has 100% max delta, PID is strictly <= 12%
        baseline_deltas = [
            abs(baseline_allocs[i] - baseline_allocs[i - 1]) for i in range(1, len(baseline_allocs))
        ]
        pid_deltas = [abs(pid_allocs[i] - pid_allocs[i - 1]) for i in range(1, len(pid_allocs))]

        assert max(baseline_deltas) == 1.0
        assert max(pid_deltas) <= 0.12

        # B. Square-wave chatter elimination: Baseline violently snaps 0 <-> 1
        # While PID smoothly decays from >0.70 down to 0.03
        assert pid_allocs[49] > 0.70
        assert pid_allocs[69] < 0.25
        assert pid_allocs[99] <= 0.04

        # C. Starvation Elimination:
        # Baseline sends 0 tx to Alpha post-recovery (101-150)
        # PID sends >= 2 probe transactions to Alpha post-recovery
        assert baseline_routes[100:].count("acquirer_alpha") == 0
        assert pid_routes[100:].count("acquirer_alpha") >= 2


class TestQAIntegralWindupStress:
    """Stress testing the accumulator against long-duration outages and windup pathology."""

    def test_anti_windup_bound_clamps_accumulator_during_long_outage(self) -> None:
        """Verify that a 200-transaction outage strictly clamps accumulator to I_max."""
        cfg_bounded = PIDConfig(kp=0.12, ki=0.005, kd=0.25, integral_max=1.0)
        state_bounded = PIDState.initialize(["a", "b"])
        curr_bounded = dict(state_bounded.previous_allocation)

        cfg_unbounded = PIDConfig(kp=0.12, ki=0.005, kd=0.25, integral_max=1000.0)
        state_unbounded = PIDState.initialize(["a", "b"])
        curr_unbounded = dict(state_unbounded.previous_allocation)

        for _ in range(200):
            res_b = calculate_pid_step(
                {"a": 0.0, "b": 1.0}, curr_bounded, state_bounded, cfg_bounded
            )
            curr_bounded, state_bounded = res_b.smoothed_allocation, res_b.next_state

            res_u = calculate_pid_step(
                {"a": 0.0, "b": 1.0}, curr_unbounded, state_unbounded, cfg_unbounded
            )
            curr_unbounded, state_unbounded = res_u.smoothed_allocation, res_u.next_state

        # Bounded is clamped strictly to -1.0
        assert pytest.approx(state_bounded.accumulated_error["a"], abs=1e-5) == -1.0
        assert pytest.approx(state_bounded.accumulated_error["b"], abs=1e-5) == 1.0

        # Unbounded wound up beyond -8.0
        assert state_unbounded.accumulated_error["a"] < -8.0
        assert state_unbounded.accumulated_error["b"] > 8.0

    def test_unbounded_windup_causes_recovery_paralysis_while_bounded_recovers_immediately(
        self,
    ) -> None:
        """Demonstrate unbounded windup paralyzes allocation while Ticket A bound prevents it."""
        # Setup high Ki to emphasize windup lag
        ki = 0.05
        cfg_bounded = PIDConfig(kp=0.12, ki=ki, kd=0.25, integral_max=1.0)
        state_bounded = PIDState.initialize(["a", "b"])
        curr_bounded = dict(state_bounded.previous_allocation)

        cfg_unbounded = PIDConfig(kp=0.12, ki=ki, kd=0.25, integral_max=1000.0)
        state_unbounded = PIDState.initialize(["a", "b"])
        curr_unbounded = dict(state_unbounded.previous_allocation)

        # 200 outage steps
        for _ in range(200):
            res_b = calculate_pid_step(
                {"a": 0.0, "b": 1.0}, curr_bounded, state_bounded, cfg_bounded
            )
            curr_bounded, state_bounded = res_b.smoothed_allocation, res_b.next_state

            res_u = calculate_pid_step(
                {"a": 0.0, "b": 1.0}, curr_unbounded, state_unbounded, cfg_unbounded
            )
            curr_unbounded, state_unbounded = res_u.smoothed_allocation, res_u.next_state

        # Now target flips to 1.0 for arm a (outage clears)
        bounded_steps: list[float] = []
        unbounded_steps: list[float] = []

        for _ in range(10):
            res_b = calculate_pid_step(
                {"a": 1.0, "b": 0.0}, curr_bounded, state_bounded, cfg_bounded
            )
            curr_bounded, state_bounded = res_b.smoothed_allocation, res_b.next_state
            bounded_steps.append(curr_bounded["a"])

            res_u = calculate_pid_step(
                {"a": 1.0, "b": 0.0}, curr_unbounded, state_unbounded, cfg_unbounded
            )
            curr_unbounded, state_unbounded = res_u.smoothed_allocation, res_u.next_state
            unbounded_steps.append(curr_unbounded["a"])

        # Unbounded is paralyzed at floor 0.03 for the first 5 steps
        for step_idx in range(5):
            assert pytest.approx(unbounded_steps[step_idx], abs=1e-4) == 0.03

        # Bounded immediately rises on step 1 (no freeze)
        assert bounded_steps[0] > 0.14
        assert bounded_steps[1] > 0.25
        assert bounded_steps[4] > 0.55

    def test_zero_overshoot_on_full_recovery(self) -> None:
        """Verify that recovering arm smoothly asymptotes to 0.97 with ZERO overshoot."""
        cfg = PIDConfig(kp=0.12, ki=0.005, kd=0.25, integral_max=1.0, min_allocation=0.03)
        state = PIDState.initialize(["a", "b"])
        curr = dict(state.previous_allocation)

        # 200 outage steps
        for _ in range(200):
            res = calculate_pid_step({"a": 0.0, "b": 1.0}, curr, state, cfg)
            curr, state = res.smoothed_allocation, res.next_state

        # 50 recovery steps
        max_seen = 0.0
        for _ in range(50):
            res = calculate_pid_step({"a": 1.0, "b": 0.0}, curr, state, cfg)
            curr, state = res.smoothed_allocation, res.next_state
            max_seen = max(max_seen, curr["a"])

        # Maximum allowed given 3% floor on arm b is exactly 0.9700
        # Assert zero overshoot (never exceeds 0.9700 + epsilon)
        assert max_seen <= 0.970001
        assert pytest.approx(curr["a"], abs=1e-4) == 0.9700
