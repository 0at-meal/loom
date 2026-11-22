"""Stage 1 in LoomPolicy: gate eligibility, wall-clock probes, cascade, rate-based floor.

Driven through the discrete-event harness against ``acquirer_sim.world``. Each test names the
Stage 0 behaviour it replaces; against the Stage 0 policy (gate, cascade and rate floor off)
these assertions fail.
"""

from __future__ import annotations

import numpy as np
import pytest

from acquirer_sim.world import AcquirerProfile, FailureMode, Outage, World
from router_core.gate import GateConfig
from router_core.models import RouterConfig
from router_core.pid import PIDConfig
from router_core.policy import (
    DecisionContext,
    FailureKind,
    LoomPolicy,
    Observation,
)
from router_core.state import Outcome
from scripts.suite.des import Arrivals, Attempt, RunResult, simulate
from scripts.suite.policies import failfast_router_config, served_router_config

A, B = "acquirer_alpha", "acquirer_beta"
START, END = 60.0, 120.0


def _world(mode: FailureMode = FailureMode.HTTP_503, seed: int = 3) -> World:
    profiles = (AcquirerProfile(A, approval_rate=0.95), AcquirerProfile(B, approval_rate=0.94))
    return World(profiles, events=(Outage(A, START, END, mode),), seed=seed)


def _run(
    policy: LoomPolicy, world: World, tps: float, horizon: float = 180.0, seed: int = 1
) -> RunResult:
    times = Arrivals("poisson", tps).times(horizon, np.random.default_rng(seed))
    return simulate(world, policy, times)


def _attempts_to_a_during_outage(result: RunResult) -> list[Attempt]:
    return [
        a
        for p in result.payments
        for a in p.attempts
        if a.acquirer_id == A and START + 1.0 <= a.decided_at < END
    ]


def test_stage1_settings_are_off_by_default() -> None:
    """RouterConfig and PIDConfig keep the Stage 0 behaviour unless Stage 1 is asked for."""
    cfg = served_router_config([A, B], 1)
    assert cfg.gate_config is None and cfg.cascade is False
    assert cfg.pid_config is not None and cfg.pid_config.floor_rate_per_sec is None
    assert LoomPolicy(cfg).max_attempts == 1


def test_fast_fail_outage_costs_almost_no_payments() -> None:
    """Stage 0 Loom loses every payment the PID sends to the dead acquirer; now the gate
    trips in two failures and the cascade retries those too."""
    world = _world()
    failfast = _run(LoomPolicy(failfast_router_config([A, B], 7)), world, 15.0)
    served = _run(LoomPolicy(served_router_config([A, B], 7)), world, 15.0)

    def lost_on_a(result: RunResult) -> int:
        return sum(
            1
            for p in result.payments
            if not p.authorized and p.attempts[-1].acquirer_id == A and START <= p.arrival < END
        )

    assert lost_on_a(failfast) == 0
    assert lost_on_a(served) > 20
    first_failures = [
        p
        for p in failfast.payments
        if START <= p.arrival < START + 5 and p.attempts[0].kind == "HTTP_503"
    ]
    # Some decisions are made while the first failures are in flight; the rest are probes.
    assert len(first_failures) <= 6
    assert all(
        len(p.attempts) == 2 and p.authorized == p.attempts[1].issuer_approved
        for p in first_failures
    )


def test_outage_traffic_is_probes_only_and_independent_of_tps() -> None:
    """The 3% volume floor sent outage traffic in proportion to TPS; probes are per second."""
    counts = {}
    for tps in (15.0, 150.0):
        result = _run(LoomPolicy(failfast_router_config([A, B], 7)), _world(), tps)
        attempts = _attempts_to_a_during_outage(result)
        assert all(a.reason == "gate_probe" for a in attempts)
        counts[tps] = len(attempts)
    # 59 s with backoff 1 s, then 2 s: about 30 probes whatever the traffic.
    assert 20 <= counts[15.0] <= 40
    assert abs(counts[150.0] - counts[15.0]) <= 3

    served = {
        tps: len(
            _attempts_to_a_during_outage(
                _run(LoomPolicy(served_router_config([A, B], 7)), _world(), tps)
            )
        )
        for tps in (15.0, 150.0)
    }
    assert served[150.0] > 5 * served[15.0]


def test_timeout_outage_trips_on_overdue_requests_before_any_timeout_returns() -> None:
    """Stage 0 learned of a hung acquirer only when the 2 s timeouts came back."""
    policy = LoomPolicy(failfast_router_config([A, B], 7))
    result = _run(policy, _world(FailureMode.TIMEOUT), 15.0)
    gate = policy.gate
    assert gate is not None
    first_attempts_to_a = [
        p.attempts[0].decided_at
        for p in result.payments
        if p.attempts[0].acquirer_id == A
        and START <= p.attempts[0].decided_at < START + 3.0
        and p.attempts[0].reason != "gate_probe"
    ]
    # 250 ms latency, k = 3 x p95: open within about one second, not two.
    assert first_attempts_to_a and max(first_attempts_to_a) < START + 1.3
    # Probes into a hang lose their payment, so they back off to 30 s.
    probes = [a for a in _attempts_to_a_during_outage(result) if a.reason == "gate_probe"]
    assert 3 <= len(probes) <= 8
    assert result.ambiguous_retries == 0


def test_recovery_is_found_within_the_short_probe_backoff() -> None:
    """After a fast-fail outage the next probe comes within 2 s and closes the gate."""
    policy = LoomPolicy(failfast_router_config([A, B], 7))
    result = _run(policy, _world(), 15.0)
    back = [
        a.decided_at
        for p in result.payments
        for a in p.attempts
        if a.acquirer_id == A and a.decided_at >= END and a.kind == "AUTHORIZED"
    ]
    assert back and back[0] < END + 2.5


@pytest.mark.parametrize(
    ("failure", "retry"),
    [
        (FailureKind.FAST_FAIL, True),
        (FailureKind.THROTTLED, True),
        (FailureKind.NOT_SENT, True),
        (FailureKind.TIMEOUT, False),
        (FailureKind.AMBIGUOUS, False),
    ],
)
def test_cascade_retries_only_safe_failures(failure: FailureKind, retry: bool) -> None:
    policy = LoomPolicy(failfast_router_config([A, B], 1))
    d = policy.decide(DecisionContext("tx", 40.0), 1.0)
    outcome = None if failure == FailureKind.NOT_SENT else Outcome.TECHNICAL_FAILURE
    obs = Observation(outcome, failure)
    assert policy.should_retry(d, obs) is retry
    # Never a third attempt, and never after an issuer decision.
    second = policy.decide(DecisionContext("tx", 40.0, attempt=2), 1.1, frozenset({d.acquirer_id}))
    assert second.acquirer_id != d.acquirer_id and second.reason == "cascade_retry"
    assert not policy.should_retry(
        second, Observation(Outcome.TECHNICAL_FAILURE, FailureKind.FAST_FAIL)
    )
    assert not policy.should_retry(d, Observation(Outcome.ISSUER_DECLINE))


def test_no_retry_when_the_other_acquirer_is_gated() -> None:
    """A retry onto an acquirer that is itself failing would only add a hop."""
    policy = LoomPolicy(failfast_router_config([A, B], 1))
    for k in range(3):
        d = policy.decide(DecisionContext(f"b{k}", 40.0), 0.1 * k, frozenset({A}))
        policy.feedback(d, Observation(Outcome.TECHNICAL_FAILURE, FailureKind.FAST_FAIL), 0.1 * k)
    gate = policy.gate
    assert gate is not None and not gate.is_closed(B)
    d = policy.decide(DecisionContext("a", 40.0), 0.5)
    assert d.acquirer_id == A
    assert not policy.should_retry(d, Observation(Outcome.TECHNICAL_FAILURE, FailureKind.FAST_FAIL))


def test_floor_rate_makes_the_exploration_share_shrink_with_tps() -> None:
    """At 15 TPS the 0.45/s floor equals the old 3%; at 150 TPS it is ten times smaller."""
    shares = {}
    for tps in (15.0, 150.0):
        cfg = RouterConfig(
            routes=served_router_config([A, B], 1).routes,
            pid_config=PIDConfig(floor_rate_per_sec=0.45),
            seed=1,
        )
        policy = LoomPolicy(cfg)
        world = World((AcquirerProfile(A, 0.99), AcquirerProfile(B, 0.5)), seed=2)
        _run(policy, world, tps, horizon=60.0)
        shares[tps] = min(policy.current_allocation.values())
    assert shares[15.0] == pytest.approx(0.03, abs=0.003)
    assert shares[150.0] == pytest.approx(0.003, abs=0.0005)


def test_gate_config_must_be_valid_inside_router_config() -> None:
    with pytest.raises(ValueError):
        RouterConfig(
            routes=served_router_config([A, B], 1).routes,
            gate_config=GateConfig(baseline_failure_rate=0.9, outage_failure_rate=0.5),
        )
