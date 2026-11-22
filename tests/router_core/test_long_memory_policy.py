"""Stage 2 technical mixture and gate-owned failures, through the discrete-event harness.

Against the Stage 1 code these fail: the technical belief was one 2.3 s Beta, and every
failed gate probe was booked into it.
"""

from __future__ import annotations

import numpy as np
import pytest

from acquirer_sim.world import AcquirerProfile, FailureMode, Outage, World
from router_core.policy import LoomPolicy
from router_core.state import (
    LONG_MEMORY_HALF_LIVES_SEC,
    AcquirerState,
    AcquirerStateConfig,
    Outcome,
)
from scripts.suite.des import Arrivals, simulate
from scripts.suite.policies import failfast_router_config, longmem_router_config

A, B = "acquirer_alpha", "acquirer_beta"


def _outage_run(policy: LoomPolicy, horizon: float = 900.0) -> None:
    profiles = (AcquirerProfile(A, approval_rate=0.95), AcquirerProfile(B, approval_rate=0.94))
    world = World(profiles, events=(Outage(A, 60.0, 360.0, FailureMode.HTTP_503),), seed=4)
    times = Arrivals("poisson", 5.0).times(horizon, np.random.default_rng(2))
    simulate(world, policy, times)


def test_technical_mixture_tracks_outcomes_and_reports_weights() -> None:
    """The technical belief becomes a mixture; alpha/beta are its moment-matched Beta."""
    cfg = AcquirerStateConfig(technical_half_lives_sec=LONG_MEMORY_HALF_LIVES_SEC)
    state = AcquirerState("a", cfg, initial_timestamp=0.0)
    for i in range(1, 601):
        state.record_outcome(True, timestamp=i / 10, outcome=Outcome.AUTHORIZED)
    snap = state.get_state()
    assert len(snap.technical_weights) == len(LONG_MEMORY_HALF_LIVES_SEC)
    assert snap.expected_success_rate > 0.98
    for i in range(601, 621):  # a burst of failures: the fast members take over
        state.record_outcome(False, timestamp=i / 10, outcome=Outcome.TECHNICAL_FAILURE)
    after = state.get_state()
    assert after.technical_weights[0] + after.technical_weights[1] > 0.9
    assert after.expected_success_rate < 0.8
    assert 0.0 <= after.health_score <= 1.0


def test_technical_mixture_round_trips_through_export() -> None:
    """Belief snapshots carry the technical mixture under its own key prefix."""
    cfg = AcquirerStateConfig(
        technical_half_lives_sec=(5.0, 600.0), approval_half_lives_sec=(5.0, 600.0)
    )
    state = AcquirerState("a", cfg, initial_timestamp=0.0)
    state.record_outcome(False, timestamp=1.0, outcome=Outcome.TECHNICAL_FAILURE)
    state.record_outcome(False, timestamp=2.0, outcome=Outcome.ISSUER_DECLINE)
    saved = state.export_beliefs()
    assert "technical_m1_weight" in saved and "approval_m1_weight" in saved
    fresh = AcquirerState("a", cfg, initial_timestamp=0.0)
    fresh.restore_beliefs(saved)
    assert fresh.get_state() == state.get_state()


def test_technical_mixture_needs_wall_clock_decay() -> None:
    """Per-observation decay has no half-lives to mix."""
    with pytest.raises(ValueError):
        AcquirerStateConfig(decay_factor=0.98, technical_half_lives_sec=(5.0, 60.0))


def _technical_failures(policy: LoomPolicy, acquirer: str) -> float:
    """Decayed technical-failure pseudo-count of the slowest technical member."""
    state = policy.registry._acquirers[acquirer]
    mix = state._tech_mixture
    assert mix is not None
    _, beta = mix.members[-1]
    return beta - state.config.beta_prior


def test_gate_owned_failures_stay_out_of_the_beliefs() -> None:
    """A 5 min outage: the 12 h technical member keeps the trip, not hundreds of probes."""
    withheld = LoomPolicy(longmem_router_config([A, B], 7))
    _outage_run(withheld)
    booked_cfg = longmem_router_config([A, B], 7).model_copy(
        update={"withhold_gated_failures": False}
    )
    booked = LoomPolicy(booked_cfg)
    _outage_run(booked)
    kept = _technical_failures(withheld, A)
    assert kept < 10.0  # the few failures before the gate opened
    assert _technical_failures(booked, A) > 10 * kept
    # After recovery the withheld policy believes the primary is technically healthy again.
    assert withheld.registry.get_state(A).expected_success_rate > 0.95


def test_withholding_is_off_in_the_stage1_configuration() -> None:
    """--fail-fast alone keeps Stage 1 behaviour (its committed results stay reproducible)."""
    cfg = failfast_router_config([A, B], 1)
    assert cfg.withhold_gated_failures is False and cfg.allocation_draws is None
    assert all(r.state_config.technical_half_lives_sec is None for r in cfg.routes)
