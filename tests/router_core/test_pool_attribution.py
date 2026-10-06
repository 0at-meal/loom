"""Router-side pool exhaustion is not an acquirer failure (AUDIT F-16)."""

from __future__ import annotations

from typing import Any, cast

import httpx
import pytest

from acquirer_sim.models import AuthorizeRequest
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig

CFG = AcquirerStateConfig(alpha_prior=1.0, half_life_sec=1.0)
ROUTES = [
    AcquirerRouteConfig(acquirer_id=aid, base_url=f"http://{aid}", state_config=CFG)
    for aid in ("a", "b")
]


async def test_pool_timeout_is_not_charged_to_the_acquirer() -> None:
    """httpx.PoolTimeout means the router had no free connection; the acquirer never saw it."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.PoolTimeout("no connection available", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        router = BanditRouter(
            config=RouterConfig(routes=ROUTES[:1], seed=1), http_client=client, clock=lambda: 1.0
        )
        result = await router.route(AuthorizeRequest(transaction_id="t", amount=5.0))

    assert result.status == "ERROR"
    assert result.authorized is False
    assert result.outcome is None  # not booked against any belief
    assert "PoolTimeout" in (result.error_message or "")
    snap = router.get_state("a")
    assert snap.beta == pytest.approx(1.0)
    assert snap.total_count == 0
    assert router.pool_timeouts == 1


async def test_connect_timeout_is_still_an_acquirer_failure() -> None:
    """A connect timeout means the acquirer could not be reached, so it is charged."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("connect timed out", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        router = BanditRouter(
            config=RouterConfig(routes=ROUTES[:1], seed=1), http_client=client, clock=lambda: 1.0
        )
        await router.route(AuthorizeRequest(transaction_id="t", amount=5.0))
    assert router.get_state("a").beta == pytest.approx(2.0)
    assert router.pool_timeouts == 0


async def test_each_acquirer_gets_its_own_connection_pool() -> None:
    """Without an injected client, one slow acquirer cannot use up the others' connections."""
    router = BanditRouter(config=RouterConfig(routes=ROUTES, max_connections=7, seed=1))
    await router.start()
    try:
        client_a = router.client_for("a")
        client_b = router.client_for("b")
        assert client_a is not client_b
        for client in (client_a, client_b):
            pool = cast(Any, client)._transport._pool
            assert pool._max_connections == 7
    finally:
        await router.close()
    with pytest.raises(KeyError):
        router.client_for("a")


async def test_injected_client_is_shared_and_not_closed() -> None:
    """A caller-supplied client (tests, benchmarks) is used for every route and left open."""
    async with httpx.AsyncClient() as shared:
        router = BanditRouter(config=RouterConfig(routes=ROUTES, seed=1), http_client=shared)
        await router.start()
        assert router.client_for("a") is shared
        assert router.client_for("b") is shared
        await router.close()
        assert not shared.is_closed
