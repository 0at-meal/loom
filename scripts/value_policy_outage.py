"""Where do high-value payments go during an outage, with the PID on? (AUDIT F-10)

Two acquirers at 95%. Warmup: 50 payments of 10. Outage on Alpha: 50 payments, every
fifth one high-value (5,000). Recovery: Alpha back up, 50 more payments with the same
mix. Each run is repeated over ``--seeds`` seeds (simulator seed s, router seed s + 1000)
for three policies:

- ``off``: value scaling disabled.
- ``blend``: Thompson samples blended toward the posterior mean, with no dispatch
  protection (``protect_lambda=1.0``). This is the Phase 8 behaviour the audit measured.
- ``protect``: the default policy: payments with lambda(V) >= 0.95 go to the acquirer with
  the highest posterior mean and skip the PID and the allocation draw.

The ``audit`` configuration is the audit's harness (per-observation decay 0.95, default
PID gains); ``served`` uses the served router's wall-clock decay on a virtual 15 TPS clock.

    python scripts/value_policy_outage.py
    python scripts/value_policy_outage.py --config served --seeds 100
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import statistics
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from acquirer_sim.app import create_app
from acquirer_sim.models import AuthorizeRequest, LatencyConfig, OutageToggleRequest
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig
from router_core.router import BanditRouter
from router_core.state import DEFAULT_HALF_LIFE_SEC, AcquirerStateConfig
from router_core.value_policy import ValueScaledExplorationConfig

ACQUIRERS = ("acquirer_alpha", "acquirer_beta")
DEAD = "acquirer_alpha"
HIGH, LOW = 5000.0, 10.0
TPS = 15.0
Actuation = Literal["deficit", "stochastic"]
ACTUATIONS: tuple[Actuation, ...] = ("deficit", "stochastic")
POLICIES: dict[str, ValueScaledExplorationConfig] = {
    "off": ValueScaledExplorationConfig(enabled=False),
    "blend": ValueScaledExplorationConfig(enabled=True, tau=100.0, protect_lambda=1.0),
    "protect": ValueScaledExplorationConfig(enabled=True, tau=100.0),
}
CONFIGS: dict[str, dict[str, Any]] = {
    "audit": {"state": {"decay_factor": 0.95}},
    "served": {"state": {"half_life_sec": DEFAULT_HALF_LIFE_SEC}},
}


@dataclass
class RunStats:
    """Counts from one seeded run."""

    high_to_dead: int  # of 10 high-value outage payments
    low_to_dead: int  # of 40 low-value outage payments
    outage_ok: int  # of 50
    recovery_ok: int  # of 50
    high_to_alpha_recovery: int  # of 10 high-value recovery payments
    low_to_alpha_recovery: int  # of 40 low-value recovery payments


async def run_once(seed: int, policy: str, actuation: Actuation, config: str) -> RunStats:
    """Warmup, outage and recovery for one seed; return the run's counts."""
    sim = create_app(
        default_acquirers=list(ACQUIRERS),
        default_base_rate=0.95,
        default_latency=LatencyConfig(base_ms=0.0, jitter_ms=0.0),
        seed=seed,
    )
    now = [0.0]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=sim), base_url="http://t") as c:
        state_config = AcquirerStateConfig(**CONFIGS[config]["state"])
        routes = [
            AcquirerRouteConfig(acquirer_id=a, base_url="http://t", state_config=state_config)
            for a in ACQUIRERS
        ]
        router = BanditRouter(
            RouterConfig(
                routes=routes,
                pid_config=PIDConfig(actuation_mode=actuation),
                seed=seed + 1000,
                value_scaled_config=POLICIES[policy],
            ),
            http_client=c,
            clock=lambda: now[0],
        )

        async def pay(tx_id: str, amount: float) -> tuple[str, bool]:
            now[0] += 1.0 / TPS
            res = await router.route(AuthorizeRequest(transaction_id=tx_id, amount=amount))
            return res.selected_acquirer, res.authorized

        async def outage(active: bool) -> None:
            await c.post(
                f"/acquirers/{DEAD}/admin/outage",
                json=OutageToggleRequest(active=active).model_dump(),
            )

        for i in range(50):
            await pay(f"w{i}", LOW)
        await outage(True)
        high_dead = low_dead = outage_ok = 0
        for i in range(50):
            amount = HIGH if i % 5 == 0 else LOW
            acquirer, ok = await pay(f"o{i}", amount)
            outage_ok += ok
            if acquirer == DEAD:
                high_dead += amount == HIGH
                low_dead += amount == LOW
        await outage(False)
        high_alpha = low_alpha = recovery_ok = 0
        for i in range(50):
            amount = HIGH if i % 5 == 0 else LOW
            acquirer, ok = await pay(f"r{i}", amount)
            recovery_ok += ok
            if acquirer == DEAD:
                high_alpha += amount == HIGH
                low_alpha += amount == LOW
    return RunStats(high_dead, low_dead, outage_ok, recovery_ok, high_alpha, low_alpha)


def collect(seeds: int, policy: str, actuation: Actuation, config: str) -> list[RunStats]:
    """Run every seed for one policy and actuation mode."""
    return [asyncio.run(run_once(s, policy, actuation, config)) for s in range(seeds)]


def summarize(runs: list[RunStats]) -> dict[str, float]:
    """Means across seeds, plus the standard deviation of the high-value count."""
    n = len(runs)
    high = [r.high_to_dead for r in runs]
    return {
        "high_to_dead": statistics.mean(high),
        "high_to_dead_sd": statistics.stdev(high) if n > 1 else 0.0,
        "high_dead_rate": statistics.mean(high) / 10,
        "low_dead_rate": statistics.mean(r.low_to_dead for r in runs) / 40,
        "outage_psr": statistics.mean(r.outage_ok for r in runs) / 50 * 100,
        "recovery_psr": statistics.mean(r.recovery_ok for r in runs) / 50 * 100,
        "high_alpha_recovery": statistics.mean(r.high_to_alpha_recovery for r in runs),
        "low_alpha_recovery": statistics.mean(r.low_to_alpha_recovery for r in runs),
    }


def main() -> None:
    """Print the outage and recovery table for each actuation mode and policy."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--seeds", type=int, default=30)
    parser.add_argument("--config", choices=sorted(CONFIGS), default="audit")
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    print(f"config={args.config}, {args.seeds} seeds, high-value = {HIGH:g}, tau = 100")
    print(
        f"{'actuation':10s} {'policy':8s} {'HV->dead /10':>14s} {'HV rate':>8s} "
        f"{'LV rate':>8s} {'outage PSR':>11s} {'recov PSR':>10s} "
        f"{'HV->alpha rec /10':>18s} {'LV->alpha rec /40':>18s}"
    )
    for actuation in ACTUATIONS:
        for policy in POLICIES:
            s = summarize(collect(args.seeds, policy, actuation, args.config))
            print(
                f"{actuation:10s} {policy:8s} "
                f"{s['high_to_dead']:6.2f} (sd {s['high_to_dead_sd']:.2f}) "
                f"{s['high_dead_rate']:8.3f} {s['low_dead_rate']:8.3f} "
                f"{s['outage_psr']:10.2f}% {s['recovery_psr']:9.2f}% "
                f"{s['high_alpha_recovery']:18.2f} {s['low_alpha_recovery']:18.2f}"
            )


if __name__ == "__main__":
    main()
