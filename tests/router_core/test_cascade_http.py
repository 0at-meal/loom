"""Stage 1 through the async router and the HTTP simulator: cascade, attempts, gate status."""

from __future__ import annotations

import logging

import httpx
import pytest

from acquirer_sim.app import create_app
from acquirer_sim.models import (
    AuthorizeRequest,
    LatencyConfig,
    OutageBehavior,
    OutageToggleRequest,
)
from router_core.router import BanditRouter
from scripts.suite.policies import failfast_router_config, served_router_config

A, B = "acquirer_alpha", "acquirer_beta"


async def _routes(
    behavior: OutageBehavior, fail_fast: bool, n: int = 40
) -> tuple[BanditRouter, list]:  # type: ignore[type-arg]
    sim_app = create_app(
        default_acquirers=[A, B], default_latency=LatencyConfig(base_ms=0.0, jitter_ms=0.0)
    )
    sim_app.state.registry.get(A).set_success_rate(1.0)
    sim_app.state.registry.get(B).set_success_rate(1.0)
    now = [0.0]
    transport = httpx.ASGITransport(app=sim_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        make = failfast_router_config if fail_fast else served_router_config
        config = make([A, B], 5)
        for r in config.routes:
            r.base_url = "http://testserver"
        router = BanditRouter(config=config, http_client=client, clock=lambda: now[0])
        await client.post(
            f"/acquirers/{A}/admin/outage",
            json=OutageToggleRequest(active=True, behavior=behavior).model_dump(),
        )
        results = []
        for i in range(n):
            now[0] = i / 15.0
            results.append(
                await router.route(AuthorizeRequest(transaction_id=f"t{i}", amount=40.0))
            )
    return router, results


@pytest.mark.parametrize(
    "behavior",
    [OutageBehavior.HTTP_503, OutageBehavior.RETURN_DECLINE, OutageBehavior.HTTP_429],
)
async def test_safe_failures_are_retried_on_the_other_acquirer(
    behavior: OutageBehavior, caplog: pytest.LogCaptureFixture
) -> None:
    """Every payment that first hit the failing acquirer is authorized by the second attempt."""
    caplog.set_level(logging.ERROR)
    router, results = await _routes(behavior, fail_fast=True)
    assert all(r.authorized for r in results)
    retried = [r for r in results if len(r.attempts) == 2]
    assert retried, "some payments must have hit the failing acquirer first"
    for r in retried:
        first, second = r.attempts
        assert (first.acquirer_id, first.attempt) == (A, 1)
        # A technical decline code comes back as HTTP 200, DECLINED; 503 and 429 as ERROR.
        expected = "DECLINED" if behavior == OutageBehavior.RETURN_DECLINE else "ERROR"
        assert first.status == expected
        assert first.failure == (
            "THROTTLED" if behavior == OutageBehavior.HTTP_429 else "FAST_FAIL"
        )
        assert (second.acquirer_id, second.reason, second.attempt) == (B, "cascade_retry", 2)
        assert r.selected_acquirer == B and r.status == "AUTHORIZED"
        if behavior == OutageBehavior.RETURN_DECLINE:
            # A 200 with a technical decline code has no error text; attempts[0] records it.
            assert r.error_message is None and first.outcome == "TECHNICAL_FAILURE"
        else:
            assert r.error_message is not None
            assert r.error_message.startswith(f"attempt 1 ({A})")
        assert r.acquirer_latency_ms == pytest.approx(
            first.acquirer_latency_ms + second.acquirer_latency_ms
        )
    status = router.gate_status()
    assert status is not None
    if behavior == OutageBehavior.HTTP_429:
        assert status[A]["state"] == "CLOSED"  # busy is not broken
    else:
        assert status[A]["state"] in ("OPEN", "HALF_OPEN")
        assert status[A]["trips"] >= 1


async def test_without_fail_fast_nothing_is_retried(caplog: pytest.LogCaptureFixture) -> None:
    """The served default keeps one attempt per payment, as before Stage 1."""
    caplog.set_level(logging.ERROR)
    router, results = await _routes(OutageBehavior.HTTP_503, fail_fast=False)
    assert all(len(r.attempts) == 1 for r in results)
    lost = [r for r in results if not r.authorized]
    assert lost and all(r.selected_acquirer == A for r in lost)
    assert all(r.error_message and not r.error_message.startswith("attempt") for r in lost)
    assert router.gate_status() is None
