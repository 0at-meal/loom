"""Decline attribution (AUDIT F-02, F-26): issuer declines are not acquirer failures."""

from __future__ import annotations

import httpx
import numpy as np
import pytest
from fastapi.testclient import TestClient

from acquirer_sim.app import create_app
from acquirer_sim.models import AuthorizeRequest, LatencyConfig
from router_core.bandit import BanditStateRegistry
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig, Outcome

CFG = AcquirerStateConfig(alpha_prior=1.0, half_life_sec=1.0, approval_half_life_sec=60.0)


def _registry() -> BanditStateRegistry:
    registry = BanditStateRegistry(CFG)
    registry.register_acquirer("a", initial_timestamp=0.0)
    registry.register_acquirer("b", initial_timestamp=0.0)
    return registry


def test_issuer_decline_leaves_the_technical_belief_alone() -> None:
    """DO_NOT_HONOR-style declines count against approval, not against the acquirer."""
    registry = _registry()
    for _ in range(20):
        snap = registry.record_outcome("a", False, timestamp=0.0, outcome=Outcome.ISSUER_DECLINE)
    assert snap.alpha - 1.0 == pytest.approx(20.0)  # the acquirer answered every time
    assert snap.beta == pytest.approx(1.0)
    assert snap.approval_beta - 1.0 == pytest.approx(20.0)
    assert snap.expected_approval_rate < 0.1


def test_technical_failure_leaves_approval_alone() -> None:
    """Outages lower the technical belief; they say nothing about issuers."""
    registry = _registry()
    snap = registry.record_outcome("a", False, timestamp=0.0, outcome=Outcome.TECHNICAL_FAILURE)
    assert snap.beta == pytest.approx(2.0)
    assert snap.approval_alpha == pytest.approx(1.0)
    assert snap.approval_beta == pytest.approx(1.0)


def test_authorization_counts_for_both_beliefs() -> None:
    """An approval is a technical success and an issuer approval."""
    registry = _registry()
    snap = registry.record_outcome("a", True, timestamp=0.0, outcome=Outcome.AUTHORIZED)
    assert snap.alpha == pytest.approx(2.0)
    assert snap.approval_alpha == pytest.approx(2.0)


def test_legacy_boolean_outcomes_are_technical() -> None:
    """Without an outcome kind, False is a technical failure and True an authorization."""
    registry = _registry()
    snap = registry.record_outcome("a", False, timestamp=0.0)
    assert snap.beta == pytest.approx(2.0)
    assert snap.approval_beta == pytest.approx(1.0)
    snap = registry.record_outcome("a", True, timestamp=0.0)
    assert snap.approval_alpha == pytest.approx(2.0)


def test_approval_memory_is_longer_than_technical_memory() -> None:
    """Issuer evidence fades on the slower approval half-life."""
    registry = _registry()
    registry.record_outcome("a", False, timestamp=0.0, outcome=Outcome.TECHNICAL_FAILURE)
    registry.record_outcome("a", False, timestamp=0.0, outcome=Outcome.ISSUER_DECLINE)
    snap = registry.get_state("a", now=60.0)
    assert snap.beta - 1.0 == pytest.approx(0.5**60)
    assert snap.approval_beta - 1.0 == pytest.approx(0.5)


def test_thompson_score_is_the_product_of_both_beliefs() -> None:
    """An acquirer that always answers but whose issuers always decline scores near zero."""
    registry = _registry()
    for _ in range(500):
        registry.record_outcome("a", False, timestamp=0.0, outcome=Outcome.ISSUER_DECLINE)
    samples = [registry.sample_all(rng=np.random.default_rng(i), now=0.0)["a"] for i in range(50)]
    assert max(samples) < 0.05
    snap = registry.get_state("a")
    assert snap.expected_psr == pytest.approx(
        snap.expected_success_rate * snap.expected_approval_rate
    )


def _response(decline_code: str | None) -> httpx.Response:
    authorized = decline_code is None
    return httpx.Response(
        200,
        json={
            "transaction_id": "t",
            "acquirer_id": "a",
            "status": "AUTHORIZED" if authorized else "DECLINED",
            "authorized": authorized,
            "authorization_code": "AUTH_X" if authorized else None,
            "decline_code": decline_code,
            "decline_message": None,
            "simulated_latency_ms": 0.0,
            "timestamp": 0.0,
        },
    )


@pytest.mark.parametrize(
    ("response", "expected"),
    [
        (_response(None), Outcome.AUTHORIZED),
        (_response("DO_NOT_HONOR"), Outcome.ISSUER_DECLINE),
        (_response("ACQUIRER_OUTAGE"), Outcome.TECHNICAL_FAILURE),
        (httpx.Response(503, text="down"), Outcome.TECHNICAL_FAILURE),
    ],
)
async def test_router_classifies_acquirer_responses(
    response: httpx.Response, expected: Outcome
) -> None:
    """The router books each response under the belief it is evidence for."""
    routes = [AcquirerRouteConfig(acquirer_id="a", base_url="http://a", state_config=CFG)]
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: response)) as client:
        router = BanditRouter(
            config=RouterConfig(routes=routes, seed=1), http_client=client, clock=lambda: 1.0
        )
        result = await router.route(AuthorizeRequest(transaction_id="t1", amount=10.0))
    assert result.outcome == expected
    snap = result.state_snapshot
    assert (snap.beta > 1.0) == (expected == Outcome.TECHNICAL_FAILURE)
    assert (snap.approval_beta > 1.0) == (expected == Outcome.ISSUER_DECLINE)


async def test_issuer_decline_burst_does_not_move_traffic() -> None:
    """A burst of issuer declines on the leader barely changes its routing score."""
    routes = [
        AcquirerRouteConfig(acquirer_id=aid, base_url="http://x", state_config=CFG)
        for aid in ("a", "b")
    ]
    registry = _registry()
    t = 0.0
    for _ in range(300):
        t += 1 / 15
        registry.record_outcome("a", True, timestamp=t, outcome=Outcome.AUTHORIZED)
        registry.record_outcome("b", True, timestamp=t, outcome=Outcome.AUTHORIZED)
    for _ in range(10):
        t += 1 / 15
        registry.record_outcome("a", False, timestamp=t, outcome=Outcome.ISSUER_DECLINE)

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: _response(None))):
        router = BanditRouter(config=RouterConfig(routes=routes, seed=3), registry=registry)
    a = router.get_state("a")
    b = router.get_state("b")
    # Ten declines out of ~310 recent payments: the technical belief is untouched, and the
    # expected PSR falls by about 3 points (a technical failure burst would halve it).
    assert a.beta == pytest.approx(1.0)
    assert a.expected_success_rate == pytest.approx(b.expected_success_rate)
    assert b.expected_psr - a.expected_psr < 0.05


def test_latency_spike_outlasts_the_router_timeout() -> None:
    """LATENCY_SPIKE must exceed the default 2 s timeout, or it is just a decline outage."""
    spike_ms = LatencyConfig().outage_spike_ms
    timeout_s = AcquirerRouteConfig(acquirer_id="a", base_url="http://a").timeout_sec
    assert spike_ms > timeout_s * 1000.0


def test_admin_calls_on_unknown_acquirers_return_404() -> None:
    """An outage toggle on a typo'd ID fails loudly instead of creating a new acquirer."""
    client = TestClient(create_app(default_acquirers=["acquirer_alpha"]))
    resp = client.post("/acquirers/acquirer_alpah/admin/outage", json={"active": True})
    assert resp.status_code == 404
    assert "acquirer_alpah" not in client.get("/acquirers").json()
    ok = client.post("/acquirers/acquirer_alpha/admin/outage", json={"active": True})
    assert ok.status_code == 200
