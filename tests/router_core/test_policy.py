"""Policy protocol (Stage 0): observation taxonomy, LoomPolicy contract, router classification."""

from __future__ import annotations

import httpx
import pytest

from acquirer_sim.models import AuthorizeRequest
from baseline_router.policies import (
    CompetentBreakerPolicy,
    SlidingWindowUCBPolicy,
    SRWindowPolicy,
    StaticBreakerPolicy,
)
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig
from router_core.policy import (
    DecisionContext,
    FailureKind,
    LoomPolicy,
    Observation,
    RoutingPolicy,
    classify_outcome,
    failure_for_status,
)
from router_core.router import BanditRouter
from router_core.state import Outcome

IDS = ("acquirer_alpha", "acquirer_beta", "acquirer_gamma")


def _config(pid: PIDConfig | None = None, seed: int = 3) -> RouterConfig:
    return RouterConfig(
        routes=[AcquirerRouteConfig(acquirer_id=a, base_url="http://x") for a in IDS],
        pid_config=pid,
        seed=seed,
    )


@pytest.mark.parametrize(
    ("failure", "safe"),
    [
        (FailureKind.FAST_FAIL, True),
        (FailureKind.THROTTLED, True),
        (FailureKind.NOT_SENT, True),
        (FailureKind.TIMEOUT, False),
        (FailureKind.AMBIGUOUS, False),
        (None, False),
    ],
)
def test_only_unprocessed_failures_are_safe_to_retry(
    failure: FailureKind | None, safe: bool
) -> None:
    """A retry is safe only when the payment certainly was not processed."""
    assert Observation(Outcome.TECHNICAL_FAILURE, failure).safe_to_retry is safe


def test_classification_helpers() -> None:
    """200 answers split by decline code; only 503, 422 and 429 statuses are known safe."""
    codes = frozenset({"ACQUIRER_OUTAGE"})
    assert classify_outcome(True, None, codes) == Observation(Outcome.AUTHORIZED)
    assert classify_outcome(False, "DO_NOT_HONOR", codes) == Observation(Outcome.ISSUER_DECLINE)
    assert classify_outcome(False, "ACQUIRER_OUTAGE", codes) == Observation(
        Outcome.TECHNICAL_FAILURE, FailureKind.FAST_FAIL
    )
    assert failure_for_status(503) == FailureKind.FAST_FAIL
    assert failure_for_status(422) == FailureKind.FAST_FAIL
    assert failure_for_status(429) == FailureKind.THROTTLED
    assert failure_for_status(500) == FailureKind.AMBIGUOUS
    assert failure_for_status(504) == FailureKind.AMBIGUOUS


def test_every_policy_satisfies_the_protocol() -> None:
    """Loom and all baselines implement RoutingPolicy."""
    policies: list[object] = [
        LoomPolicy(_config(PIDConfig())),
        StaticBreakerPolicy(IDS),
        CompetentBreakerPolicy(IDS, seed=1),
        SRWindowPolicy(IDS, seed=1),
        SlidingWindowUCBPolicy(IDS),
    ]
    for policy in policies:
        assert isinstance(policy, RoutingPolicy)


@pytest.mark.parametrize("mode", ["stochastic", "deficit", None])
def test_loom_propensities_cover_every_acquirer_and_sum_to_one(mode: str | None) -> None:
    """Propensities are a distribution over all acquirers containing the chosen one."""
    pid = PIDConfig(actuation_mode=mode) if mode is not None else None  # type: ignore[arg-type]
    policy = LoomPolicy(_config(pid))
    for i in range(50):
        d = policy.decide(DecisionContext(f"tx{i}", 10.0), now=i * 0.1)
        assert set(d.propensities) == set(IDS)
        assert sum(d.propensities.values()) == pytest.approx(1.0)
        assert d.propensities[d.acquirer_id] > 0.0
        policy.feedback(d, Observation(Outcome.AUTHORIZED), now=i * 0.1)
    if mode == "stochastic":
        assert d.reason == "pid_stochastic"
        assert d.propensities == pytest.approx(policy.current_allocation)


def test_loom_does_not_retry_and_ignores_router_side_failures() -> None:
    """Stage 0 Loom never retries; an observation without an outcome leaves beliefs alone."""
    policy = LoomPolicy(_config(PIDConfig()))
    d = policy.decide(DecisionContext("tx", 10.0), now=1.0)
    before = policy.registry.get_state(d.acquirer_id)
    obs = Observation(None, FailureKind.NOT_SENT)
    policy.feedback(d, obs, now=1.0)
    assert policy.registry.get_state(d.acquirer_id) == before
    fast = Observation(Outcome.TECHNICAL_FAILURE, FailureKind.FAST_FAIL)
    assert policy.max_attempts == 1
    assert not policy.should_retry(d, fast)


def test_loom_exclude_skips_the_pid() -> None:
    """A decision with exclusions never picks an excluded acquirer and does not step the PID."""
    policy = LoomPolicy(_config(PIDConfig(actuation_mode="deficit")))
    state = policy.pid_state
    d = policy.decide(DecisionContext("tx", 10.0, attempt=2), now=1.0, exclude=frozenset(IDS[:2]))
    assert d.acquirer_id == IDS[2]
    assert d.reason == "excluded_fallback"
    assert policy.pid_state is state
    with pytest.raises(ValueError, match="excluded"):
        policy.decide(DecisionContext("tx", 10.0), now=1.0, exclude=frozenset(IDS))


@pytest.mark.parametrize(
    ("status", "failure"),
    [(503, FailureKind.FAST_FAIL), (429, FailureKind.THROTTLED), (500, FailureKind.AMBIGUOUS)],
)
async def test_router_books_non_200_as_technical_failure(status: int, failure: FailureKind) -> None:
    """Every non-200 is a technical failure for Loom's beliefs, as before Stage 0."""
    seen: list[Observation] = []

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="nope")

    router = BanditRouter(
        config=_config(PIDConfig()),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
        clock=lambda: 1.0,
    )
    original = router.policy.feedback

    def spy(decision: object, observation: Observation, now: float) -> None:
        seen.append(observation)
        original(decision, observation, now)  # type: ignore[arg-type]

    router.policy.feedback = spy  # type: ignore[method-assign]
    res = await router.route(AuthorizeRequest(transaction_id="tx1", amount=5.0))
    assert res.outcome == Outcome.TECHNICAL_FAILURE
    assert res.status == "ERROR"
    assert seen == [Observation(Outcome.TECHNICAL_FAILURE, failure)]
    assert router.registry.get_state(res.selected_acquirer).beta > 1.0
