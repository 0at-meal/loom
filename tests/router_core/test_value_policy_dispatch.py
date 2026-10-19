"""Value-scaled protection at dispatch, with the PID on (AUDIT F-10)."""

from __future__ import annotations

import time

import httpx
import pytest

from acquirer_sim.models import AuthorizeRequest
from router_core.bandit import BanditStateRegistry
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig, Outcome
from router_core.value_policy import ValueScaledExplorationConfig, best_posterior_mean
from scripts.value_policy_outage import Actuation, collect

SEEDS = 10
ACQUIRERS = ("acquirer_alpha", "acquirer_beta")


class TestProtects:
    """Which amounts bypass the allocation draw."""

    def test_threshold_is_about_three_tau(self) -> None:
        config = ValueScaledExplorationConfig(enabled=True, tau=100.0)
        assert not config.protects(299.0)
        assert config.protects(300.0)
        assert config.protects(1e9)

    def test_disabled_zero_amount_and_lambda_one_never_protect(self) -> None:
        assert not ValueScaledExplorationConfig(enabled=False).protects(1e9)
        assert not ValueScaledExplorationConfig(enabled=True).protects(0.0)
        # lambda(V) is exactly 1.0 in floating point here, yet 1.0 still means off
        off = ValueScaledExplorationConfig(enabled=True, tau=100.0, protect_lambda=1.0)
        assert off.calculate_shrinkage(5000.0) == 1.0
        assert not off.protects(5000.0)

    def test_best_posterior_mean_breaks_ties_like_sampling(self) -> None:
        assert best_posterior_mean({"a": 0.9, "b": 0.8}) == "a"
        assert best_posterior_mean({"a": 0.9, "b": 0.9}) == "b"


@pytest.mark.parametrize("actuation", ["deficit", "stochastic"])
def test_high_value_avoids_dead_acquirer_with_pid_on(actuation: Actuation) -> None:
    """Over several seeds, high-value payments reach the dead acquirer far less often.

    The outage schedule is scripts/value_policy_outage.py: every fifth outage payment is
    5,000, the rest 10, and the PID is on with the given actuation.
    """
    protect = collect(SEEDS, "protect", actuation, "audit")
    off = collect(SEEDS, "off", actuation, "audit")
    blend = collect(SEEDS, "blend", actuation, "audit")

    high_rate = sum(r.high_to_dead for r in protect) / (10 * SEEDS)
    low_rate = sum(r.low_to_dead for r in protect) / (40 * SEEDS)
    off_high_rate = sum(r.high_to_dead for r in off) / (10 * SEEDS)
    blend_high_rate = sum(r.high_to_dead for r in blend) / (10 * SEEDS)

    # High-value payments avoid the dead acquirer much more than low-value ones ...
    assert high_rate <= 0.5 * low_rate, (high_rate, low_rate)
    # ... and much more than with the policy off or with sample blending alone.
    assert high_rate <= 0.5 * off_high_rate, (high_rate, off_high_rate)
    assert high_rate <= 0.5 * blend_high_rate, (high_rate, blend_high_rate)
    # The first outage payment cannot know about the outage; past it, protection holds.
    assert all(r.high_to_dead <= 2 for r in protect)


def _always_authorize(request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "transaction_id": "tx",
            "acquirer_id": request.url.path.split("/")[2],
            "authorized": True,
            "status": "AUTHORIZED",
            "simulated_latency_ms": 0.0,
            "timestamp": time.time(),
        },
    )


def _router(actuation: Actuation, clock: list[float]) -> tuple[BanditRouter, httpx.AsyncClient]:
    client = httpx.AsyncClient(transport=httpx.MockTransport(_always_authorize))
    state_config = AcquirerStateConfig(half_life_sec=2.3)
    config = RouterConfig(
        routes=[
            AcquirerRouteConfig(acquirer_id=a, base_url="http://mock", state_config=state_config)
            for a in ACQUIRERS
        ],
        pid_config=PIDConfig(actuation_mode=actuation),
        seed=7,
        value_scaled_config=ValueScaledExplorationConfig(enabled=True, tau=100.0),
    )
    return BanditRouter(config=config, http_client=client, clock=lambda: clock[0]), client


@pytest.mark.asyncio
@pytest.mark.parametrize("actuation", ["deficit", "stochastic"])
async def test_protected_payment_leaves_pid_and_deficit_state_alone(actuation: Actuation) -> None:
    clock = [0.0]
    router, client = _router(actuation, clock)
    for i in range(20):
        clock[0] += 0.1
        await router.route(AuthorizeRequest(transaction_id=f"lv{i}", amount=10.0))

    pid_before = router.pid_state
    allocation_before = router.current_allocation
    cumulative_before = dict(router._cumulative_target)
    dispatched_before = dict(router._dispatched_count)

    clock[0] += 0.1
    res = await router.route(AuthorizeRequest(transaction_id="hv", amount=5000.0))

    assert res.value_protected
    assert res.pid_diagnostics is None
    assert res.smoothed_allocation == allocation_before
    assert router.pid_state == pid_before
    assert router.current_allocation == allocation_before
    assert router._cumulative_target == cumulative_before
    assert router._dispatched_count == dispatched_before
    await client.aclose()


@pytest.mark.asyncio
async def test_protected_payment_goes_to_best_mean_and_updates_beliefs_by_one() -> None:
    """The amount picks the route only; the belief update is the same as for any payment."""
    clock = [0.0]
    router, client = _router("deficit", clock)
    for i in range(20):
        clock[0] += 0.1
        await router.route(AuthorizeRequest(transaction_id=f"lv{i}", amount=10.0))

    clock[0] += 0.1
    beliefs_before = router.registry.export_beliefs()
    means = {aid: s.expected_psr for aid, s in router.get_all_states().items()}
    res = await router.route(AuthorizeRequest(transaction_id="hv", amount=1e7))
    assert res.value_protected
    assert res.selected_acquirer == best_posterior_mean(means)

    twin = BanditStateRegistry()
    for aid in ACQUIRERS:
        twin.register_acquirer(aid, config=AcquirerStateConfig(half_life_sec=2.3))
    twin.restore_beliefs(beliefs_before)
    twin.get_all_states(now=clock[0])  # the router decays every arm to now when it samples
    twin.record_outcome(
        res.selected_acquirer, success=True, timestamp=clock[0], outcome=Outcome.AUTHORIZED
    )
    assert router.registry.export_beliefs() == twin.export_beliefs()
    await client.aclose()


@pytest.mark.asyncio
async def test_low_value_payments_still_use_the_pid() -> None:
    clock = [0.0]
    router, client = _router("deficit", clock)
    clock[0] += 0.1
    res = await router.route(AuthorizeRequest(transaction_id="lv", amount=10.0))
    assert not res.value_protected
    assert res.pid_diagnostics is not None
    await client.aclose()
