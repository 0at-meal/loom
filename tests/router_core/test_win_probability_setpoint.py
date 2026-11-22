"""Stage 2 in LoomPolicy: win-probability setpoint, feasible target, strict integral clamp.

Against the Stage 1 policy (one-hot setpoint of a single Thompson draw, clamp-then-centre
integral) these assertions fail: the setpoint was one-hot and the integral could exceed its
clamp with three acquirers (AUDIT F-25).
"""

from __future__ import annotations

import math

import pytest

from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig, PIDState, calculate_pid_step
from router_core.policy import DecisionContext, LoomPolicy
from router_core.server import (
    LONG_MEMORY_ALLOCATION_DRAWS,
    apply_long_memory,
    build_router_config,
    parse_args,
)
from router_core.state import LONG_MEMORY_HALF_LIVES_SEC, AcquirerStateConfig, Outcome
from scripts.suite.policies import failfast_router_config, longmem_router_config

A, B, C = "acquirer_alpha", "acquirer_beta", "acquirer_gamma"


def _config(draws: int | None, ids: tuple[str, ...] = (A, B)) -> RouterConfig:
    return RouterConfig(
        routes=[AcquirerRouteConfig(acquirer_id=a, base_url="http://x.invalid") for a in ids],
        pid_config=PIDConfig(),
        allocation_draws=draws,
        seed=5,
    )


def _teach(policy: LoomPolicy, acquirer: str, approved: int, declined: int, t: float) -> None:
    reg = policy.registry
    for _ in range(approved):
        reg.record_outcome(acquirer, True, timestamp=t, outcome=Outcome.AUTHORIZED)
    for _ in range(declined):
        reg.record_outcome(acquirer, False, timestamp=t, outcome=Outcome.ISSUER_DECLINE)


def test_setpoint_is_a_feasible_win_probability_vector() -> None:
    """The PID target is a distribution with every entry at or above the floor."""
    policy = LoomPolicy(_config(LONG_MEMORY_ALLOCATION_DRAWS))
    _teach(policy, A, 90, 10, 0.0)
    _teach(policy, B, 88, 12, 0.0)
    _, trace = policy.decide_traced(DecisionContext("t1", 10.0), now=0.0)
    target = trace.target_allocation
    assert target is not None
    assert math.fsum(target.values()) == pytest.approx(1.0)
    assert all(v >= 0.03 - 1e-12 for v in target.values())
    # Neither entry is 0 or 1: the two beliefs overlap, so both win some draws.
    assert 0.03 < target[A] < 0.97 and target[A] > target[B]


def test_one_hot_setpoint_is_kept_without_allocation_draws() -> None:
    """With allocation_draws=None the target is still a one-hot of one draw (Stage 0/1)."""
    policy = LoomPolicy(_config(None))
    _, trace = policy.decide_traced(DecisionContext("t1", 10.0), now=0.0)
    assert trace.target_allocation is not None
    assert sorted(trace.target_allocation.values()) == [0.0, 1.0]


def test_a_clear_leader_gets_everything_above_the_floors() -> None:
    """A far better acquirer wins every draw; the projection leaves the floor to the other."""
    policy = LoomPolicy(_config(LONG_MEMORY_ALLOCATION_DRAWS))
    _teach(policy, A, 990, 10, 0.0)
    _teach(policy, B, 500, 500, 0.0)
    _, trace = policy.decide_traced(DecisionContext("t1", 10.0), now=0.0)
    assert trace.target_allocation == pytest.approx({A: 0.97, B: 0.03})


def test_integral_stays_within_its_clamp_with_three_acquirers() -> None:
    """A saturating one-hot target with K=3 pushes |I| past I_max unless the clamp is strict."""
    ids = [A, B, C]
    target = {A: 1.0, B: 0.0, C: 0.0}

    def peak(strict: bool) -> float:
        cfg = PIDConfig(ki=0.005, strict_integral_clamp=strict)
        state = PIDState.initialize(ids)
        alloc = dict(state.previous_allocation)
        worst = 0.0
        for _ in range(400):
            step = calculate_pid_step(target, alloc, state, cfg)
            state, alloc = step.next_state, step.smoothed_allocation
            worst = max(worst, *(abs(v) for v in state.accumulated_error.values()))
        return worst

    assert peak(strict=False) > 1.0 + 1e-6
    assert peak(strict=True) <= 1.0 + 1e-12


def test_long_memory_flag_builds_the_stage2_configuration() -> None:
    """--long-memory sets the approval bank, the draw count and the strict clamp."""
    cfg = build_router_config(parse_args(["--long-memory"]))
    assert cfg.allocation_draws == LONG_MEMORY_ALLOCATION_DRAWS
    assert cfg.pid_config is not None and cfg.pid_config.strict_integral_clamp
    for route in cfg.routes:
        assert route.state_config.approval_half_lives_sec == LONG_MEMORY_HALF_LIVES_SEC
    plain = build_router_config(parse_args([]))
    assert plain.allocation_draws is None
    assert plain.pid_config is not None and not plain.pid_config.strict_integral_clamp
    assert all(r.state_config.approval_half_lives_sec is None for r in plain.routes)


def test_suite_longmem_policy_matches_the_server_flags() -> None:
    """The suite's loom_longmem is --fail-fast --long-memory, as served."""
    served = build_router_config(parse_args(["--fail-fast", "--long-memory"]))
    suite = longmem_router_config([A, B, C], seed=1)
    assert suite.allocation_draws == served.allocation_draws
    assert suite.pid_config == served.pid_config
    assert suite.gate_config == served.gate_config and suite.cascade == served.cascade
    assert {r.state_config.approval_half_lives_sec for r in suite.routes} == {
        LONG_MEMORY_HALF_LIVES_SEC
    }
    assert apply_long_memory(failfast_router_config([A, B], 1)) == longmem_router_config([A, B], 1)


def test_apply_long_memory_keeps_other_state_settings() -> None:
    """Only the approval bank changes on each route; half-life and priors are kept."""
    base = RouterConfig(
        routes=[
            AcquirerRouteConfig(
                acquirer_id=A,
                base_url="http://x.invalid",
                state_config=AcquirerStateConfig(half_life_sec=0.9, warm_approval_rate=0.9),
            )
        ],
        pid_config=None,
    )
    out = apply_long_memory(base).routes[0].state_config
    assert out.half_life_sec == 0.9 and out.warm_approval_rate == 0.9
    assert out.approval_half_lives_sec == LONG_MEMORY_HALF_LIVES_SEC
