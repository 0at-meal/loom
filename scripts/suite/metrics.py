"""Per-run metrics for the scenario suite (definitions in the Stage 0 decisions-log entry)."""

from __future__ import annotations

import math
from typing import Any

from acquirer_sim.world import World
from scripts.suite.des import Payment, RunResult
from scripts.suite.scenarios import Scenario, Window

# Divert: the affected acquirer's share of first attempts in the following window falls below
# this. Restore: it climbs back to at least this fraction of its pre-incident share.
DIVERT_SHARE = 0.20
RESTORE_FRACTION = 0.50
MIN_WINDOW_SEC = 2.0
MIN_WINDOW_PAYMENTS = 20


def _percentile(values: list[float], q: float) -> float:
    """Nearest-rank percentile (q in [0, 100]); nan for an empty list."""
    if not values:
        return math.nan
    ordered = sorted(values)
    k = max(0, min(len(ordered) - 1, math.ceil(q / 100.0 * len(ordered)) - 1))
    return ordered[k]


def _share(decisions: list[tuple[float, str]], acquirer: str, start: float, end: float) -> float:
    picked = [a for t, a in decisions if start <= t < end]
    return math.nan if not picked else sum(1 for a in picked if a == acquirer) / len(picked)


def _first_crossing(
    decisions: list[tuple[float, str]],
    acquirer: str,
    from_time: float,
    window: float,
    below: float | None = None,
    at_least: float | None = None,
) -> float | None:
    """Seconds after ``from_time`` until the first decision whose following window has the
    acquirer's share below ``below`` (or at least ``at_least``); None if it never happens."""
    times = [t for t, _ in decisions]
    hits = [1 if a == acquirer else 0 for _, a in decisions]
    n = len(decisions)
    count = 0
    # Sliding window [times[i], times[i] + window) over decisions at or after from_time.
    i0 = next((i for i, t in enumerate(times) if t >= from_time), n)
    j = i0
    for i in range(i0, n):
        if i > i0:
            count -= hits[i - 1]
        while j < n and times[j] < times[i] + window:
            count += hits[j]
            j += 1
        share = count / (j - i)
        if (below is not None and share < below) or (at_least is not None and share >= at_least):
            return times[i] - from_time
    return None


def run_metrics(scenario: Scenario, world: World, run: RunResult) -> dict[str, Any]:
    """Scalar metrics of one policy's run on one scenario."""
    payments = run.payments
    n = len(payments)
    authorized = sum(1 for p in payments if p.authorized)
    attempts = [a for p in payments for a in p.attempts]
    fees = 0.0
    value = 0.0
    for p in payments:
        for a in p.attempts:
            prof = world.profile(a.acquirer_id)
            if a.processed:
                fees += prof.fee_fixed
            if p.authorized and a is p.attempts[-1]:
                value += p.amount
                fees += prof.fee_rate * p.amount
    dead_attempts = sum(
        1 for a in attempts if world.down_mode(a.acquirer_id, a.decided_at) is not None
    )
    dead_failed = sum(
        1
        for p in payments
        if not p.authorized
        and p.attempts
        and world.down_mode(p.attempts[-1].acquirer_id, p.attempts[-1].decided_at) is not None
    )
    latencies = [(p.completed_at - p.arrival) * 1000.0 for p in payments]
    out: dict[str, Any] = {
        "payments": n,
        "authorized": authorized,
        "psr": authorized / n if n else math.nan,
        "attempts": len(attempts),
        "added_hops": len(attempts) / n - 1.0 if n else math.nan,
        "dead_route_attempts": dead_attempts,
        "dead_route_failed_payments": dead_failed,
        "latency_mean_ms": sum(latencies) / n if n else math.nan,
        "latency_p99_ms": _percentile(latencies, 99.0),
        "net_value": value - fees,
        "ambiguous_retries": run.ambiguous_retries,
        "duplicate_risk_retries": run.duplicate_risk_retries,
        "timeouts": sum(1 for a in attempts if a.kind == "TIMEOUT"),
        "throttled": sum(1 for a in attempts if a.kind == "HTTP_429"),
    }
    out.update(_incident_metrics(scenario, payments))
    out.update(_component_counts(scenario, payments))
    if run.decide_seconds:
        out["decide_p50_us"] = _percentile(run.decide_seconds, 50.0) * 1e6
        out["decide_p99_us"] = _percentile(run.decide_seconds, 99.0) * 1e6
    return out


def _incident_metrics(scenario: Scenario, payments: list[Payment]) -> dict[str, Any]:
    inc: Window | None = scenario.incident
    if inc is None:
        return {}
    decisions = [(p.attempts[0].decided_at, p.attempts[0].acquirer_id) for p in payments]
    in_window = [p for p in payments if inc.start <= p.arrival < inc.end]
    out: dict[str, Any] = {
        "incident_psr": (
            sum(1 for p in in_window if p.authorized) / len(in_window) if in_window else math.nan
        ),
    }
    watched = inc.acquirer if inc.acquirer is not None else scenario.priority[0]
    span = inc.end - inc.start
    before = _share(decisions, watched, max(0.0, inc.start - span), inc.start)
    during = _share(decisions, watched, inc.start, inc.end)
    out["share_before"] = before
    out["share_during"] = during
    out["share_shift_pp"] = (during - before) * 100.0
    if inc.acquirer is None:
        return out
    window = max(MIN_WINDOW_SEC, MIN_WINDOW_PAYMENTS / scenario.arrivals.mean_tps)
    divert = _first_crossing(decisions, watched, inc.start, window, below=DIVERT_SHARE)
    restore = (
        _first_crossing(decisions, watched, inc.end, window, at_least=RESTORE_FRACTION * before)
        if before > 0.0
        else None
    )
    out["seconds_to_divert"] = math.nan if divert is None else divert
    out["seconds_to_restore"] = math.nan if restore is None else restore
    return out


def _component_counts(scenario: Scenario, payments: list[Payment]) -> dict[str, Any]:
    """Payments, authorizations and incident-period payments per soak component."""
    out: dict[str, Any] = {}
    for comp in scenario.components:
        n = ok = incident_n = 0
        for p in payments:
            for w in comp.windows:
                if w.start <= p.arrival < w.end:
                    n += 1
                    ok += 1 if p.authorized else 0
                    if p.arrival < w.start + comp.incident_seconds:
                        incident_n += 1
                    break
        out[f"component:{comp.name}"] = {"payments": n, "authorized": ok, "incident": incident_n}
    return out


# Audit revenue model (docs/AUDIT.md section 6), base case.
REVENUE_GMV = 1e9
REVENUE_RECOVERY = 0.5
HOURS_PER_YEAR = 8760.0


def annual_lost_gmv(
    scenario: Scenario, policy_row: dict[str, Any], oracle_row: dict[str, Any]
) -> dict[str, float]:
    """Annual lost GMV vs the oracle, per component and in total (audit section 6 model).

    For each component, the payments the policy lost against the oracle in the component's
    windows (incident plus a 10 min tail) are divided by the payments that arrived during the
    incident itself, giving a loss per incident-time payment. That rate is applied to the
    component's share of a year's traffic: GMV x hours / 8760, times (1 - shopper recovery).
    """
    out: dict[str, float] = {}
    total = 0.0
    for comp in scenario.components:
        mine = policy_row[f"component:{comp.name}"]
        ref = oracle_row[f"component:{comp.name}"]
        base = mine["incident"]
        if base <= 0:
            continue
        loss_rate = (ref["authorized"] - mine["authorized"]) / base
        lost = REVENUE_GMV * comp.annual_hours / HOURS_PER_YEAR * loss_rate * (1 - REVENUE_RECOVERY)
        out[comp.name] = lost
        total += lost
    out["total"] = total
    return out
