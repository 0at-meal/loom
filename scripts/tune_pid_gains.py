"""Empirical tuning script for Phase 4 PID controller.

Runs Phase 3's exact outage scenario
(Seed 777, Alpha 95% vs Beta 94%, Outage at Tx 50, Recovery at Tx 100)
across different PID gain configurations to evaluate damping, overshoot, and easing.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Literal

import httpx

from acquirer_sim.app import create_app
from acquirer_sim.models import AuthorizeRequest, LatencyConfig, OutageToggleRequest
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig, PIDState, calculate_pid_step
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig


@dataclass
class TuningRunResult:
    label: str
    kp: float
    ki: float
    kd: float
    allocations_alpha: list[float]
    routes: list[str]
    outage_flips: int
    alpha_outage_fails: int
    alpha_rec_traffic: int
    max_step_delta: float
    overshoot: float


async def run_scenario_with_gains(
    label: str,
    kp: float,
    ki: float,
    kd: float,
    integral_max: float = 1.0,
    min_allocation: float = 0.03,
    actuation_mode: Literal["stochastic", "deficit"] = "deficit",
    derivative_on_measurement: bool = True,
    use_pid: bool = True,
) -> TuningRunResult:
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
            seed=777,
        )
        router = BanditRouter(config=router_config, http_client=client)

        pid_config = PIDConfig(
            kp=kp,
            ki=ki,
            kd=kd,
            integral_max=integral_max,
            min_allocation=min_allocation,
            actuation_mode=actuation_mode,
            derivative_on_measurement=derivative_on_measurement,
        )
        pid_state = PIDState.initialize(["acquirer_alpha", "acquirer_beta"])
        current_alloc = dict(pid_state.previous_allocation)
        cum_target = {"acquirer_alpha": 0.0, "acquirer_beta": 0.0}
        dispatched_count = {"acquirer_alpha": 0, "acquirer_beta": 0}

        alloc_alpha: list[float] = []
        routes: list[str] = []
        prev_route = None
        outage_flips = 0
        alpha_fails = 0

        # Stage 1: Warmup (Tx 1 - 50)
        for i in range(1, 51):
            samples = router._registry.sample_all(rng=router._rng)
            win_id = max(samples.keys(), key=lambda aid: (samples[aid], aid))

            if use_pid:
                target_alloc = {aid: 1.0 if aid == win_id else 0.0 for aid in samples}
                pid_res = calculate_pid_step(target_alloc, current_alloc, pid_state, pid_config)
                current_alloc = pid_res.smoothed_allocation
                pid_state = pid_res.next_state
                alloc_alpha.append(current_alloc["acquirer_alpha"])

                if actuation_mode == "deficit":
                    for aid in ["acquirer_alpha", "acquirer_beta"]:
                        cum_target[aid] += current_alloc[aid]
                    picked = max(
                        ["acquirer_alpha", "acquirer_beta"],
                        key=lambda aid: (cum_target[aid] - dispatched_count[aid], aid),
                    )
                    dispatched_count[picked] += 1
                else:
                    picked = router._rng.choice(
                        ["acquirer_alpha", "acquirer_beta"],
                        p=[current_alloc["acquirer_alpha"], current_alloc["acquirer_beta"]],
                    )
            else:
                picked = win_id
                alloc_alpha.append(1.0 if picked == "acquirer_alpha" else 0.0)

            routes.append(picked)
            prev_route = picked

            route_info = router._routes[picked]
            req = AuthorizeRequest(transaction_id=f"tx_warmup_{i}", amount=50.0)
            resp = await client.post(route_info.get_authorize_url(), json=req.model_dump())
            auth_ok = resp.json().get("authorized", False)
            router._registry.record_outcome(picked, success=auth_ok)

        # Stage 2: Trigger outage on Alpha
        await client.post(
            "/acquirers/acquirer_alpha/admin/outage",
            json=OutageToggleRequest(active=True).model_dump(),
        )

        # Stage 3: Outage Phase (Tx 51 - 100)
        for i in range(51, 101):
            samples = router._registry.sample_all(rng=router._rng)
            win_id = max(samples.keys(), key=lambda aid: (samples[aid], aid))

            if use_pid:
                target_alloc = {aid: 1.0 if aid == win_id else 0.0 for aid in samples}
                pid_res = calculate_pid_step(target_alloc, current_alloc, pid_state, pid_config)
                current_alloc = pid_res.smoothed_allocation
                pid_state = pid_res.next_state
                alloc_alpha.append(current_alloc["acquirer_alpha"])

                if actuation_mode == "deficit":
                    for aid in ["acquirer_alpha", "acquirer_beta"]:
                        cum_target[aid] += current_alloc[aid]
                    picked = max(
                        ["acquirer_alpha", "acquirer_beta"],
                        key=lambda aid: (cum_target[aid] - dispatched_count[aid], aid),
                    )
                    dispatched_count[picked] += 1
                else:
                    picked = router._rng.choice(
                        ["acquirer_alpha", "acquirer_beta"],
                        p=[current_alloc["acquirer_alpha"], current_alloc["acquirer_beta"]],
                    )
            else:
                picked = win_id
                alloc_alpha.append(1.0 if picked == "acquirer_alpha" else 0.0)

            if picked != prev_route:
                outage_flips += 1
            prev_route = picked
            routes.append(picked)

            route_info = router._routes[picked]
            req = AuthorizeRequest(transaction_id=f"tx_outage_{i}", amount=50.0)
            resp = await client.post(route_info.get_authorize_url(), json=req.model_dump())
            auth = resp.json().get("authorized", False)
            if picked == "acquirer_alpha" and not auth:
                alpha_fails += 1
            router._registry.record_outcome(picked, success=auth)

        # Stage 4: Clear Outage / Recovery (Tx 101 - 150)
        await client.post(
            "/acquirers/acquirer_alpha/admin/outage",
            json=OutageToggleRequest(active=False).model_dump(),
        )
        alpha_rec_traffic = 0
        for i in range(101, 151):
            samples = router._registry.sample_all(rng=router._rng)
            win_id = max(samples.keys(), key=lambda aid: (samples[aid], aid))

            if use_pid:
                target_alloc = {aid: 1.0 if aid == win_id else 0.0 for aid in samples}
                pid_res = calculate_pid_step(target_alloc, current_alloc, pid_state, pid_config)
                current_alloc = pid_res.smoothed_allocation
                pid_state = pid_res.next_state
                alloc_alpha.append(current_alloc["acquirer_alpha"])

                if actuation_mode == "deficit":
                    for aid in ["acquirer_alpha", "acquirer_beta"]:
                        cum_target[aid] += current_alloc[aid]
                    picked = max(
                        ["acquirer_alpha", "acquirer_beta"],
                        key=lambda aid: (cum_target[aid] - dispatched_count[aid], aid),
                    )
                    dispatched_count[picked] += 1
                else:
                    picked = router._rng.choice(
                        ["acquirer_alpha", "acquirer_beta"],
                        p=[current_alloc["acquirer_alpha"], current_alloc["acquirer_beta"]],
                    )
            else:
                picked = win_id
                alloc_alpha.append(1.0 if picked == "acquirer_alpha" else 0.0)

            if picked == "acquirer_alpha":
                alpha_rec_traffic += 1
            routes.append(picked)
            prev_route = picked

            route_info = router._routes[picked]
            req = AuthorizeRequest(transaction_id=f"tx_recovery_{i}", amount=50.0)
            resp = await client.post(route_info.get_authorize_url(), json=req.model_dump())
            router._registry.record_outcome(picked, success=resp.json().get("authorized", False))

        # Metrics
        outage_curve = alloc_alpha[50:100]
        deltas = [abs(outage_curve[t] - outage_curve[t - 1]) for t in range(1, len(outage_curve))]
        max_delta = max(deltas) if deltas else 0.0

        # Overshoot: minimum allocation achieved during outage vs min_allocation
        min_w = min(outage_curve)
        overshoot = max(0.0, min_allocation - min_w)

        return TuningRunResult(
            label=label,
            kp=kp,
            ki=ki,
            kd=kd,
            allocations_alpha=alloc_alpha,
            routes=routes,
            outage_flips=outage_flips,
            alpha_outage_fails=alpha_fails,
            alpha_rec_traffic=alpha_rec_traffic,
            max_step_delta=max_delta,
            overshoot=overshoot,
        )


async def main() -> None:
    experiments = [
        ("Phase 3 Baseline (No PID)", 0.0, 0.0, 0.0, False),
        ("High Kp (Too Aggressive / Step Jump)", 0.50, 0.0, 0.05, True),
        ("Low Kp (Too Sluggish / Failure Bleed)", 0.02, 0.005, 0.10, True),
        ("Zero Kd (Undamped / Ringing Chatter)", 0.15, 0.01, 0.00, True),
        ("Excessive Kd (Overdamped / Sluggish)", 0.15, 0.01, 0.80, True),
        ("High Ki (Integral Windup Overshoot)", 0.15, 0.10, 0.20, True),
        ("Tuned PID Candidate 1 (Balanced)", 0.12, 0.005, 0.25, True),
        ("Tuned PID Candidate 2 (Optimal Damped)", 0.15, 0.008, 0.30, True),
    ]

    print("=" * 80)
    print("PHASE 4 PID GAIN TUNING EXPERIMENTS (50 Warmup -> 50 Outage -> 50 Recovery)")
    print("=" * 80)

    for label, kp, ki, kd, use_pid in experiments:
        res = await run_scenario_with_gains(
            label=label,
            kp=kp,
            ki=ki,
            kd=kd,
            use_pid=use_pid,
            min_allocation=0.03,
        )
        curve = res.allocations_alpha
        print(f"\nConfiguration: {label}")
        print(f"  Gains: Kp={kp:.2f}, Ki={ki:.3f}, Kd={kd:.2f}")
        print(f"  Max single-step delta in allocation: {res.max_step_delta * 100:.1f}%")
        print(f"  Failures absorbed by dead leader: {res.alpha_outage_fails}")
        print(f"  Traffic restored to recovered leader (Tx 101-150): {res.alpha_rec_traffic} tx")
        # Sample key points on the allocation curve:
        trace_points = [50, 52, 54, 56, 58, 60, 65, 70, 80, 100]
        trace_str = " | ".join(f"Tx{tx}: {curve[tx - 1]:.2f}" for tx in trace_points)
        print(f"  Alpha Allocation Curve:\n    {trace_str}")


if __name__ == "__main__":
    asyncio.run(main())
