"""Comparative PSR benchmark tool evaluating Loom dynamic router against static baseline.

Executes identical transaction stream against simulated acquirers, logs to separate
SQLite databases using identical schemas, and computes empirical PSR lift and stability metrics.

Two modes:

* ``--n-seeds 1`` (default): one Loom run and one static-baseline run at ``--seed`` /
  ``--loom-seed``, logged to ``--base-db`` / ``--loom-db``, printed as a side-by-side table.
* ``--n-seeds N`` (N >= 2): N paired seeds. Run ``k`` uses simulator seed ``seed + 10 * k``
  and Loom seed ``loom_seed + k`` for every configuration, so each comparison is paired.
  Simulator seeds are spaced by 10 because the simulator gives acquirer ``i`` the stream
  ``default_rng(seed + i)``; adjacent seeds would share streams. Metrics are computed from
  in-memory SQLite ledgers, so this mode writes no database files.

``--out-json`` writes the results (both modes) as JSON; the dashboard's baseline comparison
card reads the multi-seed output from ``dashboard/src/data/baselineComparison.json``.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import statistics
import sys
from pathlib import Path
from typing import Any

import httpx
from scipy import stats

from acquirer_sim.app import create_app
from acquirer_sim.models import AuthorizeRequest, LatencyConfig, OutageToggleRequest
from baseline_router.models import (
    BaselineRouterConfig,
    FailoverPolicyConfig,
)
from baseline_router.router import StaticBaselineRouter
from data_layer.config import DataLayerConfig
from data_layer.sqlite_logger import SQLiteMetricsStore
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig
from router_core.router import BanditRouter
from router_core.state import DEFAULT_ALPHA_PRIOR, AcquirerStateConfig

DEFAULT_BASE_DB = "compare_psr_baseline.db"
DEFAULT_LOOM_DB = "compare_psr_loom.db"
SEED_SPACING = 10
ALPHA_BASE_RATE = 0.95
BETA_BASE_RATE = 0.94
GRAY_FAILURE_RATE = 0.60
# Transactions arrive on a virtual clock at this rate, so belief decay is measured in
# simulated seconds and results do not depend on how fast the machine runs.
BENCH_TPS = 15.0
# 0.9 s at 15 TPS equals the 13.5-observation half-life of the gamma=0.95 this benchmark
# used before decay moved to the wall clock (AUDIT F-23).
BENCH_HALF_LIFE_SEC = 0.9


def _check_db_path(db_path: str) -> None:
    """Refuse to delete the data layer's default metrics ledger."""
    protected = Path(DataLayerConfig().sqlite_db_path).resolve()
    if Path(db_path).resolve() == protected:
        raise SystemExit(
            f"Refusing to use {db_path!r}: it is the data layer's default metrics ledger, "
            "and this script deletes its database before each run. Pick another path."
        )


async def execute_scenario(
    router_type: str,
    db_path: str,
    warmup_count: int = 50,
    outage_count: int = 50,
    recovery_count: int = 50,
    seed: int = 42,
    threshold_m: int = 3,
    cooldown_n: int = 30,
    loom_seed: int = 777,
    gray_rate: float | None = None,
    alpha_prior: float = DEFAULT_ALPHA_PRIOR,
) -> dict[str, Any]:
    """Execute complete benchmark run for 'baseline', 'loom' (PID) or 'raw' (no PID).

    ``gray_rate`` replaces the hard outage on Alpha with a degraded success rate.
    ``alpha_prior`` sets Loom's technical prior Beta(alpha_prior, 1).
    ``db_path=":memory:"`` keeps the ledger in memory and writes no file.
    """
    if db_path != ":memory:":
        _check_db_path(db_path)
        # Ensure fresh DB
        if os.path.exists(db_path):
            os.remove(db_path)

    metrics_store = SQLiteMetricsStore(db_path=db_path)

    sim_app = create_app(
        default_acquirers=["acquirer_alpha", "acquirer_beta"],
        default_base_rate=ALPHA_BASE_RATE,
        default_latency=LatencyConfig(base_ms=0.0, jitter_ms=0.0),
        seed=seed,
    )
    sim_app.state.registry.get("acquirer_alpha").set_success_rate(ALPHA_BASE_RATE)
    sim_app.state.registry.get("acquirer_beta").set_success_rate(BETA_BASE_RATE)

    now = [0.0]
    transport = httpx.ASGITransport(app=sim_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        routes = [
            AcquirerRouteConfig(
                acquirer_id="acquirer_alpha",
                base_url="http://testserver",
                state_config=AcquirerStateConfig(
                    half_life_sec=BENCH_HALF_LIFE_SEC, alpha_prior=alpha_prior
                ),
            ),
            AcquirerRouteConfig(
                acquirer_id="acquirer_beta",
                base_url="http://testserver",
                state_config=AcquirerStateConfig(
                    half_life_sec=BENCH_HALF_LIFE_SEC, alpha_prior=alpha_prior
                ),
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
        elif router_type in ("loom", "raw"):
            router_config = RouterConfig(
                routes=routes,
                pid_config=(
                    PIDConfig(
                        kp=0.12,
                        ki=0.005,
                        kd=0.25,
                        integral_max=1.0,
                        min_allocation=0.03,
                        actuation_mode="deficit",
                    )
                    if router_type == "loom"
                    else None
                ),
                seed=loom_seed,
            )
            router = BanditRouter(
                config=router_config,
                http_client=client,
                clock=lambda: now[0],
            )
        else:
            raise ValueError(f"unknown router_type {router_type!r}")

        results = []
        alloc_alpha = []
        routes_chosen = []

        async def run_stage(stage: str, count: int) -> None:
            for i in range(1, count + 1):
                req = AuthorizeRequest(transaction_id=f"tx_{router_type}_{stage}_{i}", amount=50.0)
                now[0] += 1.0 / BENCH_TPS
                res = await router.route(req)
                if router_type != "baseline":
                    metrics_store.log_routing_result(res)
                results.append(res)
                alloc = (
                    res.smoothed_allocation["acquirer_alpha"]
                    if res.smoothed_allocation
                    else (1.0 if res.selected_acquirer == "acquirer_alpha" else 0.0)
                )
                alloc_alpha.append(alloc)
                routes_chosen.append(res.selected_acquirer)

        async def set_outage(active: bool) -> None:
            if gray_rate is None:
                await client.post(
                    "/acquirers/acquirer_alpha/admin/outage",
                    json=OutageToggleRequest(active=active).model_dump(),
                )
            else:
                sim_app.state.registry.get("acquirer_alpha").set_success_rate(
                    gray_rate if active else ALPHA_BASE_RATE
                )

        # 1. Warmup Phase
        await run_stage("warmup", warmup_count)
        # 2. Trigger Outage, 3. Outage Phase
        await set_outage(True)
        await run_stage("outage", outage_count)
        # 4. Clear Outage, 5. Recovery Phase
        await set_outage(False)
        await run_stage("recovery", recovery_count)

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
            "alpha_allocation": alloc_alpha,
            "outage_flips": outage_flips,
            "total_flips": sum(
                1 for k in range(1, len(routes_chosen)) if routes_chosen[k] != routes_chosen[k - 1]
            ),
        }


def format_report(
    base: dict[str, Any], loom: dict[str, Any], threshold_m: int = 3, cooldown_n: int = 30
) -> str:
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
    report.append(
        "Scenario  : 150 transactions (50 Warmup -> 50 Outage -> 50 Recovery) at "
        f"{BENCH_TPS:g} TPS on a virtual clock; Loom half-life {BENCH_HALF_LIFE_SEC:g} s"
    )
    report.append("Acquirers : Alpha (Primary: 95% base PSR), Beta (Secondary: 94% base PSR)")
    report.append(
        f"Policy    : Static Priority [Alpha, Beta], Threshold M={threshold_m}, "
        f"Cooldown N={cooldown_n}"
    )
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


# Multi-seed paired comparison ---------------------------------------------------------------

# Each configuration: (router_type, threshold_m, gray_rate).
MULTI_SEED_CONFIGS: dict[str, tuple[str, int, float | None]] = {
    "loom": ("loom", 3, None),
    "raw": ("raw", 3, None),
    "static_m1": ("baseline", 1, None),
    "static_m3": ("baseline", 3, None),
    "static_m5": ("baseline", 5, None),
    "loom_gray": ("loom", 3, GRAY_FAILURE_RATE),
    "static_m3_gray": ("baseline", 3, GRAY_FAILURE_RATE),
}

# Each comparison: (key, label, first config, second config). Difference = first - second.
COMPARISONS: list[tuple[str, str, str, str]] = [
    ("loom_vs_m1", "Loom vs static M=1 (hard outage)", "loom", "static_m1"),
    ("loom_vs_m3", "Loom vs static M=3 (hard outage)", "loom", "static_m3"),
    ("loom_vs_m5", "Loom vs static M=5 (hard outage)", "loom", "static_m5"),
    (
        "loom_vs_m3_gray",
        "Loom vs static M=3 (gray failure, Alpha at 60%)",
        "loom_gray",
        "static_m3_gray",
    ),
    ("pid_vs_raw", "Loom with PID vs raw bandit (hard outage)", "loom", "raw"),
]


def summarize_paired(diffs: list[float]) -> dict[str, Any]:
    """Mean paired difference with a t-based 95% CI and wins/ties/losses (first vs second)."""
    n = len(diffs)
    mean = statistics.mean(diffs)
    sd = statistics.stdev(diffs) if n > 1 else 0.0
    half = float(stats.t.ppf(0.975, n - 1)) * sd / n**0.5 if n > 1 else 0.0
    wins = sum(1 for d in diffs if d > 1e-12)
    ties = sum(1 for d in diffs if abs(d) <= 1e-12)
    return {
        "n": n,
        "mean_diff_pp": mean * 100,
        "ci95_low_pp": (mean - half) * 100,
        "ci95_high_pp": (mean + half) * 100,
        "wins": wins,
        "ties": ties,
        "losses": n - wins - ties,
    }


async def run_multi_seed(
    n_seeds: int,
    seed: int,
    loom_seed: int,
    cooldown_n: int,
    alpha_prior: float = DEFAULT_ALPHA_PRIOR,
) -> dict[str, Any]:
    """Run every configuration on N paired seeds and summarize the paired PSR differences."""
    rows: list[dict[str, Any]] = []
    for k in range(n_seeds):
        sim_seed = seed + SEED_SPACING * k
        row: dict[str, Any] = {"sim_seed": sim_seed, "loom_seed": loom_seed + k}
        for name, (router_type, m, gray) in MULTI_SEED_CONFIGS.items():
            res = await execute_scenario(
                router_type=router_type,
                db_path=":memory:",
                seed=sim_seed,
                threshold_m=m,
                cooldown_n=cooldown_n,
                loom_seed=loom_seed + k,
                gray_rate=gray,
                alpha_prior=alpha_prior,
            )
            row[name] = {
                "psr": res["global_metrics"]["psr"],
                "outage_psr": res["outage_psr"],
                "outage_failures_alpha": res["outage_failures_alpha"],
                "outage_flips": res["outage_flips"],
                "max_step_delta": res["max_step_delta"],
                "recovery_txs_alpha": res["recovery_txs_alpha"],
            }
        rows.append(row)

    configs = {
        name: {
            "router": router_type,
            "threshold_m": m if router_type == "baseline" else None,
            "gray_rate": gray,
            "mean_psr_pct": statistics.mean(r[name]["psr"] for r in rows) * 100,
            "mean_outage_flips": statistics.mean(r[name]["outage_flips"] for r in rows),
            "mean_outage_failures_alpha": statistics.mean(
                r[name]["outage_failures_alpha"] for r in rows
            ),
            "mean_recovery_txs_alpha": statistics.mean(r[name]["recovery_txs_alpha"] for r in rows),
            "max_step_delta_range_pct": [
                min(r[name]["max_step_delta"] for r in rows) * 100,
                max(r[name]["max_step_delta"] for r in rows) * 100,
            ],
        }
        for name, (router_type, m, gray) in MULTI_SEED_CONFIGS.items()
    }
    comparisons = {
        key: {
            "label": label,
            "first": first,
            "second": second,
            **summarize_paired([r[first]["psr"] - r[second]["psr"] for r in rows]),
        }
        for key, label, first, second in COMPARISONS
    }
    return {
        "mode": "multi_seed",
        "n_seeds": n_seeds,
        "seed": seed,
        "seed_spacing": SEED_SPACING,
        "loom_seed": loom_seed,
        "cooldown_n": cooldown_n,
        "alpha_prior": alpha_prior,
        "scenario": {
            "transactions": 150,
            "stages": "50 warmup, 50 outage on Alpha, 50 recovery",
            "alpha_rate": ALPHA_BASE_RATE,
            "beta_rate": BETA_BASE_RATE,
            "gray_rate": GRAY_FAILURE_RATE,
            "tps": BENCH_TPS,
            "loom": (
                f"half-life {BENCH_HALF_LIFE_SEC:g} s, technical prior Beta({alpha_prior:g},1), "
                "PID kp=0.12 ki=0.005 kd=0.25, floor 0.03, deficit actuation"
            ),
        },
        "configs": configs,
        "comparisons": comparisons,
        "rows": rows,
    }


def format_multi_seed_report(summary: dict[str, Any]) -> str:
    """Format the paired multi-seed comparison table."""
    n = summary["n_seeds"]
    seed = summary["seed"]
    report = ["=" * 100]
    report.append(f"     LOOM vs STATIC BASELINE: {n} PAIRED SEEDS")
    report.append("=" * 100)
    report.append(
        f"Seeds     : simulator {seed} + {summary['seed_spacing']}k, "
        f"Loom {summary['loom_seed']} + k, k = 0..{n - 1}"
    )
    report.append(
        "Scenario  : 150 transactions (50 Warmup -> 50 Outage -> 50 Recovery) at "
        f"{BENCH_TPS:g} TPS on a virtual clock; Loom half-life {BENCH_HALF_LIFE_SEC:g} s"
    )
    report.append(
        f"Policy    : Static Priority [Alpha, Beta], Threshold M in {{1, 3, 5}}, "
        f"Cooldown N={summary['cooldown_n']}"
    )
    report.append("-" * 100)
    report.append(f"Loom      : technical prior Beta({summary['alpha_prior']:g}, 1)")
    report.append("-" * 100)
    report.append(
        f"{'CONFIGURATION':<34} | {'MEAN PSR':>9} | {'OUTAGE FLIPS':>12} | "
        f"{'RECOVERY TX TO ALPHA':>20}"
    )
    for name, cfg in summary["configs"].items():
        report.append(
            f"{name:<34} | {cfg['mean_psr_pct']:>8.2f}% | {cfg['mean_outage_flips']:>12.2f} | "
            f"{cfg['mean_recovery_txs_alpha']:>20.2f}"
        )
    report.append("-" * 100)
    report.append(f"{'COMPARISON (first - second)':<50} | {'MEAN':>9} | {'95% CI':>18} | W/T/L")
    for cmp in summary["comparisons"].values():
        report.append(
            f"{cmp['label']:<50} | {cmp['mean_diff_pp']:>+7.2f}pp | "
            f"[{cmp['ci95_low_pp']:+6.2f}, {cmp['ci95_high_pp']:+6.2f}] | "
            f"{cmp['wins']}/{cmp['ties']}/{cmp['losses']}"
        )
    report.append("=" * 100)
    return "\n".join(report)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(description="Compare Loom vs Static Baseline PSR")
    parser.add_argument(
        "--base-db", default=DEFAULT_BASE_DB, help="Path to baseline SQLite db (single-seed mode)"
    )
    parser.add_argument(
        "--loom-db", default=DEFAULT_LOOM_DB, help="Path to Loom SQLite db (single-seed mode)"
    )
    parser.add_argument("--threshold-m", type=int, default=3, help="Failover threshold M")
    parser.add_argument("--cooldown-n", type=int, default=30, help="Cooldown window N")
    parser.add_argument("--seed", type=int, default=42, help="Simulator seed (first seed)")
    parser.add_argument("--loom-seed", type=int, default=777, help="Loom router seed")
    parser.add_argument(
        "--n-seeds",
        type=int,
        default=1,
        help="Number of paired seeds; 2 or more runs the multi-seed comparison",
    )
    parser.add_argument(
        "--alpha-prior",
        type=float,
        default=DEFAULT_ALPHA_PRIOR,
        help=f"Loom's technical prior Beta(alpha_prior, 1) (default {DEFAULT_ALPHA_PRIOR:g})",
    )
    parser.add_argument("--out-json", default=None, help="Write results to this JSON file")
    return parser


async def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    if args.n_seeds < 1:
        raise SystemExit("--n-seeds must be at least 1")

    if args.n_seeds >= 2:
        # Hundreds of runs: silence the per-transaction circuit-breaker warnings.
        breaker_logger = logging.getLogger("loom.baseline_router")
        previous_level = breaker_logger.level
        breaker_logger.setLevel(logging.ERROR)
        print(f"Running {args.n_seeds} paired seeds...")
        try:
            summary = await run_multi_seed(
                n_seeds=args.n_seeds,
                seed=args.seed,
                loom_seed=args.loom_seed,
                cooldown_n=args.cooldown_n,
                alpha_prior=args.alpha_prior,
            )
        finally:
            breaker_logger.setLevel(previous_level)
        print("\n" + format_multi_seed_report(summary))
    else:
        print("Running Static Baseline benchmark...")
        base_res = await execute_scenario(
            router_type="baseline",
            db_path=args.base_db,
            seed=args.seed,
            threshold_m=args.threshold_m,
            cooldown_n=args.cooldown_n,
        )

        print("Running Loom Dynamic benchmark...")
        loom_res = await execute_scenario(
            router_type="loom",
            db_path=args.loom_db,
            seed=args.seed,
            loom_seed=args.loom_seed,
            alpha_prior=args.alpha_prior,
        )

        print("\n" + format_report(base_res, loom_res, args.threshold_m, args.cooldown_n))
        summary = {
            "mode": "single_seed",
            "seed": args.seed,
            "loom_seed": args.loom_seed,
            "threshold_m": args.threshold_m,
            "cooldown_n": args.cooldown_n,
            "baseline": base_res,
            "loom": loom_res,
        }

    summary["command"] = " ".join(["python scripts/compare_psr.py", *(argv or sys.argv[1:])])
    if args.out_json:
        Path(args.out_json).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        print(f"\nWrote {args.out_json}")


if __name__ == "__main__":
    asyncio.run(main())
