"""Run the Stage 0 scenario suite: every policy on every scenario over paired seeds.

    python scripts/suite/run_suite.py --policies all --seeds 200 --split test \\
        --out-json docs/results/suite.json

Every policy of a run sees the same arrivals and the same common random numbers, so policy
differences are paired per seed and reported as mean differences with 95% t-intervals
(``summarize_paired`` from ``compare_psr.py``). The oracle always runs, because regret and the
soak's revenue scoring are measured against it. Results are deterministic for a given split,
seed count and code version; ``--timing`` adds machine-dependent decision timings.
"""

from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):
    # Run as a file: make the repository root importable so ``scripts.suite`` resolves.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import argparse
import json
import logging
import math
import os
import statistics
import time
from concurrent.futures import ProcessPoolExecutor
from typing import Any

from scipy import stats

from scripts.compare_psr import summarize_paired
from scripts.suite.des import simulate
from scripts.suite.metrics import annual_lost_gmv, run_metrics
from scripts.suite.policies import POLICIES, REFERENCE, build_policy
from scripts.suite.scenarios import AMOUNT, SCENARIOS, run_seeds, select

# Paired comparisons reported for every scenario: (first, second); difference = first - second.
COMPARISONS: list[tuple[str, str]] = [
    ("loom_longmem", "competent"),
    ("loom_longmem", "loom_failfast"),
    ("loom_longmem", "sr_window"),
    ("loom_longmem", "sw_ucb"),
    ("loom_longmem_warm", "competent"),
    ("loom_longmem_warm", "loom_longmem"),
    ("loom_longmem_warm", "static_m3"),
    ("loom_failfast", "competent"),
    ("loom_failfast", "loom_served"),
    ("loom_failfast", "sr_window"),
    ("loom_failfast", "sw_ucb"),
    ("loom_served", "competent"),
    ("loom_bench", "competent"),
    ("loom_served", "static_m3"),
    ("loom_served", "sr_window"),
    ("loom_served", "sw_ucb"),
    ("loom_served", "loom_raw"),
    ("competent", "static_m3"),
    ("competent", "competent_noretry"),
]

# Metrics averaged per policy (nan values, e.g. "never diverted", are counted separately).
SUMMARY_METRICS = [
    "psr",
    "incident_psr",
    "added_hops",
    "dead_route_attempts",
    "dead_route_failed_payments",
    "seconds_to_divert",
    "seconds_to_restore",
    "share_shift_pp",
    "latency_mean_ms",
    "latency_p99_ms",
    "net_value",
    "ambiguous_retries",
    "duplicate_risk_retries",
    "timeouts",
    "throttled",
    "decide_p50_us",
    "decide_p99_us",
]


def run_unit(
    scenario_name: str, run_seed: int, policies: list[str], timing: bool
) -> dict[str, Any]:
    """Run every policy once on one scenario and seed; return metrics per policy."""
    logging.disable(logging.CRITICAL)  # per-payment breaker logs would dominate the runtime
    scenario = SCENARIOS[scenario_name]
    arrivals = scenario.arrival_times(run_seed)
    out: dict[str, Any] = {}
    for name in policies:
        world = scenario.build_world(run_seed)
        policy = build_policy(name, scenario.priority, world, scenario.policy_seed(run_seed))
        result = simulate(world, policy, arrivals, amount=AMOUNT, timing=timing)
        out[name] = run_metrics(scenario, world, result)
    return {"scenario": scenario_name, "seed": run_seed, "metrics": out}


def _mean_ci(values: list[float]) -> dict[str, Any]:
    """Mean with a t-based 95% interval over finite values; counts the non-finite ones."""
    finite = [v for v in values if isinstance(v, int | float) and math.isfinite(v)]
    missing = len(values) - len(finite)
    if not finite:
        return {"mean": None, "ci95": None, "n": 0, "missing": missing}
    mean = statistics.fmean(finite)
    if len(finite) < 2:
        return {"mean": mean, "ci95": None, "n": len(finite), "missing": missing}
    half = float(stats.t.ppf(0.975, len(finite) - 1)) * statistics.stdev(finite)
    half /= len(finite) ** 0.5
    return {"mean": mean, "ci95": [mean - half, mean + half], "n": len(finite), "missing": missing}


def aggregate(rows: list[dict[str, Any]], policies: list[str], seeds: list[int]) -> dict[str, Any]:
    """Summaries per scenario: policy means, regret vs the oracle, paired comparisons."""
    by_scenario: dict[str, dict[int, dict[str, Any]]] = {}
    for row in rows:
        by_scenario.setdefault(row["scenario"], {})[row["seed"]] = row["metrics"]
    out: dict[str, Any] = {}
    for name, per_seed in by_scenario.items():
        scenario = SCENARIOS[name]
        ordered = [per_seed[s] for s in seeds if s in per_seed]
        entry: dict[str, Any] = {
            "group": scenario.group,
            "description": scenario.description,
            "n_seeds": len(ordered),
            "mean_payments": statistics.fmean(r[REFERENCE]["payments"] for r in ordered),
            "policies": {},
            "regret_vs_oracle": {},
            "comparisons": {},
        }
        for pol in policies:
            entry["policies"][pol] = {
                m: _mean_ci([r[pol][m] for r in ordered])
                for m in SUMMARY_METRICS
                if m in ordered[0][pol]
            }
            if pol != REFERENCE:
                entry["regret_vs_oracle"][pol] = summarize_paired(
                    [r[REFERENCE]["psr"] - r[pol]["psr"] for r in ordered]
                )
        for first, second in COMPARISONS:
            if first in policies and second in policies:
                entry["comparisons"][f"{first}_vs_{second}"] = {
                    "first": first,
                    "second": second,
                    **summarize_paired([r[first]["psr"] - r[second]["psr"] for r in ordered]),
                }
        if scenario.components:
            keys = [c.name for c in scenario.components] + ["total"]
            lost: dict[str, Any] = {}
            for pol in policies:
                if pol == REFERENCE:
                    continue
                losses = [annual_lost_gmv(scenario, r[pol], r[REFERENCE]) for r in ordered]
                lost[pol] = {key: _mean_ci([v[key] for v in losses]) for key in keys}
            entry["annual_lost_gmv"] = lost
        out[name] = entry
    return out


def merge_from(summary: dict[str, Any], old: dict[str, Any]) -> list[str]:
    """Add policies that only ``old`` ran to ``summary`` (mutates); return the names added.

    Only valid because runs are deterministic per split, seed and code version, so the check
    comes first: every policy in both must have identical per-scenario results in both, and
    the split, seeds and scenarios must match. Comparisons between two added policies are
    copied; comparisons that need a new policy's per-seed values are only in ``summary``.
    """
    for key in ("split", "n_seeds", "seeds", "reference"):
        if summary[key] != old[key]:
            raise ValueError(f"cannot merge: {key} differs ({summary[key]} vs {old[key]})")
    if set(summary["scenarios"]) != set(old["scenarios"]):
        raise ValueError("cannot merge: the two runs cover different scenarios")
    shared = [p for p in summary["policies"] if p in old["policies"]]
    for name, entry in summary["scenarios"].items():
        prev = old["scenarios"][name]
        for pol in shared:
            if entry["policies"][pol] != prev["policies"][pol] and not _same_except_timing(
                entry["policies"][pol], prev["policies"][pol]
            ):
                raise ValueError(
                    f"cannot merge: {pol} on {name} differs from the old run, so the code "
                    "it depends on changed; rerun every policy"
                )
    added = [p for p in old["policies"] if p not in summary["policies"]]
    for name, entry in summary["scenarios"].items():
        prev = old["scenarios"][name]
        for pol in added:
            entry["policies"][pol] = prev["policies"][pol]
            if pol in prev["regret_vs_oracle"]:
                entry["regret_vs_oracle"][pol] = prev["regret_vs_oracle"][pol]
            if "annual_lost_gmv" in prev and pol in prev["annual_lost_gmv"]:
                entry.setdefault("annual_lost_gmv", {})[pol] = prev["annual_lost_gmv"][pol]
        for key, comp in prev["comparisons"].items():
            if key not in entry["comparisons"] and (
                comp["first"] in added or comp["second"] in added
            ):
                if comp["first"] in summary["policies"] and comp["second"] in summary["policies"]:
                    continue
                entry["comparisons"][key] = comp
    order = list(POLICIES)
    summary["policies"] = sorted(
        [*summary["policies"], *added], key=lambda p: order.index(p) if p in order else len(order)
    )
    summary["merged_from"] = {"policies": added}
    return added


def _same_except_timing(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Equal apart from machine-dependent decision timings."""
    skip = {"decide_p50_us", "decide_p99_us"}
    return {k: v for k, v in a.items() if k not in skip} == {
        k: v for k, v in b.items() if k not in skip
    }


def build_parser() -> argparse.ArgumentParser:
    """Command-line options."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--policies", default="all", help=f"comma list or 'all' ({', '.join(POLICIES)})"
    )
    parser.add_argument("--scenarios", default="", help="comma list (default: every scenario)")
    parser.add_argument("--seeds", type=int, default=20, help="paired seeds per scenario")
    parser.add_argument("--split", choices=["train", "test"], default="test")
    parser.add_argument(
        "--workers", type=int, default=max(1, (os.cpu_count() or 2) - 1), help="processes"
    )
    parser.add_argument("--timing", action="store_true", help="record decide() wall time")
    parser.add_argument("--out-json", default=None, help="write the summary here")
    parser.add_argument(
        "--merge-from",
        default=None,
        help=(
            "an earlier summary JSON on the same split and seeds: copy in the policies only it "
            "ran, after checking that every policy in both reproduced exactly"
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> dict[str, Any]:
    """Run the suite and print a short table; optionally write JSON."""
    args = build_parser().parse_args(argv)
    if args.seeds < 2:
        raise SystemExit("--seeds must be at least 2 (confidence intervals need pairs)")
    policies = list(POLICIES) if args.policies == "all" else args.policies.split(",")
    unknown = sorted(set(policies) - set(POLICIES))
    if unknown:
        raise SystemExit(f"unknown policies: {unknown}")
    if REFERENCE not in policies:
        policies.append(REFERENCE)
    scenarios = select([s for s in args.scenarios.split(",") if s])
    seeds = run_seeds(args.split, args.seeds)
    units = [(sc.name, seed) for sc in scenarios for seed in seeds]

    started = time.perf_counter()
    rows: list[dict[str, Any]] = []
    print(
        f"{len(units)} runs ({len(scenarios)} scenarios x {len(seeds)} seeds), "
        f"{len(policies)} policies each, {args.workers} workers",
        flush=True,
    )
    if args.workers <= 1:
        rows = [run_unit(n, s, policies, args.timing) for n, s in units]
    else:
        with ProcessPoolExecutor(max_workers=args.workers) as pool:
            futures = [pool.submit(run_unit, n, s, policies, args.timing) for n, s in units]
            for i, fut in enumerate(futures, 1):
                rows.append(fut.result())
                if i % max(1, len(units) // 20) == 0:
                    print(f"  {i}/{len(units)} runs", flush=True)
    elapsed = time.perf_counter() - started

    summary: dict[str, Any] = {
        "command": " ".join(
            ["python scripts/suite/run_suite.py", *(argv if argv is not None else sys.argv[1:])]
        ),
        "split": args.split,
        "n_seeds": len(seeds),
        "seeds": [seeds[0], seeds[-1]],
        "policies": policies,
        "reference": REFERENCE,
        "timing": args.timing,
        "scenarios": aggregate(rows, policies, seeds),
    }
    if args.merge_from:
        old = json.loads(Path(args.merge_from).read_text(encoding="utf-8"))
        try:
            added = merge_from(summary, old)
        except ValueError:
            if args.out_json:  # keep hours of runs: write what was run, unmerged
                keep = Path(args.out_json).with_suffix(".unmerged.json")
                keep.write_text(json.dumps(summary, separators=(",", ":")) + "\n", "utf-8")
                print(f"Merge refused; wrote the unmerged summary to {keep}")
            raise
        print(f"Merged {', '.join(added) or 'nothing'} from {args.merge_from}")
    print(format_table(summary))
    print(f"\n{elapsed:.0f} s")
    if args.out_json:
        Path(args.out_json).parent.mkdir(parents=True, exist_ok=True)
        # Compact: the indented 200-seed file is over the repo's 500 KB large-file hook.
        Path(args.out_json).write_text(
            json.dumps(summary, separators=(",", ":")) + "\n", encoding="utf-8"
        )
        print(f"Wrote {args.out_json}")
    return summary


def format_table(summary: dict[str, Any]) -> str:
    """Mean PSR (%) per scenario and policy."""
    pols = summary["policies"]
    width = max(len(n) for n in summary["scenarios"]) + 1
    lines = [f"{'scenario':<{width}}" + "".join(f"{p[:11]:>12}" for p in pols)]
    for name, entry in summary["scenarios"].items():
        cells = []
        for p in pols:
            mean = entry["policies"][p]["psr"]["mean"]
            cells.append(f"{mean * 100:>12.2f}" if mean is not None else f"{'-':>12}")
        lines.append(f"{name:<{width}}" + "".join(cells))
    return "\n".join(lines)


if __name__ == "__main__":
    main()
