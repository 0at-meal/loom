"""QA Scripted Scenario: Execute Static Baseline Router against Phase 3/4 Outage Harness.

Runs:
1. Phase 6 Static Baseline (M=3, Cooldown=30, Probe)
2. Phase 6 Static Baseline Overreaction Mode (M=1)
3. Phase 6 Static Baseline Conservative Mode (M=5)
4. Phase 6 Static Baseline Gray Failure Brownout (60% PSR)
5. Phase 3 Raw Bandit (No PID)
6. Phase 4 Tuned PID (Smooth Easing)

Outputs exact empirical counts and PSR for direct apples-to-apples comparison.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx

from acquirer_sim.app import create_app
from acquirer_sim.models import AuthorizeRequest, LatencyConfig, OutageToggleRequest
from baseline_router.models import (
    BaselineRouterConfig,
    FailoverPolicyConfig,
)
from baseline_router.router import StaticBaselineRouter
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig


@dataclass
class RunSummary:
    name: str
    total_tx: int
    authorized_tx: int
    declined_tx: int
    error_tx: int
    psr: float
    warmup_psr: float
    outage_psr: float
    recovery_psr: float
    outage_failures_alpha: int
    outage_txs_alpha: int
    outage_txs_beta: int
    outage_flips: int
    total_flips: int
    max_step_delta: float
    recovery_txs_alpha: int
    recovery_txs_beta: int
    history: list[dict[str, Any]]


async def run_baseline_scenario(
    name: str,
    failover_policy: FailoverPolicyConfig,
    gray_failure_rate: float | None = None,
) -> RunSummary:
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
        routes = [
            AcquirerRouteConfig(acquirer_id="acquirer_alpha", base_url="http://testserver"),
            AcquirerRouteConfig(acquirer_id="acquirer_beta", base_url="http://testserver"),
        ]
        config = BaselineRouterConfig(
            routes=routes,
            priority_order=["acquirer_alpha", "acquirer_beta"],
            failover_policy=failover_policy,
        )
        router = StaticBaselineRouter(config=config, http_client=client)

        history: list[dict[str, Any]] = []

        # Stage 1: Warmup (Tx 1-50)
        for i in range(1, 51):
            req = AuthorizeRequest(transaction_id=f"tx_warmup_{i}", amount=50.0)
            res = await router.route(req)
            alloc_a = (
                res.smoothed_allocation["acquirer_alpha"]
                if res.smoothed_allocation
                else (1.0 if res.selected_acquirer == "acquirer_alpha" else 0.0)
            )
            history.append(
                {
                    "tx": i,
                    "stage": "WARMUP",
                    "route": res.selected_acquirer,
                    "authorized": res.authorized,
                    "status": res.status,
                    "alloc_alpha": alloc_a,
                }
            )

        # Stage 2: Outage Trigger
        if gray_failure_rate is not None:
            # Partial degradation / brownout
            sim_app.state.registry.get("acquirer_alpha").set_success_rate(gray_failure_rate)
        else:
            # Total cliff outage
            await client.post(
                "/acquirers/acquirer_alpha/admin/outage",
                json=OutageToggleRequest(active=True).model_dump(),
            )

        # Stage 3: Outage Phase (Tx 51-100)
        for i in range(51, 101):
            req = AuthorizeRequest(transaction_id=f"tx_outage_{i}", amount=50.0)
            res = await router.route(req)
            alloc_a = (
                res.smoothed_allocation["acquirer_alpha"]
                if res.smoothed_allocation
                else (1.0 if res.selected_acquirer == "acquirer_alpha" else 0.0)
            )
            history.append(
                {
                    "tx": i,
                    "stage": "OUTAGE",
                    "route": res.selected_acquirer,
                    "authorized": res.authorized,
                    "status": res.status,
                    "alloc_alpha": alloc_a,
                }
            )

        # Stage 4: Recovery Trigger
        if gray_failure_rate is not None:
            sim_app.state.registry.get("acquirer_alpha").set_success_rate(0.95)
        else:
            await client.post(
                "/acquirers/acquirer_alpha/admin/outage",
                json=OutageToggleRequest(active=False).model_dump(),
            )

        # Stage 5: Recovery Phase (Tx 101-150)
        for i in range(101, 151):
            req = AuthorizeRequest(transaction_id=f"tx_recovery_{i}", amount=50.0)
            res = await router.route(req)
            alloc_a = (
                res.smoothed_allocation["acquirer_alpha"]
                if res.smoothed_allocation
                else (1.0 if res.selected_acquirer == "acquirer_alpha" else 0.0)
            )
            history.append(
                {
                    "tx": i,
                    "stage": "RECOVERY",
                    "route": res.selected_acquirer,
                    "authorized": res.authorized,
                    "status": res.status,
                    "alloc_alpha": alloc_a,
                }
            )

        return _compute_summary(name, history)


async def run_dynamic_scenario(
    name: str,
    pid_config: PIDConfig | None,
    seed: int = 777,
) -> RunSummary:
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

        history: list[dict[str, Any]] = []

        # Warmup
        for i in range(1, 51):
            req = AuthorizeRequest(transaction_id=f"tx_warmup_{i}", amount=50.0)
            res = await router.route(req)
            alloc_a = (
                res.smoothed_allocation["acquirer_alpha"]
                if res.smoothed_allocation
                else (1.0 if res.selected_acquirer == "acquirer_alpha" else 0.0)
            )
            history.append(
                {
                    "tx": i,
                    "stage": "WARMUP",
                    "route": res.selected_acquirer,
                    "authorized": res.authorized,
                    "status": res.status,
                    "alloc_alpha": alloc_a,
                }
            )

        # Outage
        await client.post(
            "/acquirers/acquirer_alpha/admin/outage",
            json=OutageToggleRequest(active=True).model_dump(),
        )
        for i in range(51, 101):
            req = AuthorizeRequest(transaction_id=f"tx_outage_{i}", amount=50.0)
            res = await router.route(req)
            alloc_a = (
                res.smoothed_allocation["acquirer_alpha"]
                if res.smoothed_allocation
                else (1.0 if res.selected_acquirer == "acquirer_alpha" else 0.0)
            )
            history.append(
                {
                    "tx": i,
                    "stage": "OUTAGE",
                    "route": res.selected_acquirer,
                    "authorized": res.authorized,
                    "status": res.status,
                    "alloc_alpha": alloc_a,
                }
            )

        # Recovery
        await client.post(
            "/acquirers/acquirer_alpha/admin/outage",
            json=OutageToggleRequest(active=False).model_dump(),
        )
        for i in range(101, 151):
            req = AuthorizeRequest(transaction_id=f"tx_recovery_{i}", amount=50.0)
            res = await router.route(req)
            alloc_a = (
                res.smoothed_allocation["acquirer_alpha"]
                if res.smoothed_allocation
                else (1.0 if res.selected_acquirer == "acquirer_alpha" else 0.0)
            )
            history.append(
                {
                    "tx": i,
                    "stage": "RECOVERY",
                    "route": res.selected_acquirer,
                    "authorized": res.authorized,
                    "status": res.status,
                    "alloc_alpha": alloc_a,
                }
            )

        return _compute_summary(name, history)


def _compute_summary(name: str, history: list[dict[str, Any]]) -> RunSummary:
    total_tx = len(history)
    auth_tx = sum(1 for h in history if h["authorized"])
    declined_tx = sum(1 for h in history if h["status"] == "DECLINED")
    error_tx = sum(1 for h in history if h["status"] == "ERROR")
    psr = auth_tx / total_tx if total_tx > 0 else 0.0

    warmup = history[0:50]
    outage = history[50:100]
    recovery = history[100:150]

    warmup_psr = sum(1 for h in warmup if h["authorized"]) / len(warmup)
    outage_psr = sum(1 for h in outage if h["authorized"]) / len(outage)
    recovery_psr = sum(1 for h in recovery if h["authorized"]) / len(recovery)

    outage_failures_alpha = sum(
        1 for h in outage if h["route"] == "acquirer_alpha" and not h["authorized"]
    )
    outage_txs_alpha = sum(1 for h in outage if h["route"] == "acquirer_alpha")
    outage_txs_beta = sum(1 for h in outage if h["route"] == "acquirer_beta")

    recovery_txs_alpha = sum(1 for h in recovery if h["route"] == "acquirer_alpha")
    recovery_txs_beta = sum(1 for h in recovery if h["route"] == "acquirer_beta")

    routes = [h["route"] for h in history]
    total_flips = sum(1 for i in range(1, len(routes)) if routes[i] != routes[i - 1])

    outage_routes = [h["route"] for h in outage]
    outage_flips = sum(
        1 for i in range(1, len(outage_routes)) if outage_routes[i] != outage_routes[i - 1]
    )

    allocs = [h["alloc_alpha"] for h in history]
    deltas = [abs(allocs[i] - allocs[i - 1]) for i in range(1, len(allocs))]
    max_step_delta = max(deltas) if deltas else 0.0

    return RunSummary(
        name=name,
        total_tx=total_tx,
        authorized_tx=auth_tx,
        declined_tx=declined_tx,
        error_tx=error_tx,
        psr=psr,
        warmup_psr=warmup_psr,
        outage_psr=outage_psr,
        recovery_psr=recovery_psr,
        outage_failures_alpha=outage_failures_alpha,
        outage_txs_alpha=outage_txs_alpha,
        outage_txs_beta=outage_txs_beta,
        outage_flips=outage_flips,
        total_flips=total_flips,
        max_step_delta=max_step_delta,
        recovery_txs_alpha=recovery_txs_alpha,
        recovery_txs_beta=recovery_txs_beta,
        history=history,
    )


async def main() -> None:
    print("=" * 100)
    print("PHASE 6 QA GAUNTLET: STATIC BASELINE VS LOOM EMPIRICAL PSR COMPARISON")
    print("Identical Environment: Alpha (base 95%), Beta (base 94%), Sim Seed=42, 150 Transactions")
    print("=" * 100)

    # 1. Standard Static Baseline (M=3, Cooldown=30, Probe)
    base_m3 = await run_baseline_scenario(
        "Static Baseline (M=3, Cooldown=30, Probe)",
        FailoverPolicyConfig(
            consecutive_failure_threshold=3, cooldown_transactions=30, failback_mode="probe"
        ),
    )

    # 2. Overreaction Baseline (M=1, Cooldown=30, Probe)
    base_m1 = await run_baseline_scenario(
        "Static Baseline (M=1, Sensitive / Overreaction)",
        FailoverPolicyConfig(
            consecutive_failure_threshold=1, cooldown_transactions=30, failback_mode="probe"
        ),
    )

    # 3. Conservative Baseline (M=5, Cooldown=30, Probe)
    base_m5 = await run_baseline_scenario(
        "Static Baseline (M=5, Conservative / Underreaction)",
        FailoverPolicyConfig(
            consecutive_failure_threshold=5, cooldown_transactions=30, failback_mode="probe"
        ),
    )

    # 4. Standard Static Baseline Snapback (M=3, Cooldown=30, Snapback)
    base_m3_snap = await run_baseline_scenario(
        "Static Baseline (M=3, Cooldown=30, Snapback)",
        FailoverPolicyConfig(
            consecutive_failure_threshold=3, cooldown_transactions=30, failback_mode="snapback"
        ),
    )

    # 5. Gray Failure Scenario (Alpha degrades to 60% PSR during outage window, M=3)
    base_gray_m3 = await run_baseline_scenario(
        "Static Baseline (M=3 under Gray Failure 60% Brownout)",
        FailoverPolicyConfig(
            consecutive_failure_threshold=3, cooldown_transactions=30, failback_mode="probe"
        ),
        gray_failure_rate=0.60,
    )

    # 6. Phase 3 Raw Bandit (No PID)
    dyn_p3 = await run_dynamic_scenario(
        "Loom Phase 3 (Raw Bandit, Hard Argmax)",
        pid_config=None,
    )

    # 7. Phase 4 Tuned PID
    dyn_p4 = await run_dynamic_scenario(
        "Loom Phase 4 (Tuned PID Smoothed)",
        pid_config=PIDConfig(
            kp=0.12,
            ki=0.005,
            kd=0.25,
            integral_max=1.0,
            min_allocation=0.03,
            actuation_mode="deficit",
        ),
    )

    runs = [base_m3, base_m1, base_m5, base_m3_snap, base_gray_m3, dyn_p3, dyn_p4]

    header = (
        f"{'Configuration':<42} | {'Global PSR':<10} | {'Warmup':<7} | {'Outage':<7} | "
        f"{'Recov':<7} | {'Auth':<5} | {'Fail':<5} | {'Delta_w':<7} | {'Flips'}"
    )
    print("\n" + header)
    print("-" * len(header))

    for r in runs:
        fails = r.total_tx - r.authorized_tx
        row = (
            f"{r.name:<42} | {r.psr * 100:>8.2f}% | {r.warmup_psr * 100:>5.1f}% | "
            f"{r.outage_psr * 100:>5.1f}% | {r.recovery_psr * 100:>5.1f}% | "
            f"{r.authorized_tx:>4} | {fails:>4} | {r.max_step_delta * 100:>5.1f}% | "
            f"{r.total_flips:>3}"
        )
        print(row)

    print("\n" + "=" * 100)
    print("DETAILED PATHOLOGY AUDIT:")
    print("=" * 100)
    for r in runs:
        w_alpha = sum(1 for h in r.history[0:50] if h["route"] == "acquirer_alpha")
        w_beta = sum(1 for h in r.history[0:50] if h["route"] == "acquirer_beta")
        print(f"\n--- {r.name} ---")
        print(f"  Authorized / Total: {r.authorized_tx} / {r.total_tx} ({r.psr * 100:.2f}%)")
        print(f"  Warmup Split (Alpha / Beta): {w_alpha} / {w_beta}")
        print(f"  Outage Split (Alpha / Beta): {r.outage_txs_alpha} / {r.outage_txs_beta}")
        print(f"  Outage Failures on Alpha: {r.outage_failures_alpha}")
        print(f"  Outage Flips: {r.outage_flips}, Total Flips: {r.total_flips}")
        print(f"  Max Single-Step Allocation Jump: {r.max_step_delta * 100:.2f}%")
        print(f"  Recovery Split (Alpha / Beta): {r.recovery_txs_alpha} / {r.recovery_txs_beta}")
        print(f"  Recovery PSR: {r.recovery_psr * 100:.2f}%")


if __name__ == "__main__":
    asyncio.run(main())
