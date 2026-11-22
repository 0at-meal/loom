"""Stage 2 multi-timescale approval belief (``router_core.state.BeliefMixture``).

Against the Stage 1 code (one approval Beta with a 60 s half-life, no mixture) these tests fail:
the mixture, its weights and the warm start did not exist.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from router_core.state import (
    LONG_MEMORY_HALF_LIVES_SEC,
    AcquirerState,
    AcquirerStateConfig,
    BeliefMixture,
    Outcome,
)


def _pattern(p: float, n: int) -> list[bool]:
    """A deterministic outcome sequence whose running approval rate tracks ``p``."""
    out, approved = [], 0
    for i in range(1, n + 1):
        take = approved < p * i
        out.append(take)
        approved += take
    return out


def _mixture(rate: float = 1.0 / 600.0) -> BeliefMixture:
    return BeliefMixture(LONG_MEMORY_HALF_LIVES_SEC, 1.0, 1.0, rate, now=0.0)


def test_large_drop_moves_weight_to_the_fast_member_within_seconds() -> None:
    """95% -> 60% at 15 outcomes/s: within 3 s the fast members carry most of the weight."""
    mix = _mixture()
    t = 0.0
    for approved in _pattern(0.95, 15 * 600):
        t += 1 / 15
        mix.observe(approved, t)
    before = mix.mean()
    for approved in _pattern(0.60, 45):  # 3 s
        t += 1 / 15
        mix.observe(approved, t)
    w = mix.weights
    assert before == pytest.approx(0.95, abs=0.01)
    assert w[0] + w[1] > 0.8
    assert mix.mean() < 0.85  # well below a 94% backup


def test_steady_state_weight_goes_to_the_slow_members() -> None:
    """Six hours stationary at 1 outcome/s: the 30 min and 12 h members dominate."""
    mix = _mixture(rate=1.0 / 86400.0)
    for i, approved in enumerate(_pattern(0.90, 6 * 3600), start=1):
        mix.observe(approved, float(i))
    w = mix.weights
    assert w[2] + w[3] > 0.9
    assert mix.mean() == pytest.approx(0.90, abs=0.005)


def test_weights_are_a_distribution_and_idle_time_shares_toward_uniform() -> None:
    """Fixed share: weights always sum to 1 and drift toward uniform while no outcome arrives."""
    mix = _mixture(rate=1.0 / 60.0)
    for i, approved in enumerate(_pattern(0.9, 600), start=1):
        mix.observe(approved, i / 10)
    assert math.fsum(mix.weights) == pytest.approx(1.0)
    spread_before = max(mix.weights) - min(mix.weights)
    mix.advance(60.0 + 600.0)
    assert math.fsum(mix.weights) == pytest.approx(1.0)
    assert max(mix.weights) - min(mix.weights) < spread_before


def test_members_decay_toward_the_prior_on_their_own_half_life() -> None:
    """After one half-life of idle time a member keeps half its evidence."""
    mix = BeliefMixture((10.0, 1000.0), 1.0, 1.0, 1e-6, now=0.0, starts=[(101.0, 1.0)] * 2)
    mix.advance(10.0)
    (fast_a, _), (slow_a, _) = mix.members
    assert fast_a == pytest.approx(1.0 + 50.0)
    assert slow_a == pytest.approx(1.0 + 100.0 * 0.5 ** (10.0 / 1000.0))
    mix.advance(5.0)  # a clock step backwards is ignored
    assert mix.members[0][0] == pytest.approx(51.0)


def test_moment_matched_beta_has_the_mixture_mean_and_variance() -> None:
    """The single Beta used in snapshots and the ledger matches the mixture's two moments."""
    mix = BeliefMixture((5.0, 500.0), 1.0, 1.0, 1e-6, now=0.0, starts=None)
    t = 0.0
    for approved in _pattern(0.9, 200) + _pattern(0.5, 20):
        t += 0.1
        mix.observe(approved, t)
    a, b = mix.moment_matched()
    w = mix.weights
    means = [x / (x + y) for x, y in mix.members]
    second = sum(
        wk * (mk * (1 - mk) / (x + y + 1) + mk * mk)
        for wk, mk, (x, y) in zip(w, means, mix.members, strict=True)
    )
    mean = sum(wk * mk for wk, mk in zip(w, means, strict=True))
    assert a / (a + b) == pytest.approx(mean)
    assert a * b / ((a + b) ** 2 * (a + b + 1)) == pytest.approx(second - mean * mean)


def test_sampling_draws_from_the_weighted_members() -> None:
    """Thompson draws follow the mixture: their average is the mixture mean."""
    mix = BeliefMixture((5.0, 500.0), 1.0, 1.0, 1e-6, now=0.0, starts=[(91.0, 11.0), (60.0, 12.0)])
    rng = np.random.default_rng(0)
    draws = mix.sample_many(rng, 20_000)
    single = [mix.sample(rng) for _ in range(5_000)]
    assert float(np.mean(draws)) == pytest.approx(mix.mean(), abs=0.003)
    assert float(np.mean(single)) == pytest.approx(mix.mean(), abs=0.005)


def test_export_restore_round_trip_and_mismatch() -> None:
    """A saved mixture restores exactly; one with another member count is not loaded."""
    mix = _mixture()
    for i, approved in enumerate(_pattern(0.8, 100), start=1):
        mix.observe(approved, i / 10)
    saved = mix.export()
    other = _mixture()
    assert other.restore(saved)
    assert other.weights == mix.weights and other.members == mix.members
    two = BeliefMixture((5.0, 60.0), 1.0, 1.0, 1.0, now=0.0)
    assert not two.restore(saved)
    assert two.weights == (0.5, 0.5)


def test_acquirer_state_uses_the_mixture_only_when_configured() -> None:
    """Default config: one approval Beta, no weights. Configured: the mixture drives it."""
    plain = AcquirerState("a", AcquirerStateConfig(), initial_timestamp=0.0)
    assert plain.get_state().approval_weights == ()
    cfg = AcquirerStateConfig(approval_half_lives_sec=LONG_MEMORY_HALF_LIVES_SEC)
    state = AcquirerState("a", cfg, initial_timestamp=0.0)
    for i, approved in enumerate(_pattern(0.7, 300), start=1):
        kind = Outcome.AUTHORIZED if approved else Outcome.ISSUER_DECLINE
        state.record_outcome(approved, timestamp=i / 10, outcome=kind)
    snap = state.get_state()
    assert len(snap.approval_weights) == len(LONG_MEMORY_HALF_LIVES_SEC)
    assert snap.expected_approval_rate == pytest.approx(0.7, abs=0.03)
    # A technical failure is no approval evidence: the mixture only decays.
    weights = snap.approval_weights
    state.record_outcome(False, timestamp=30.0, outcome=Outcome.TECHNICAL_FAILURE)
    assert state.get_state().approval_weights == pytest.approx(weights, abs=1e-6)


def test_export_beliefs_carries_the_mixture() -> None:
    """Belief snapshots (Redis sidecar) include and restore the mixture."""
    cfg = AcquirerStateConfig(approval_half_lives_sec=(5.0, 600.0))
    state = AcquirerState("a", cfg, initial_timestamp=0.0)
    state.record_outcome(False, timestamp=1.0, outcome=Outcome.ISSUER_DECLINE)
    saved = state.export_beliefs()
    fresh = AcquirerState("a", cfg, initial_timestamp=0.0)
    fresh.restore_beliefs(saved)
    assert fresh.get_state().approval_weights == state.get_state().approval_weights
    assert fresh.get_state().approval_alpha == state.get_state().approval_alpha


def test_warm_start_seeds_each_belief_by_what_its_half_life_would_keep() -> None:
    """n outcomes spread over W seconds: a belief with half-life h keeps n h/(W ln2)(1-2^-W/h)."""
    cfg = AcquirerStateConfig(
        warm_approval_rate=0.9, warm_observations=86_400.0, warm_window_sec=86_400.0
    )
    a, b = cfg.warm_approval(43_200.0)
    kept = 86_400.0 * 43_200.0 / (86_400.0 * math.log(2)) * (1 - 2 ** (-2.0))
    assert a + b - 2.0 == pytest.approx(kept)
    assert (a - 1.0) / (a + b - 2.0) == pytest.approx(0.9)
    a5, b5 = cfg.warm_approval(5.0)
    assert a5 + b5 - 2.0 == pytest.approx(5.0 / math.log(2), rel=1e-6)  # ~7 outcomes
    assert cfg.warm_approval(None) == cfg.warm_approval(cfg.approval_half_life_sec)
    assert AcquirerStateConfig().warm_approval(5.0) == (1.0, 1.0)


@pytest.mark.parametrize("lives", [None, LONG_MEMORY_HALF_LIVES_SEC])
def test_warm_start_sets_the_starting_approval_belief(lives: tuple[float, ...] | None) -> None:
    """A day of ledger outcomes at 90% starts the belief near 90%, mostly in slow members."""
    cfg = AcquirerStateConfig(
        approval_half_lives_sec=lives, warm_approval_rate=0.9, warm_observations=86_400.0
    )
    snap = AcquirerState("a", cfg, initial_timestamp=0.0).get_state()
    assert snap.expected_approval_rate == pytest.approx(0.9, abs=0.01)
    if lives is not None:
        assert snap.approval_weights[-1] > 0.9  # weight follows the warm evidence


@pytest.mark.parametrize(
    "kwargs",
    [
        {"approval_half_lives_sec": (5.0,)},
        {"approval_half_lives_sec": (5.0, -1.0)},
        {"approval_half_lives_sec": (5.0, math.inf)},
        {"mixture_switch_rate_per_sec": 0.0},
        {"warm_approval_rate": 1.0},
        {"warm_observations": -1.0},
    ],
)
def test_invalid_stage2_settings_are_rejected(kwargs: dict[str, object]) -> None:
    """Bad half-lives, switch rates or warm-start values raise ValueError."""
    with pytest.raises(ValueError):
        AcquirerStateConfig(**kwargs)  # type: ignore[arg-type]
