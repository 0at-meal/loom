"""Policies the suite runs, built per scenario run, plus the clairvoyant oracle.

Every factory takes the scenario's acquirer order (its priority order: primary first), the
world, and one integer seed shared by all policies of a run.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

import numpy as np

from acquirer_sim.world import World
from baseline_router.models import FailoverPolicyConfig
from baseline_router.policies import (
    CompetentBreakerPolicy,
    SlidingWindowUCBPolicy,
    SRWindowPolicy,
    StaticBreakerPolicy,
)
from router_core.gate import GateConfig
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig
from router_core.policy import (
    Decision,
    DecisionContext,
    LoomPolicy,
    Observation,
    RoutingPolicy,
    one_hot,
)
from router_core.server import (
    FAIL_FAST_FLOOR_RATE_PER_SEC,
    apply_long_memory,
    apply_warm_priors,
)
from router_core.state import DEFAULT_HALF_LIFE_SEC, AcquirerStateConfig

# compare_psr.py's benchmark configuration (kept in step by tests/scripts/test_suite.py).
BENCH_HALF_LIFE_SEC = 0.9


class OraclePolicy:
    """Clairvoyant reference: knows each acquirer's true success probability at every moment.

    Sends each payment to the acquirer most likely to authorize it now (given its issuer
    segment) and, after a failure that was not processed, retries once on the next best. It
    does not know capacity limits or in-flight load, so under capacity limits it can overload
    an acquirer and is not an upper bound there.
    """

    name = "oracle"

    def __init__(self, world: World, acquirer_ids: Sequence[str]) -> None:
        """Bind the oracle to the world it can see into."""
        self._world = world
        self._ids = tuple(acquirer_ids)

    @property
    def acquirer_ids(self) -> tuple[str, ...]:
        """Acquirers in priority order (ties go to the earlier one)."""
        return self._ids

    @property
    def max_attempts(self) -> int:
        """One retry, like the competent breaker."""
        return 2

    def decide(
        self, ctx: DecisionContext, now: float, exclude: frozenset[str] = frozenset()
    ) -> Decision:
        """Best true success probability among the acquirers not excluded."""
        segment = self._world.segment_of(ctx.transaction_id)
        rest = [a for a in self._ids if a not in exclude]
        best = max(
            rest,
            key=lambda a: (self._world.expected_success(a, segment, now), -self._ids.index(a)),
        )
        return Decision(
            transaction_id=ctx.transaction_id,
            acquirer_id=best,
            propensities=one_hot(self._ids, best),
            reason="oracle",
            decided_at=now,
            attempt=ctx.attempt,
        )

    def feedback(self, decision: Decision, observation: Observation, now: float) -> None:
        """The oracle learns nothing."""

    def should_retry(self, decision: Decision, observation: Observation) -> bool:
        """Retry once after a failure that certainly was not processed."""
        return decision.attempt == 1 and not observation.authorized and observation.safe_to_retry


def _routes(ids: Sequence[str], half_life_sec: float) -> list[AcquirerRouteConfig]:
    state = AcquirerStateConfig(half_life_sec=half_life_sec)
    return [
        AcquirerRouteConfig(acquirer_id=a, base_url="http://suite.invalid", state_config=state)
        for a in ids
    ]


def served_router_config(ids: Sequence[str], seed: int) -> RouterConfig:
    """The served router's defaults (``python -m router_core.server``, no flags, no env)."""
    return RouterConfig(
        routes=_routes(ids, DEFAULT_HALF_LIFE_SEC),
        pid_config=PIDConfig(kp=0.12, ki=0.005, kd=0.25, min_allocation=0.03),
        seed=seed,
    )


def bench_router_config(ids: Sequence[str], seed: int) -> RouterConfig:
    """``scripts/compare_psr.py``'s Loom configuration (deficit actuation, 0.9 s half-life)."""
    return RouterConfig(
        routes=_routes(ids, BENCH_HALF_LIFE_SEC),
        pid_config=PIDConfig(
            kp=0.12,
            ki=0.005,
            kd=0.25,
            integral_max=1.0,
            min_allocation=0.03,
            actuation_mode="deficit",
        ),
        seed=seed,
    )


def failfast_router_config(ids: Sequence[str], seed: int) -> RouterConfig:
    """Served defaults plus Stage 1 (``python -m router_core.server --fail-fast``)."""
    served = served_router_config(ids, seed)
    assert served.pid_config is not None
    return served.model_copy(
        update={
            "pid_config": served.pid_config.model_copy(
                update={"floor_rate_per_sec": FAIL_FAST_FLOOR_RATE_PER_SEC}
            ),
            "gate_config": GateConfig(),
            "cascade": True,
        }
    )


def longmem_router_config(ids: Sequence[str], seed: int) -> RouterConfig:
    """Fail-fast plus Stage 2 (``python -m router_core.server --fail-fast --long-memory``)."""
    return apply_long_memory(failfast_router_config(ids, seed))


# Warm start for 'loom_longmem_warm': a simulated previous day's ledger holding this many
# issuer outcomes per acquirer (half a day at 1 TPS), spread over 24 h.
WARM_OBSERVATIONS = 43_200
WARM_WINDOW_SEC = 86_400.0


def previous_day_priors(world: World, ids: Sequence[str], seed: int) -> dict[str, dict[str, float]]:
    """What ``export-priors`` would give after a steady previous day in this world.

    Per acquirer, Binomial(WARM_OBSERVATIONS, steady approval rate at t=0, averaged over
    issuer segments by share). Only the scenario's starting rates are used, never its
    incidents; the draw has its own stream so other policies' draws are unchanged.
    """
    rng = np.random.default_rng(np.random.SeedSequence([seed, 3]))
    out: dict[str, dict[str, float]] = {}
    for aid in ids:
        rate = sum(s.share * world.approval_rate(aid, s.name, 0.0) for s in world.segments)
        approved = int(rng.binomial(WARM_OBSERVATIONS, rate))
        out[aid] = {
            "rate": min(max(approved / WARM_OBSERVATIONS, 1e-4), 1.0 - 1e-4),
            "observations": float(WARM_OBSERVATIONS),
            "window_sec": WARM_WINDOW_SEC,
        }
    return out


def longmem_warm_router_config(ids: Sequence[str], world: World, seed: int) -> RouterConfig:
    """``loom_longmem`` started with ``--priors`` from a simulated previous day."""
    return apply_warm_priors(
        longmem_router_config(ids, seed), previous_day_priors(world, ids, seed)
    )


def raw_router_config(ids: Sequence[str], seed: int) -> RouterConfig:
    """Served half-life without the PID: raw Thompson winner-take-all."""
    return RouterConfig(routes=_routes(ids, DEFAULT_HALF_LIFE_SEC), pid_config=None, seed=seed)


def _named(policy: RoutingPolicy, name: str) -> RoutingPolicy:
    policy.name = name
    return policy


PolicyFactory = Callable[[Sequence[str], World, int], RoutingPolicy]

POLICIES: dict[str, PolicyFactory] = {
    "loom_served": lambda ids, w, s: _named(
        LoomPolicy(served_router_config(ids, s)), "loom_served"
    ),
    "loom_bench": lambda ids, w, s: _named(LoomPolicy(bench_router_config(ids, s)), "loom_bench"),
    "loom_failfast": lambda ids, w, s: _named(
        LoomPolicy(failfast_router_config(ids, s)), "loom_failfast"
    ),
    "loom_longmem": lambda ids, w, s: _named(
        LoomPolicy(longmem_router_config(ids, s)), "loom_longmem"
    ),
    "loom_longmem_warm": lambda ids, w, s: _named(
        LoomPolicy(longmem_warm_router_config(ids, w, s)), "loom_longmem_warm"
    ),
    "loom_raw": lambda ids, w, s: _named(LoomPolicy(raw_router_config(ids, s)), "loom_raw"),
    "static_m3": lambda ids, w, s: _named(
        StaticBreakerPolicy(
            ids,
            FailoverPolicyConfig(
                consecutive_failure_threshold=3, cooldown_transactions=30, failback_mode="probe"
            ),
        ),
        "static_m3",
    ),
    "competent": lambda ids, w, s: CompetentBreakerPolicy(ids, seed=s, retry=True),
    "competent_noretry": lambda ids, w, s: CompetentBreakerPolicy(ids, seed=s, retry=False),
    "sr_window": lambda ids, w, s: SRWindowPolicy(ids, seed=s),
    "sw_ucb": lambda ids, w, s: SlidingWindowUCBPolicy(ids),
    "oracle": lambda ids, w, s: OraclePolicy(w, ids),
}

REFERENCE = "oracle"


def build_policy(name: str, ids: Sequence[str], world: World, seed: int) -> RoutingPolicy:
    """Construct a fresh policy by suite name."""
    try:
        factory = POLICIES[name]
    except KeyError:
        raise ValueError(f"unknown policy {name!r}; known: {sorted(POLICIES)}") from None
    return factory(ids, world, seed)
