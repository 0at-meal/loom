"""Telemetry and post-dispatch failures must not fail or block a payment (AUDIT F-05, F-07)."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, cast

import httpx
import pytest
from fastapi import WebSocket

from acquirer_sim.models import AuthorizeRequest
from router_core.app import create_router_app
from router_core.bandit import BanditStateRegistry
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig, AcquirerStateSnapshot, Outcome
from router_core.telemetry import TelemetryBroadcaster

CFG = AcquirerStateConfig(alpha_prior=1.0, half_life_sec=1.0)


def _authorized(_request: httpx.Request) -> httpx.Response:
    return httpx.Response(
        200,
        json={
            "transaction_id": "t",
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


def _router(client: httpx.AsyncClient, registry: BanditStateRegistry | None = None) -> BanditRouter:
    routes = [AcquirerRouteConfig(acquirer_id="a", base_url="http://a", state_config=CFG)]
    return BanditRouter(
        config=RouterConfig(routes=routes, seed=1),
        http_client=client,
        registry=registry,
        clock=lambda: 1.0,
    )


class StalledSocket:
    """A dashboard client that never reads: every send blocks forever."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, text: str) -> None:
        await asyncio.Event().wait()


class RecordingSocket:
    """A healthy dashboard client."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, text: str) -> None:
        self.sent.append(text)


# --- F-05: telemetry off the request path ---------------------------------------------


async def test_publish_never_waits_for_a_stalled_client() -> None:
    """Publishing returns immediately; a stalled client only fills its own queue."""
    hub = TelemetryBroadcaster(queue_size=4)
    stalled, healthy = StalledSocket(), RecordingSocket()
    hub.register(cast(WebSocket, stalled))
    hub.register(cast(WebSocket, healthy))

    slowest = 0.0
    for i in range(10):
        t0 = time.perf_counter()
        hub.publish({"n": i})
        slowest = max(slowest, time.perf_counter() - t0)
        for _ in range(3):  # let the healthy client's sender run
            await asyncio.sleep(0)
    assert slowest < 0.01

    await asyncio.sleep(0.05)
    assert [json.loads(m)["n"] for m in healthy.sent] == list(range(10))
    # The stalled client's queue keeps only the newest messages; the rest are counted.
    assert hub.dropped >= 10 - 4 - 1
    await hub.close()


async def test_route_completes_with_a_stalled_dashboard() -> None:
    """POST /route answers even when a connected dashboard never reads (audit ws_block.py)."""
    transport_to_acquirer = httpx.MockTransport(_authorized)
    acquirer_client = httpx.AsyncClient(transport=transport_to_acquirer)
    app = create_router_app(router=_router(acquirer_client))
    hub: TelemetryBroadcaster = app.state.telemetry
    hub.register(cast(WebSocket, StalledSocket()))

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://router"
    ) as client:
        for i in range(hub.queue_size + 20):
            resp = await asyncio.wait_for(
                client.post("/route", json={"transaction_id": f"t{i}", "amount": 5.0}),
                timeout=2.0,
            )
            assert resp.status_code == 200
    await hub.close()


async def test_broadcast_errors_do_not_fail_the_payment(monkeypatch: pytest.MonkeyPatch) -> None:
    """If building or publishing the telemetry event raises, /route still returns the result."""
    app = create_router_app(
        router=_router(httpx.AsyncClient(transport=httpx.MockTransport(_authorized)))
    )
    hub: TelemetryBroadcaster = app.state.telemetry

    def boom(_payload: dict[str, Any]) -> None:
        raise RuntimeError("telemetry down")

    monkeypatch.setattr(hub, "publish", boom)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://router"
    ) as client:
        resp = await client.post("/route", json={"transaction_id": "t1", "amount": 5.0})
    assert resp.status_code == 200
    assert resp.json()["authorized"] is True


# --- F-07: unexpected acquirer responses and post-dispatch failures ---------------------


@pytest.mark.parametrize(
    "handler",
    [
        lambda _r: httpx.Response(422, json={"detail": "bad"}),
        lambda _r: httpx.Response(200, text="<html>not json</html>"),
        lambda _r: httpx.Response(200, json={"unexpected": "shape"}),
    ],
    ids=["http-422", "non-json-200", "invalid-schema-200"],
)
async def test_unexpected_responses_are_technical_failures(handler: Any) -> None:
    """Responses the router cannot interpret are booked against the acquirer, not raised."""
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await _router(client).route(AuthorizeRequest(transaction_id="t", amount=5.0))
    assert result.status == "ERROR"
    assert result.outcome == Outcome.TECHNICAL_FAILURE
    assert result.authorized is False
    assert result.error_message
    assert result.state_snapshot.beta == pytest.approx(2.0)


async def test_protocol_errors_are_technical_failures() -> None:
    """A broken connection mid-response (RemoteProtocolError) is an acquirer failure."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.RemoteProtocolError("peer closed connection", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await _router(client).route(AuthorizeRequest(transaction_id="t", amount=5.0))
    assert result.status == "ERROR"
    assert result.outcome == Outcome.TECHNICAL_FAILURE
    assert "RemoteProtocolError" in (result.error_message or "")


class BrokenRegistry(BanditStateRegistry):
    """A state store that fails after the acquirer has answered (e.g. Redis down)."""

    def record_outcome(self, *args: Any, **kwargs: Any) -> AcquirerStateSnapshot:
        raise ConnectionError("state store unavailable")


async def test_state_update_failure_still_returns_the_authorization() -> None:
    """An approved payment is reported as approved even if the belief update fails."""
    registry = BrokenRegistry(CFG)
    async with httpx.AsyncClient(transport=httpx.MockTransport(_authorized)) as client:
        result = await _router(client, registry=registry).route(
            AuthorizeRequest(transaction_id="t", amount=5.0)
        )
    assert result.authorized is True
    assert result.status == "AUTHORIZED"
    assert "state update failed" in (result.error_message or "")
    assert result.state_snapshot.acquirer_id == "a"
