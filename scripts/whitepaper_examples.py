"""Reproduce every worked example and figure quoted in docs/WHITEPAPER_L1.md.

Run from the repository root:

    python scripts/whitepaper_examples.py

Each block is labelled with the whitepaper section it feeds. The benchmark figures in
Section 8 call ``scripts/compare_psr.py`` with in-memory ledgers, so nothing is written to disk.
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Any

import numpy as np

from router_core.pid import PIDConfig, PIDState, calculate_pid_step, project_to_bounded_simplex
from router_core.state import DEFAULT_HALF_LIFE_SEC, AcquirerState, AcquirerStateConfig
from scripts.compare_psr import execute_scenario

BETA_DRAW_SEED = 7
PAIRS_SEED = 7
DIE_SEED = 7


def section_4_thompson() -> list[str]:
    """Section 4.4: five Thompson draws and the long-run win share of the weaker arm."""
    out = ["[4.4] Thompson draws, A ~ Beta(20,2), B ~ Beta(3,2), numpy default_rng(7)"]
    rng = np.random.default_rng(BETA_DRAW_SEED)
    a = [float(rng.beta(20, 2)) for _ in range(5)]
    b = [float(rng.beta(3, 2)) for _ in range(5)]
    for i, (x, y) in enumerate(zip(a, b, strict=True), start=1):
        out.append(f"  draw {i}: A={x:.3f} B={y:.3f} winner={'A' if x > y else 'B'}")
    rng = np.random.default_rng(PAIRS_SEED)
    n = 100_000
    b_wins = int(np.sum(rng.beta(3, 2, n) > rng.beta(20, 2, n)))
    out.append(f"  B wins {b_wins} of {n:,} pairs = {b_wins / n:.1%}")
    return out


def section_5_decay() -> list[str]:
    """Section 5: offset-decay updates, memory length and the outage example."""
    out = ["[5.2] Per-observation offset decay from Beta(1,1), gamma=0.98, outcomes S S S F S"]
    state = AcquirerState("example", AcquirerStateConfig(decay_factor=0.98))
    for outcome in (True, True, True, False, True):
        snap = state.record_outcome(outcome, timestamp=0.0)
        out.append(
            f"  {'S' if outcome else 'F'}: alpha={snap.alpha:.4f} beta={snap.beta:.4f} "
            f"mean={snap.expected_success_rate:.3f}"
        )
    out.append("[5.3] Memory length and maximum confidence")
    for gamma in (0.98, 0.95):
        half = math.log(0.5) / math.log(gamma)
        out.append(
            f"  gamma={gamma}: half-life {half:.1f} updates, "
            f"alpha ceiling {1 + 1 / (1 - gamma):.0f}"
        )
    for gamma, n_fail in ((0.95, (1, 3, 5, 10)), (0.98, (10,))):
        state = AcquirerState("example", AcquirerStateConfig(decay_factor=gamma))
        for _ in range(200):
            snap = state.record_outcome(True, timestamp=0.0)
        line = (
            f"  gamma={gamma}: after 200 successes Beta({snap.alpha:.1f},{snap.beta:.1f}) "
            f"mean={snap.expected_success_rate:.3f};"
        )
        done = 0
        for target in n_fail:
            while done < target:
                snap = state.record_outcome(False, timestamp=0.0)
                done += 1
            line += f" after {target} failures {snap.expected_success_rate:.3f}"
        out.append(line)
    memory = 1 / (1 - 0.98)
    out.append(
        f"  memory 1/(1-0.98) = {memory:.0f} observations; at 15 TPS that is "
        f"{memory / (15 * 0.97):.1f} s for an acquirer at 97% of traffic and "
        f"{memory / (15 * 0.03):.0f} s for one at the 3% floor"
    )
    out.append(f"[5.3] Wall-clock decay (default), half-life {DEFAULT_HALF_LIFE_SEC} s")
    for share in (1.0, 0.97, 0.03):
        rate = 15 * share
        out.append(
            f"  arm at {share:.0%} of 15 TPS: steady-state memory "
            f"{rate * DEFAULT_HALF_LIFE_SEC / math.log(2):.1f} observations"
        )
    cfg = AcquirerStateConfig(half_life_sec=DEFAULT_HALF_LIFE_SEC)
    state = AcquirerState("example", cfg, initial_timestamp=0.0)
    t = 0.0
    for _ in range(200):
        t += 1 / 15
        snap = state.record_outcome(True, timestamp=t)
    line = (
        f"  all 15 TPS: after 200 successes Beta({snap.alpha:.1f},{snap.beta:.1f}) "
        f"mean={snap.expected_success_rate:.3f};"
    )
    for target in (1, 3, 5, 10):
        while snap.failure_count < target:
            t += 1 / 15
            snap = state.record_outcome(False, timestamp=t)
        line += f" after {target} failures {snap.expected_success_rate:.3f}"
    out.append(line)
    idle = AcquirerState("example", cfg, initial_timestamp=0.0)
    for _ in range(20):
        idle.record_outcome(False, timestamp=0.0)
    for secs in (0.0, DEFAULT_HALF_LIFE_SEC, 10.0, 30.0):
        snap = idle.get_state(now=secs)
        out.append(
            f"  idle arm after 20 failures, {secs:.1f} s later: "
            f"Beta({snap.alpha:.2f},{snap.beta:.2f}) mean={snap.expected_success_rate:.3f}"
        )
    state = AcquirerState("example", AcquirerStateConfig(decay_factor=0.98))
    snap = state.record_outcome(False, timestamp=0.0)
    out.append(f"[5.4] Per-observation health after one failure from 1.0: {snap.health_score:.2f}")
    return out


def section_6_pid() -> list[str]:
    """Section 6: PID step table, projection examples, deficit and stochastic schedulers."""
    out = ["[6.3] PID steps, two acquirers from 50/50, target all-to-A, default gains"]
    cfg = PIDConfig()
    w = {"A": 0.5, "B": 0.5}
    st = PIDState(
        accumulated_error={"A": 0.0, "B": 0.0},
        previous_error={"A": 0.0, "B": 0.0},
        previous_allocation=dict(w),
        filtered_derivative={"A": 0.0, "B": 0.0},
    )
    for step in range(1, 6):
        res = calculate_pid_step({"A": 1.0, "B": 0.0}, w, st, cfg)
        d = res.diagnostics
        w, st = res.smoothed_allocation, res.next_state
        out.append(
            f"  step {step}: e_A={d.error['A']:+.4f} P={d.p_term['A']:+.4f} "
            f"I={d.i_term['A']:+.4f} D={d.d_term['A']:+.4f} w_A={w['A']:.4f}"
        )
    out.append("[6.4] Projection onto the simplex with a 0.03 floor")
    for vec in ((1.05, -0.05), (0.98, 0.00, 0.10), (0.5, 0.3)):
        proj = project_to_bounded_simplex({str(i): v for i, v in enumerate(vec)}, min_floor=0.03)
        out.append(f"  {vec} -> {tuple(round(v, 3) for v in proj.values())}")
    out.append("[6.5] Deficit scheduler, shares (0.7, 0.3), ten payments")
    owed = {"A": 0.0, "B": 0.0}
    sent = {"A": 0, "B": 0}
    seq = []
    for _ in range(10):
        for k, share in (("A", 0.7), ("B", 0.3)):
            owed[k] += share
        pick = max(sorted(owed), key=lambda k: (owed[k] - sent[k], k))
        sent[pick] += 1
        seq.append(pick)
    out.append(f"  sequence {' '.join(seq)}; counts A={sent['A']} B={sent['B']}")
    rng = np.random.default_rng(DIE_SEED)
    counts = [int(np.sum(rng.random(10) < 0.7)) for _ in range(8)]
    out.append(f"  eight runs of ten weighted draws, numpy default_rng(7): A = {counts}")
    return out


def section_7_value_policy() -> list[str]:
    """Section 7.1: value-scaled shrinkage lambda = 1 - exp(-V / tau), tau = 100."""
    lams = ", ".join(f"V={v}: {1 - math.exp(-v / 100):.3f}" for v in (10, 100, 250, 1000))
    return [f"[7.1] Value policy lambda (tau=100): {lams}"]


async def section_8_benchmark() -> list[str]:
    """Section 8.2: seed-42 traces from scripts/compare_psr.py (in-memory ledgers)."""
    out = ["[8.2] compare_psr.py scenario, simulator seed 42, Loom seed 777"]
    runs: dict[str, dict[str, Any]] = {}
    for name, kind, m, gray in (
        ("static M=3", "baseline", 3, None),
        ("static M=1", "baseline", 1, None),
        ("static M=5", "baseline", 5, None),
        ("static M=3, gray 60%", "baseline", 3, 0.60),
        ("Loom raw bandit", "raw", 3, None),
        ("Loom with PID", "loom", 3, None),
        ("Loom with PID, gray 60%", "loom", 3, 0.60),
    ):
        res = await execute_scenario(kind, ":memory:", threshold_m=m, gray_rate=gray)
        runs[name] = res
        g = res["global_metrics"]
        out.append(
            f"  {name:<24} PSR {g['psr'] * 100:6.2f}% ({g['authorized_count']}/150) "
            f"outage {res['outage_psr'] * 100:5.1f}% fail_alpha {res['outage_failures_alpha']:2d} "
            f"dw_max {res['max_step_delta'] * 100:6.2f}% outage_flips {res['outage_flips']:2d} "
            f"recovery_alpha_tx {res['recovery_txs_alpha']}"
        )
    trace = runs["Loom with PID"]["alpha_allocation"]
    out.append(
        "  Loom with PID, Alpha share at Tx 50-62: " + ", ".join(f"{x:.2f}" for x in trace[49:62])
    )
    end = trace[-1]
    out.append(f"  Loom with PID, Alpha share at Tx 150: {end:.2f}")
    out.append(f"  One authorization out of 150 = {100 / 150:.2f} pp of PSR")
    return out


async def main() -> None:
    logging.getLogger("loom.baseline_router").setLevel(logging.ERROR)
    lines = [
        *section_4_thompson(),
        *section_5_decay(),
        *section_6_pid(),
        *section_7_value_policy(),
        *(await section_8_benchmark()),
    ]
    print("\n".join(lines))


if __name__ == "__main__":
    asyncio.run(main())
