"""Comparative PSR benchmark tool evaluating Loom dynamic router against static baseline.

Executes identical transaction stream against simulated acquirers, logs to separate
SQLite databases using identical schemas, and computes empirical PSR lift and stability metrics.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from typing import Any

import httpx

from acquirer_sim.app import create_app
from acquirer_sim.models import AuthorizeRequest, LatencyConfig, OutageToggleRequest
from baseline_router.models import (
    BaselineRouterConfig,
    FailoverPolicyConfig,
)
from baseline_router.router import StaticBaselineRouter
from data_layer.sqlite_logger import SQLiteMetricsStore
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig


async def execute_scenario(
    router_type: str,
    db_path: str,
    warmup_count: int = 50,
    outage_count: int = 50,
    recovery_count: int = 50,
    seed: int = 42,
    threshold_m: int = 3,
    cooldown_n: int = 30,
) -> dict[str, Any]:
    """Execute complete benchmark run for either 'baseline' or 'loom'."""
    # Ensure fresh DB
    if os.path.exists(db_path):
        os.remove(db_path)

    metrics_store = SQLiteMetricsStore(db_path=db_path)

    sim_app = create_app(
        default_acquirers=["acquirer_alpha", "acquirer_beta"],
        default_base_rate=0.95,
        default_latency=LatencyConfig(base_ms=0.0, jitter_ms=0.0),
        seed=seed,
    )
    sim_app.state.registry.get("acquirer_alpha").set_success_rate(0.95)
    sim_app.state.registry.get("acquirer_beta").set_success_rate(0.94)

    transport = httpx.ASGITransport(app=sim_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        routes = [
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
        ]

        if router_type == "baseline":
            config = BaselineRouterConfig(
                routes=routes,
                priority_order=["acquirer_alpha", "acquirer_beta"],
                failover_policy=FailoverPolicyConfig(
                    consecutive_failure_threshold=threshold_m,
                    cooldown_transactions=cooldown_n,
                    failback_mode="probe",
                ),
            )
            router: Any = StaticBaselineRouter(
                config=config,
                http_client=client,
                metrics_logger=metrics_store,
            )
        else:
            router_config = RouterConfig(
                routes=routes,
                pid_config=PIDConfig(
                    kp=0.12,
                    ki=0.005,
                    kd=0.25,
                    integral_max=1.0,
                    min_allocation=0.03,
                    actuation_mode="deficit",
                ),
                seed=777,
            )
            router = BanditRouter(
                config=router_config,
                http_client=client,
            )

        results = []
        alloc_alpha = []
        routes_chosen = []

        # 1. Warmup Phase
        for i in range(1, warmup_count + 1):
            req = AuthorizeRequest(transaction_id=f"tx_{router_type}_warmup_{i}", amount=50.0)
            res = await router.route(req)
            if router_type == "loom":
                metrics_store.log_routing_result(res)
            results.append(res)
            alloc = (
                res.smoothed_allocation["acquirer_alpha"]
                if res.smoothed_allocation
                else (1.0 if res.selected_acquirer == "acquirer_alpha" else 0.0)
            )
            alloc_alpha.append(alloc)
            routes_chosen.append(res.selected_acquirer)

        # 2. Trigger Outage
        await client.post(
            "/acquirers/acquirer_alpha/admin/outage",
            json=OutageToggleRequest(active=True).model_dump(),
        )

        # 3. Outage Phase
        for i in range(1, outage_count + 1):
            req = AuthorizeRequest(transaction_id=f"tx_{router_type}_outage_{i}", amount=50.0)
            res = await router.route(req)
            if router_type == "loom":
                metrics_store.log_routing_result(res)
            results.append(res)
            alloc = (
                res.smoothed_allocation["acquirer_alpha"]
                if res.smoothed_allocation
                else (1.0 if res.selected_acquirer == "acquirer_alpha" else 0.0)
            )
            alloc_alpha.append(alloc)
            routes_chosen.append(res.selected_acquirer)

        # 4. Clear Outage (Recovery)
        await client.post(
            "/acquirers/acquirer_alpha/admin/outage",
            json=OutageToggleRequest(active=False).model_dump(),
        )

        # 5. Recovery Phase
        for i in range(1, recovery_count + 1):
            req = AuthorizeRequest(transaction_id=f"tx_{router_type}_recovery_{i}", amount=50.0)
            res = await router.route(req)
            if router_type == "loom":
                metrics_store.log_routing_result(res)
            results.append(res)
            alloc = (
                res.smoothed_allocation["acquirer_alpha"]
                if res.smoothed_allocation
                else (1.0 if res.selected_acquirer == "acquirer_alpha" else 0.0)
            )
            alloc_alpha.append(alloc)
            routes_chosen.append(res.selected_acquirer)

        # Query metrics directly from SQLite ledger
        global_metrics = metrics_store.get_psr_metrics()

        warmup_txs = results[:warmup_count]
        outage_txs = results[warmup_count : warmup_count + outage_count]
        recovery_txs = results[warmup_count + outage_count :]

        deltas = [abs(alloc_alpha[k] - alloc_alpha[k - 1]) for k in range(1, len(alloc_alpha))]
        max_delta = max(deltas) if deltas else 0.0

        outage_routes = routes_chosen[warmup_count : warmup_count + outage_count]
        outage_flips = sum(
            1 for k in range(1, len(outage_routes)) if outage_routes[k] != outage_routes[k - 1]
        )

        metrics_store.close()

        return {
            "global_metrics": global_metrics,
            "warmup_psr": sum(1 for r in warmup_txs if r.authorized) / len(warmup_txs),
            "warmup_auth": sum(1 for r in warmup_txs if r.authorized),
            "outage_psr": sum(1 for r in outage_txs if r.authorized) / len(outage_txs),
            "outage_auth": sum(1 for r in outage_txs if r.authorized),
            "outage_failures_alpha": sum(
                1
                for r in outage_txs
                if r.selected_acquirer == "acquirer_alpha" and not r.authorized
            ),
            "recovery_psr": sum(1 for r in recovery_txs if r.authorized) / len(recovery_txs),
            "recovery_auth": sum(1 for r in recovery_txs if r.authorized),
            "recovery_txs_alpha": sum(
                1 for r in recovery_txs if r.selected_acquirer == "acquirer_alpha"
            ),
            "max_step_delta": max_delta,
            "outage_flips": outage_flips,
            "total_flips": sum(
                1 for k in range(1, len(routes_chosen)) if routes_chosen[k] != routes_chosen[k - 1]
            ),
        }


def format_report(base: dict[str, Any], loom: dict[str, Any]) -> str:
    """Format comparative audit report table."""
    b_glob = base["global_metrics"]
    l_glob = loom["global_metrics"]

    b_psr = b_glob["psr"] * 100
    l_psr = l_glob["psr"] * 100
    delta_psr = l_psr - b_psr
    bps = delta_psr * 100

    report = []
    report.append("=" * 90)
    report.append("                   LOOM vs STATIC BASELINE: PSR LIFT AUDIT")
    report.append("=" * 90)
    report.append("Scenario  : 150 transactions (50 Warmup -> 50 Outage -> 50 Recovery)")
    report.append("Acquirers : Alpha (Primary: 95% base PSR), Beta (Secondary: 94% base PSR)")
    report.append("Policy    : Static Priority [Alpha, Beta], Threshold M=3, Cooldown N=30")
    report.append("-" * 90)
    header = f"{'METRIC':<28} | {'STATIC BASELINE':<18} | {'LOOM DYNAMIC':<18} | {'DELTA / LIFT'}"
    report.append(header)
    report.append("-" * 90)

    report.append(
        f"{'Global Transactions':<28} | {b_glob['total_transactions']:<18} | "
        f"{l_glob['total_transactions']:<18} | 0"
    )
    auth_diff = l_glob["authorized_count"] - b_glob["authorized_count"]
    dec_diff = l_glob["declined_count"] - b_glob["declined_count"]
    report.append(
        f"{'Global Authorized':<28} | {b_glob['authorized_count']:<18} | "
        f"{l_glob['authorized_count']:<18} | {auth_diff:+d} authorizations"
    )
    report.append(
        f"{'Global Declined':<28} | {b_glob['declined_count']:<18} | "
        f"{l_glob['declined_count']:<18} | {dec_diff:+d}"
    )
    report.append(
        f"{'Global PSR':<28} | {b_psr:>16.2f}% | {l_psr:>16.2f}% | "
        f"{delta_psr:+6.2f}% ({bps:+6.0f} bps)"
    )
    report.append("-" * 90)
    report.append("WINDOW BREAKDOWN:")
    w_diff = (loom["warmup_psr"] - base["warmup_psr"]) * 100
    o_diff = (loom["outage_psr"] - base["outage_psr"]) * 100
    r_diff = (loom["recovery_psr"] - base["recovery_psr"]) * 100
    fail_diff = loom["outage_failures_alpha"] - base["outage_failures_alpha"]
    report.append(
        f"1. Warmup (Tx 1-50) PSR    | {base['warmup_psr'] * 100:>16.2f}% | "
        f"{loom['warmup_psr'] * 100:>16.2f}% | {w_diff:+6.2f}%"
    )
    report.append(
        f"2. Outage (Tx 51-100) PSR  | {base['outage_psr'] * 100:>16.2f}% | "
        f"{loom['outage_psr'] * 100:>16.2f}% | {o_diff:+6.2f}%"
    )
    report.append(
        f"   - Failures on Alpha     | {base['outage_failures_alpha']:<18} | "
        f"{loom['outage_failures_alpha']:<18} | {fail_diff:+d}"
    )
    report.append(
        f"3. Recovery (Tx 101-150) PSR| {base['recovery_psr'] * 100:>16.2f}% | "
        f"{loom['recovery_psr'] * 100:>16.2f}% | {r_diff:+6.2f}%"
    )
    report.append("-" * 90)
    report.append("DYNAMICS & STABILITY:")
    dw_diff = (loom["max_step_delta"] - base["max_step_delta"]) * 100
    report.append(
        f"{'Peak Allocation Jump (dw)':<28} | {base['max_step_delta'] * 100:>15.1f}% | "
        f"{loom['max_step_delta'] * 100:>15.2f}% | {dw_diff:+6.2f}%"
    )
    report.append(
        f"{'Outage Route Flips':<28} | {base['outage_flips']:<18} | "
        f"{loom['outage_flips']:<18} | {loom['outage_flips'] - base['outage_flips']:+d}"
    )
    report.append(
        f"{'Routing Latency (ms)':<28} | {b_glob['avg_routing_latency_ms']:>15.4f} ms | "
        f"{l_glob['avg_routing_latency_ms']:>15.4f} ms | "
        f"{l_glob['avg_routing_latency_ms'] - b_glob['avg_routing_latency_ms']:+.4f} ms"
    )
    report.append("=" * 90)

    return "\n".join(report)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Compare Loom vs Static Baseline PSR")
    parser.add_argument(
        "--base-db", default="baseline_metrics.db", help="Path to baseline SQLite db"
    )
    parser.add_argument("--loom-db", default="loom_metrics.db", help="Path to Loom SQLite db")
    parser.add_argument("--threshold-m", type=int, default=3, help="Failover threshold M")
    parser.add_argument("--cooldown-n", type=int, default=30, help="Cooldown window N")
    args = parser.parse_args()

    print("Running Static Baseline benchmark...")
    base_res = await execute_scenario(
        router_type="baseline",
        db_path=args.base_db,
        threshold_m=args.threshold_m,
        cooldown_n=args.cooldown_n,
    )

    print("Running Loom Dynamic benchmark...")
    loom_res = await execute_scenario(
        router_type="loom",
        db_path=args.loom_db,
    )

    print("\n" + format_report(base_res, loom_res))


if __name__ == "__main__":
    asyncio.run(main())
