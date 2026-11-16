"""Golden decision traces for the Stage 0 policy extraction (docs/001-router_enhancements.md).

The traces in ``data/loom_golden_trace.json`` were recorded from the router before its decision
logic moved into ``router_core.policy.LoomPolicy`` and the static breaker's into
``baseline_router.policy.StaticBreakerPolicy``. ``test_policy_golden.py`` replays the same runs
and requires every decision, outcome and float to match exactly.

Regenerate only on purpose (it overwrites the reference)::

    python -m tests.router_core.loom_golden --write
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

import httpx

from acquirer_sim.app import create_app
from acquirer_sim.models import AuthorizeRequest, LatencyConfig, OutageBehavior, OutageToggleRequest
from baseline_router.models import BaselineRouterConfig, FailoverPolicyConfig
from baseline_router.router import StaticBaselineRouter
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig
from router_core.value_policy import ValueScaledExplorationConfig

GOLDEN_PATH = Path(__file__).parent / "data" / "loom_golden_trace.json"
SIM_SEED = 42
LOOM_SEED = 777
TPS = 15.0

# name -> (acquirer rates, router kind, outage behaviour or gray rate, extra options)
CASES: dict[str, dict[str, Any]] = {
    "loom_bench_hard_outage": {
        "rates": {"acquirer_alpha": 0.95, "acquirer_beta": 0.94},
        "router": "loom",
        "pid": {"actuation_mode": "deficit"},
        "half_life": 0.9,
        "incident": ("outage", OutageBehavior.RETURN_DECLINE),
    },
    "raw_bandit_hard_outage": {
        "rates": {"acquirer_alpha": 0.95, "acquirer_beta": 0.94},
        "router": "loom",
        "pid": None,
        "half_life": 0.9,
        "incident": ("outage", OutageBehavior.RETURN_DECLINE),
    },
    "loom_bench_gray": {
        "rates": {"acquirer_alpha": 0.95, "acquirer_beta": 0.94},
        "router": "loom",
        "pid": {"actuation_mode": "deficit"},
        "half_life": 0.9,
        "incident": ("gray", 0.60),
    },
    "loom_served_503_three_acquirers": {
        "rates": {"acquirer_alpha": 0.95, "acquirer_beta": 0.90, "acquirer_gamma": 0.85},
        "router": "loom",
        "pid": {},
        "half_life": None,
        "incident": ("outage", OutageBehavior.HTTP_503),
    },
    "loom_value_scaled_deficit": {
        "rates": {"acquirer_alpha": 0.95, "acquirer_beta": 0.94},
        "router": "loom",
        "pid": {"actuation_mode": "deficit"},
        "half_life": 0.9,
        "incident": ("outage", OutageBehavior.RETURN_DECLINE),
        "value_scaled": True,
    },
    "loom_value_scaled_stochastic": {
        "rates": {"acquirer_alpha": 0.95, "acquirer_beta": 0.90, "acquirer_gamma": 0.85},
        "router": "loom",
        "pid": {},
        "half_life": None,
        "incident": ("outage", OutageBehavior.HTTP_503),
        "value_scaled": True,
    },
    "static_m3_hard_outage": {
        "rates": {"acquirer_alpha": 0.95, "acquirer_beta": 0.94},
        "router": "static",
        "threshold_m": 3,
        "incident": ("outage", OutageBehavior.RETURN_DECLINE),
    },
    "static_m1_hard_outage": {
        "rates": {"acquirer_alpha": 0.95, "acquirer_beta": 0.94},
        "router": "static",
        "threshold_m": 1,
        "incident": ("outage", OutageBehavior.RETURN_DECLINE),
    },
}

# Amounts cycle so value-scaled runs see unprotected, blended and protected payments.
AMOUNTS = [10.0, 50.0, 400.0, 1000.0, 25.0]
STAGE_LENGTH = 50


def _routes(rates: dict[str, float], half_life: float | None) -> list[AcquirerRouteConfig]:
    """Route configs for every acquirer, with the given technical half-life."""
    state = (
        AcquirerStateConfig(half_life_sec=half_life)
        if half_life is not None
        else AcquirerStateConfig()
    )
    return [
        AcquirerRouteConfig(acquirer_id=aid, base_url="http://testserver", state_config=state)
        for aid in rates
    ]


def _floats(d: dict[str, float] | None) -> dict[str, float] | None:
    """Copy a float mapping in key order (JSON keeps doubles exactly)."""
    return None if d is None else {k: float(d[k]) for k in sorted(d)}


async def run_case(name: str) -> list[dict[str, Any]]:
    """Run one golden case through the HTTP path and return its per-payment trace."""
    case = CASES[name]
    rates: dict[str, float] = case["rates"]
    first = next(iter(rates))
    sim_app = create_app(
        default_acquirers=list(rates),
        default_base_rate=0.95,
        default_latency=LatencyConfig(base_ms=0.0, jitter_ms=0.0),
        seed=SIM_SEED,
    )
    for aid, rate in rates.items():
        sim_app.state.registry.get(aid).set_success_rate(rate)

    now = [0.0]
    trace: list[dict[str, Any]] = []
    transport = httpx.ASGITransport(app=sim_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        router: Any
        if case["router"] == "static":
            router = StaticBaselineRouter(
                config=BaselineRouterConfig(
                    routes=_routes(rates, 0.9),
                    priority_order=list(rates),
                    failover_policy=FailoverPolicyConfig(
                        consecutive_failure_threshold=case["threshold_m"],
                        cooldown_transactions=30,
                        failback_mode="probe",
                    ),
                ),
                http_client=client,
            )
        else:
            pid = case["pid"]
            router = BanditRouter(
                config=RouterConfig(
                    routes=_routes(rates, case["half_life"]),
                    pid_config=PIDConfig(**pid) if pid is not None else None,
                    value_scaled_config=(
                        ValueScaledExplorationConfig(enabled=True, tau=100.0)
                        if case.get("value_scaled")
                        else None
                    ),
                    seed=LOOM_SEED,
                ),
                http_client=client,
                clock=lambda: now[0],
            )

        kind, arg = case["incident"]

        async def incident(active: bool) -> None:
            if kind == "outage":
                await client.post(
                    f"/acquirers/{first}/admin/outage",
                    json=OutageToggleRequest(active=active, behavior=arg).model_dump(mode="json"),
                )
            else:
                sim_app.state.registry.get(first).set_success_rate(arg if active else rates[first])

        index = 0
        for stage in ("warmup", "incident", "recovery"):
            if stage == "incident":
                await incident(True)
            elif stage == "recovery":
                await incident(False)
            for _ in range(STAGE_LENGTH):
                index += 1
                now[0] += 1.0 / TPS
                req = AuthorizeRequest(
                    transaction_id=f"tx_{name}_{index}", amount=AMOUNTS[index % len(AMOUNTS)]
                )
                res = await router.route(req)
                # Every float that the decision produced, by exact repr, digested to keep
                # the reference file small while still demanding bit-for-bit equality.
                floats = repr(
                    (
                        _floats(res.thompson_samples),
                        _floats(res.adjusted_samples),
                        res.exploration_shrinkage,
                        _floats(res.smoothed_allocation),
                        _floats(res.target_allocation),
                        res.state_snapshot.alpha,
                        res.state_snapshot.beta,
                    )
                )
                trace.append(
                    {
                        "i": index,
                        "selected": res.selected_acquirer,
                        "status": res.status,
                        "authorized": res.authorized,
                        "outcome": None if res.outcome is None else str(res.outcome),
                        "protected": res.value_protected,
                        "floats": hashlib.sha256(floats.encode()).hexdigest()[:24],
                    }
                )
    return trace


async def build_traces() -> dict[str, list[dict[str, Any]]]:
    """Run every golden case."""
    return {name: await run_case(name) for name in CASES}


def main() -> None:
    """Write the golden traces when asked explicitly."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="overwrite the golden file")
    args = parser.parse_args()
    traces = asyncio.run(build_traces())
    if not args.write:
        print(json.dumps({k: len(v) for k, v in traces.items()}))
        return
    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    # One payment per line: small enough to commit, and a diff points at the payment.
    cases = [
        json.dumps(name)
        + ": [\n"
        + ",\n".join(json.dumps(rec, separators=(",", ":")) for rec in trace)
        + "\n]"
        for name, trace in traces.items()
    ]
    GOLDEN_PATH.write_text("{\n" + ",\n".join(cases) + "\n}\n", encoding="utf-8")
    print(f"Wrote {GOLDEN_PATH}")


if __name__ == "__main__":
    main()
