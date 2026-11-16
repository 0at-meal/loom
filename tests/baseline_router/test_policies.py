"""Baseline policies (Stage 0): static breaker, competent breaker, SR window, SW-UCB."""

from __future__ import annotations

import math

import pytest

from baseline_router.models import FailoverPolicyConfig
from baseline_router.policies import (
    CompetentBreakerPolicy,
    SlidingWindowUCBPolicy,
    SRWindowPolicy,
    StaticBreakerPolicy,
)
from router_core.policy import Decision, DecisionContext, FailureKind, Observation
from router_core.state import Outcome

A, B, C = "acquirer_alpha", "acquirer_beta", "acquirer_gamma"
AUTH = Observation(Outcome.AUTHORIZED)
ISSUER = Observation(Outcome.ISSUER_DECLINE)
FAST = Observation(Outcome.TECHNICAL_FAILURE, FailureKind.FAST_FAIL)
THROTTLE = Observation(Outcome.TECHNICAL_FAILURE, FailureKind.THROTTLED)
TIMEOUT = Observation(Outcome.TECHNICAL_FAILURE, FailureKind.TIMEOUT)
AMBIG = Observation(Outcome.TECHNICAL_FAILURE, FailureKind.AMBIGUOUS)


def _ctx(i: int = 0, attempt: int = 1) -> DecisionContext:
    return DecisionContext(f"tx{i}", 40.0, attempt=attempt)


def _step(policy: object, obs: Observation, now: float, i: int = 0) -> Decision:
    d: Decision = policy.decide(_ctx(i), now)  # type: ignore[attr-defined]
    policy.feedback(d, obs, now)  # type: ignore[attr-defined]
    return d


def _fail(policy: object, aid: str, now: float, times: int = 1) -> None:
    """Feed ``times`` fast technical failures on ``aid`` (as if routed there)."""
    for _ in range(times):
        policy.feedback(Decision("tx", aid, {}, "preferred", now), FAST, now)  # type: ignore[attr-defined]


# --- static breaker -------------------------------------------------------------------------


def test_static_breaker_counts_issuer_declines_and_falls_back_to_primary() -> None:
    """The repo baseline, unchanged: issuer declines trip it; all-tripped goes to the primary."""
    p = StaticBreakerPolicy([A, B], FailoverPolicyConfig(consecutive_failure_threshold=2))
    for i in range(2):
        assert _step(p, ISSUER, 0.0, i).acquirer_id == A
    assert p.decide(_ctx(), 0.0).acquirer_id == B
    p.feedback(Decision("tx", B, {A: 0, B: 1}, "priority", 0.0), ISSUER, 0.0)
    p.feedback(Decision("tx", B, {A: 0, B: 1}, "priority", 0.0), ISSUER, 0.0)
    d = p.decide(_ctx(), 0.0)
    assert (d.acquirer_id, d.reason) == (A, "exhaustion_fallback")
    assert not p.should_retry(d, FAST)


# --- competent breaker ----------------------------------------------------------------------


def test_competent_breaker_ignores_issuer_declines() -> None:
    """No number of issuer declines opens the breaker (AUDIT F-01)."""
    p = CompetentBreakerPolicy([A, B], seed=0, eps=0.0)
    for i in range(100):
        _step(p, ISSUER, i * 0.1, i)
    assert not p.is_open(A)


def test_competent_breaker_opens_after_k_technical_failures_and_probes_on_wall_clock() -> None:
    """3 technical failures open it; the half-open probe waits seconds, not payments."""
    p = CompetentBreakerPolicy([A, B], seed=0, eps=0.0, cooldown_sec=2.0)
    _fail(p, A, 1.0, times=2)
    assert not p.is_open(A)
    _fail(p, A, 1.0)
    assert p.is_open(A)
    # Many payments within the cooldown: none goes to A (a transaction-counted breaker would).
    for i in range(200):
        assert _step(p, AUTH, 1.0 + i * 0.009, i).acquirer_id == B
    probe = p.decide(_ctx(), 3.0)
    assert (probe.acquirer_id, probe.reason) == (A, "half_open_probe")
    # While the probe is in flight, nothing else goes to A.
    assert p.decide(_ctx(1), 3.0).acquirer_id == B
    p.feedback(probe, AUTH, 3.1)
    assert not p.is_open(A)


def test_competent_failed_probes_back_off() -> None:
    """Each failed probe doubles the cooldown, up to the cap."""
    p = CompetentBreakerPolicy(
        [A, B], seed=0, eps=0.0, cooldown_sec=2.0, cooldown_backoff=2.0, max_cooldown_sec=5.0
    )
    _fail(p, A, 0.0, times=3)
    t = 0.0
    waits = []
    for _ in range(4):
        nxt = t
        while (d := p.decide(_ctx(), nxt)).reason != "half_open_probe":
            nxt += 0.25
            assert nxt < 100.0, "no probe was ever sent"
        waits.append(nxt - t)
        p.feedback(d, FAST, nxt)
        t = nxt
    assert waits == [2.0, 4.0, 5.0, 5.0]


def test_competent_all_open_goes_to_least_recently_opened() -> None:
    """When every breaker is open, the acquirer opened longest ago gets the traffic."""
    p = CompetentBreakerPolicy([A, B, C], seed=0, eps=0.0, cooldown_sec=30.0)
    for t, aid in ((1.0, B), (2.0, A), (3.0, C)):
        _fail(p, aid, t, times=3)
    d = p.decide(_ctx(), 4.0)
    assert (d.acquirer_id, d.reason) == (B, "all_open")


@pytest.mark.parametrize(
    ("obs", "retry"),
    [(FAST, True), (THROTTLE, True), (TIMEOUT, False), (AMBIG, False), (ISSUER, False)],
)
def test_competent_retries_only_after_unprocessed_failures(obs: Observation, retry: bool) -> None:
    """One retry after a fast failure or a 429; never after a timeout or an ambiguous answer."""
    p = CompetentBreakerPolicy([A, B], seed=0, eps=0.0)
    d = p.decide(_ctx(), 0.0)
    p.feedback(d, obs, 0.0)
    assert p.should_retry(d, obs) is retry
    if retry:
        second = p.decide(_ctx(attempt=2), 0.0, exclude=frozenset({d.acquirer_id}))
        assert second.acquirer_id != d.acquirer_id
        assert second.reason == "retry"
        assert not p.should_retry(second, obs)
    assert not CompetentBreakerPolicy([A, B], seed=0, retry=False).should_retry(d, FAST)


def test_competent_hysteresis_and_exploration_propensities() -> None:
    """The preferred acquirer keeps traffic within the margin; propensities match eps."""
    p = CompetentBreakerPolicy([A, B, C], seed=4, eps=0.1, margin=0.03)
    d = p.decide(_ctx(), 0.0)
    assert d.propensities == pytest.approx({A: 0.9, B: 0.05, C: 0.05})
    picks = [p.decide(_ctx(i), 0.0).acquirer_id for i in range(4000)]
    assert picks.count(A) / 4000 == pytest.approx(0.9, abs=0.02)
    # B slightly better than A but within the margin: A stays preferred.
    for _ in range(40):
        p.feedback(Decision("tx", B, {}, "explore", 0.0), AUTH, 0.0)
    for k in range(40):
        p.feedback(Decision("tx", A, {}, "preferred", 0.0), AUTH if k else ISSUER, 0.0)
    assert p.estimate(B) > p.estimate(A)
    assert p.estimate(B) < p.estimate(A) + 0.03
    assert p.decide(_ctx(), 0.0).propensities[A] == pytest.approx(0.9)


# --- SR window ------------------------------------------------------------------------------


def test_sr_window_tries_unscored_then_routes_by_score() -> None:
    """Unscored acquirers rank first; afterwards the best window success rate wins."""
    p = SRWindowPolicy([A, B], seed=0, bucket_size=5, max_buckets=2, exploration=0.0)
    assert p.score(A) is None
    for _ in range(5):
        p.feedback(Decision("tx", A, {}, "x", 0.0), AUTH, 0.0)
    assert p.decide(_ctx(), 0.0).acquirer_id == B  # B still unscored
    for i in range(5):
        p.feedback(Decision("tx", B, {}, "x", 0.0), AUTH if i else ISSUER, 0.0)
    assert p.score(B) == pytest.approx(0.8)
    assert p.decide(_ctx(), 0.0).acquirer_id == A


def test_sr_window_eliminates_on_a_bad_latest_bucket() -> None:
    """A latest full bucket below the threshold ranks the acquirer last despite its score."""
    p = SRWindowPolicy(
        [A, B], seed=0, bucket_size=4, max_buckets=10, exploration=0.0, elimination_threshold=0.5
    )
    for _ in range(36):
        p.feedback(Decision("tx", A, {}, "x", 0.0), AUTH, 0.0)
    for _ in range(4):
        p.feedback(Decision("tx", B, {}, "x", 0.0), ISSUER, 0.0)
    for _ in range(4):
        p.feedback(Decision("tx", B, {}, "x", 0.0), AUTH, 0.0)
    for _ in range(4):
        p.feedback(Decision("tx", A, {}, "x", 0.0), FAST, 0.0)
    assert p.eliminated(A)
    assert p.score(A) == pytest.approx(0.9)
    assert p.decide(_ctx(), 0.0).acquirer_id == B
    d = SRWindowPolicy([A, B, C], seed=0, exploration=0.06).decide(_ctx(), 0.0)
    assert sum(d.propensities.values()) == pytest.approx(1.0)
    assert d.propensities[A] == pytest.approx(0.94)


# --- SW-UCB ---------------------------------------------------------------------------------


def test_sw_ucb_index_and_window() -> None:
    """Untried arms first; index = window mean + c1 * sqrt(1/N) over the last W attempts."""
    p = SlidingWindowUCBPolicy([A, B], window=4, c1=1.0)
    assert math.isinf(p.index(A))
    assert p.decide(_ctx(), 0.0).acquirer_id == A
    for obs in (AUTH, AUTH, ISSUER, AUTH, AUTH):  # the first AUTH falls out of the window
        p.feedback(Decision("tx", A, {}, "ucb", 0.0), obs, 0.0)
    assert p.index(A) == pytest.approx(0.75 + 0.5)
    assert p.decide(_ctx(), 0.0).acquirer_id == B
    assert not p.should_retry(Decision("tx", A, {}, "ucb", 0.0), FAST)
