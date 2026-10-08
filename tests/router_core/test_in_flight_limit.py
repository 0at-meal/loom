"""At most max_in_flight payments are dispatched at once (AUDIT F-16)."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from acquirer_sim.models import AuthorizeRequest
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.router import BanditRouter
from router_core.server import build_router_config, parse_args
from scripts.concurrency_inflight import sent_to_dead


def _authorized(tx_id: str) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "transaction_id": tx_id,
            "acquirer_id": "a",
            "status": "AUTHORIZED",
            "authorized": True,
            "authorization_code": "AUTH_X",
            "decline_code": None,
            "decline_message": None,
            "simulated_latency_ms": 0.0,
            "timestamp": 0.0,
        },
    )


async def test_concurrency_never_exceeds_the_limit() -> None:
    """A burst larger than the limit waits; no more than max_in_flight reach acquirers."""
    active = 0
    peak = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.005)
        active -= 1
        return _authorized("t")

    routes = [AcquirerRouteConfig(acquirer_id="a", base_url="http://acq")]
    router = BanditRouter(
        config=RouterConfig(routes=routes, seed=1, max_in_flight=5),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    results = await asyncio.gather(
        *[router.route(AuthorizeRequest(transaction_id=f"t{i}", amount=1.0)) for i in range(40)]
    )
    assert peak == 5
    assert all(r.authorized for r in results)
    assert max(r.queue_wait_ms for r in results) > 0.0
    status = router.in_flight_status()
    assert status["max_in_flight"] == 5 and status["in_flight"] == 0 and status["waited"] > 0


async def test_limit_cuts_payments_sent_to_a_dead_acquirer() -> None:
    """The audit's 200-concurrent outage burst: the limit lets outcomes arrive first."""
    unlimited = await sent_to_dead("deficit", concurrent=True, max_in_flight=None)
    limited = await sent_to_dead("deficit", concurrent=True, max_in_flight=20)
    assert limited < unlimited / 2


def test_default_limit_and_cli_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """The default is 50; ROUTER_MAX_IN_FLIGHT and --max-in-flight override; 0 disables."""
    assert (
        RouterConfig(routes=[AcquirerRouteConfig(acquirer_id="a", base_url="x")]).max_in_flight
        == 50
    )
    monkeypatch.setenv("ROUTER_MAX_IN_FLIGHT", "12")
    assert build_router_config(parse_args([])).max_in_flight == 12
    assert build_router_config(parse_args(["--max-in-flight", "0"])).max_in_flight is None
