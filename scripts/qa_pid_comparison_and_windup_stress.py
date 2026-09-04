"""QA Verification Script: Compare PID vs Phase 3 Baseline & Test Long-Duration Outage Windup.

Executes:
1. Standard Outage Comparison (50 Warmup -> 50 Outage -> 50 Recovery):
   - Baseline (Phase 3 pure Thompson Sampling, no PID)
   - Tuned PID (Kp=0.12, Ki=0.005, Kd=0.25, I_max=1.0, min_alloc=0.03, deficit)
2. Long-Duration Outage & Integral Windup Stress Test (50 Warmup -> 200 Outage -> 100 Recovery):
   - Tuned PID with Anti-Windup Bound (I_max=1.0)
   - Unbounded PID without Anti-Windup Bound (I_max=1000.0)
   - Unbounded PID with aggressive Ki (Ki=0.05, I_max=1000.0)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

from acquirer_sim.app import create_app
from acquirer_sim.models import AuthorizeRequest, LatencyConfig, OutageToggleRequest
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig


@dataclass
class ScenarioMetrics:
    label: str
    warmup_txs: int
    outage_txs: int
    recovery_txs: int
    alloc_alpha: list[float]
    routes: list[str]
    outage_flips: int
    total_flips: int
    consecutive_flip_max: int
    max_step_delta: float
    outage_failures_alpha: int
    recovery_alpha_txs: int
    recovery_start_tx: int
    alloc_at_recovery_start: float
    alloc_at_recovery_end: float
    max_recovery_alloc: float
    accumulated_error_alpha: list[float]
    accum_at_outage_end: float
    recovery_delay_txs: int  # Transactions until alloc increases after recovery


async def run_qa_scenario(
    label: str,
    warmup_count: int,
    outage_count: int,
    recovery_count: int,
    pid_config: PIDConfig | None,
    seed: int = 777,
) -> ScenarioMetrics:
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
            pid_config=pid_config,
            seed=seed,
        )
        router = BanditRouter(config=router_config, http_client=client)

        alloc_alpha: list[float] = []
        routes: list[str] = []
        accum_alpha: list[float] = []
        outage_failures_alpha = 0

        # Stage 1: Warmup
        for i in range(1, warmup_count + 1):
            res = await router.route(AuthorizeRequest(transaction_id=f"warmup_{i}", amount=50.0))
            if res.smoothed_allocation is not None:
                alloc_alpha.append(res.smoothed_allocation["acquirer_alpha"])
            else:
                alloc_alpha.append(1.0 if res.selected_acquirer == "acquirer_alpha" else 0.0)
            routes.append(res.selected_acquirer)
            if router.pid_state is not None:
                accum_alpha.append(router.pid_state.accumulated_error.get("acquirer_alpha", 0.0))
            else:
                accum_alpha.append(0.0)

        # Stage 2: Outage on Alpha
        await client.post(
            "/acquirers/acquirer_alpha/admin/outage",
            json=OutageToggleRequest(active=True).model_dump(),
        )
        outage_start_idx = len(routes)
        for i in range(1, outage_count + 1):
            res = await router.route(AuthorizeRequest(transaction_id=f"outage_{i}", amount=50.0))
            if res.smoothed_allocation is not None:
                alloc_alpha.append(res.smoothed_allocation["acquirer_alpha"])
            else:
                alloc_alpha.append(1.0 if res.selected_acquirer == "acquirer_alpha" else 0.0)
            routes.append(res.selected_acquirer)
            if router.pid_state is not None:
                accum_alpha.append(router.pid_state.accumulated_error.get("acquirer_alpha", 0.0))
            else:
                accum_alpha.append(0.0)
            if res.selected_acquirer == "acquirer_alpha" and not res.authorized:
                outage_failures_alpha += 1

        outage_end_idx = len(routes)
        accum_at_outage_end = accum_alpha[-1] if accum_alpha else 0.0

        # Stage 3: Recovery on Alpha
        await client.post(
            "/acquirers/acquirer_alpha/admin/outage",
            json=OutageToggleRequest(active=False).model_dump(),
        )
        for i in range(1, recovery_count + 1):
            res = await router.route(AuthorizeRequest(transaction_id=f"recovery_{i}", amount=50.0))
            if res.smoothed_allocation is not None:
                alloc_alpha.append(res.smoothed_allocation["acquirer_alpha"])
            else:
                alloc_alpha.append(1.0 if res.selected_acquirer == "acquirer_alpha" else 0.0)
            routes.append(res.selected_acquirer)
            if router.pid_state is not None:
                accum_alpha.append(router.pid_state.accumulated_error.get("acquirer_alpha", 0.0))
            else:
                accum_alpha.append(0.0)

        # Metrics computation
        deltas = [abs(alloc_alpha[i] - alloc_alpha[i - 1]) for i in range(1, len(alloc_alpha))]
        max_step_delta = max(deltas) if deltas else 0.0

        # Flips
        total_flips = sum(1 for i in range(1, len(routes)) if routes[i] != routes[i - 1])
        outage_routes = routes[outage_start_idx:outage_end_idx]
        outage_flips = sum(
            1 for i in range(1, len(outage_routes)) if outage_routes[i] != outage_routes[i - 1]
        )

        # Consecutive flip streak
        max_streak = 0
        curr_streak = 0
        for i in range(1, len(outage_routes)):
            if outage_routes[i] != outage_routes[i - 1]:
                curr_streak += 1
                max_streak = max(max_streak, curr_streak)
            else:
                curr_streak = 0

        recovery_routes = routes[outage_end_idx:]
        recovery_alpha_txs = recovery_routes.count("acquirer_alpha")
        alloc_at_rec_start = alloc_alpha[outage_end_idx]
        alloc_at_rec_end = alloc_alpha[-1]
        recovery_allocs = alloc_alpha[outage_end_idx:]
        max_rec_alloc = max(recovery_allocs) if recovery_allocs else 0.0

        # Recovery delay: how many transactions after outage clearance
        # before alloc > alloc_at_rec_start
        delay = 0
        for idx, val in enumerate(recovery_allocs):
            if val > alloc_at_rec_start + 0.005:
                delay = idx
                break
        else:
            delay = len(recovery_allocs)

        return ScenarioMetrics(
            label=label,
            warmup_txs=warmup_count,
            outage_txs=outage_count,
            recovery_txs=recovery_count,
            alloc_alpha=alloc_alpha,
            routes=routes,
            outage_flips=outage_flips,
            total_flips=total_flips,
            consecutive_flip_max=max_streak,
            max_step_delta=max_step_delta,
            outage_failures_alpha=outage_failures_alpha,
            recovery_alpha_txs=recovery_alpha_txs,
            recovery_start_tx=outage_end_idx + 1,
            alloc_at_recovery_start=alloc_at_rec_start,
            alloc_at_recovery_end=alloc_at_rec_end,
            max_recovery_alloc=max_rec_alloc,
            accumulated_error_alpha=accum_alpha,
            accum_at_outage_end=accum_at_outage_end,
            recovery_delay_txs=delay,
        )


async def main() -> None:
    print("=" * 80)
    print("QA TEST HARNESS: PHASE 4 PID VERIFICATION & WINDUP STRESS SUITE")
    print("=" * 80)

    # 1. Standard Outage Comparison
    print("\n--- TEST 1: STANDARD OUTAGE SCENARIO (50 Warmup -> 50 Outage -> 50 Recovery) ---")
    baseline = await run_qa_scenario(
        label="Phase 3 Baseline (No PID)",
        warmup_count=50,
        outage_count=50,
        recovery_count=50,
        pid_config=None,
    )
    tuned = await run_qa_scenario(
        label="Tuned PID (Kp=0.12, Ki=0.005, Kd=0.25, I_max=1.0)",
        warmup_count=50,
        outage_count=50,
        recovery_count=50,
        pid_config=PIDConfig(
            kp=0.12,
            ki=0.005,
            kd=0.25,
            integral_max=1.0,
            min_allocation=0.03,
            actuation_mode="deficit",
        ),
    )

    for res in [baseline, tuned]:
        print(f"\nConfiguration: {res.label}")
        print(f"  Max Single-Step Delta: {res.max_step_delta * 100:.2f}%")
        print(f"  Outage Flips (Tx 51-100): {res.outage_flips}")
        print(f"  Total Flips (Tx 1-150): {res.total_flips}")
        print(f"  Max Consecutive Flip Streak: {res.consecutive_flip_max}")
        print(f"  Failures Absorbed by Alpha: {res.outage_failures_alpha}")
        print(f"  Recovery Traffic to Alpha (Tx 101-150): {res.recovery_alpha_txs} tx")
        print(f"  Alloc at Outage End: {res.alloc_at_recovery_start:.4f}")
        print(f"  Alloc at Recovery End: {res.alloc_at_recovery_end:.4f}")
        print(f"  Accumulator at Outage End: {res.accum_at_outage_end:.4f}")
        sample_curve = [f"{res.alloc_alpha[i]:.2f}" for i in range(49, 70, 2)]
        print(f"  Alpha Alloc Curve (Tx 50-70 by 2): {sample_curve}")

    # 2. Harder Case: Long-Duration Outage & Integral Windup Stress Test
    print("\n" + "=" * 80)
    print(
        "--- TEST 2: LONG-DURATION OUTAGE & INTEGRAL WINDUP "
        "(50 Warmup -> 200 Outage -> 100 Recovery) ---"
    )
    print("=" * 80)

    # 2a. Tuned PID with anti-windup clamping (I_max = 1.0)
    windup_safe = await run_qa_scenario(
        label="Tuned PID Bound Active (I_max=1.0, Ki=0.005)",
        warmup_count=50,
        outage_count=200,
        recovery_count=100,
        pid_config=PIDConfig(
            kp=0.12,
            ki=0.005,
            kd=0.25,
            integral_max=1.0,
            min_allocation=0.03,
            actuation_mode="deficit",
        ),
    )

    # 2b. Unbounded accumulator (I_max = 1000.0, Ki=0.005)
    windup_unbounded = await run_qa_scenario(
        label="Unbounded Accumulator (I_max=1000.0, Ki=0.005)",
        warmup_count=50,
        outage_count=200,
        recovery_count=100,
        pid_config=PIDConfig(
            kp=0.12,
            ki=0.005,
            kd=0.25,
            integral_max=1000.0,
            min_allocation=0.03,
            actuation_mode="deficit",
        ),
    )

    # 2c. High Ki + Unbounded accumulator (I_max = 1000.0, Ki=0.05)
    windup_severe = await run_qa_scenario(
        label="Severe Windup Case (I_max=1000.0, Ki=0.05)",
        warmup_count=50,
        outage_count=200,
        recovery_count=100,
        pid_config=PIDConfig(
            kp=0.12,
            ki=0.05,
            kd=0.25,
            integral_max=1000.0,
            min_allocation=0.03,
            actuation_mode="deficit",
        ),
    )

    # 2d. High Ki + Bounded accumulator (I_max = 1.0, Ki=0.05)
    windup_bounded_high_ki = await run_qa_scenario(
        label="Bounded High Ki (I_max=1.0, Ki=0.05)",
        warmup_count=50,
        outage_count=200,
        recovery_count=100,
        pid_config=PIDConfig(
            kp=0.12,
            ki=0.05,
            kd=0.25,
            integral_max=1.0,
            min_allocation=0.03,
            actuation_mode="deficit",
        ),
    )

    for res in [windup_safe, windup_unbounded, windup_severe, windup_bounded_high_ki]:
        print(f"\nConfiguration: {res.label}")
        print(f"  Accumulator at Outage End (Tx 250): {res.accum_at_outage_end:.4f}")
        print(f"  Alloc at Outage End (Tx 250): {res.alloc_at_recovery_start:.4f}")
        print(f"  Recovery Delay (Txs to start rising): {res.recovery_delay_txs} tx")
        print(f"  Alloc at Recovery End (Tx 350): {res.alloc_at_recovery_end:.4f}")
        print(f"  Max Alloc in Recovery: {res.max_recovery_alloc:.4f}")
        print(f"  Recovery Probe Traffic to Alpha: {res.recovery_alpha_txs} tx")
        rec_curve = [f"{res.alloc_alpha[i]:.2f}" for i in range(250, 270, 2)]
        print(f"  Immediate Recovery Curve (Tx 251-270 by 2): {rec_curve}")


if __name__ == "__main__":
    asyncio.run(main())
