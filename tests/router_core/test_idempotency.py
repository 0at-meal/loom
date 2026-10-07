"""A transaction_id is dispatched at most once (AUDIT F-07, idempotency)."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi import FastAPI

from data_layer.config import DataLayerConfig
from router_core.app import create_router_app
from router_core.idempotency import Admission, IdempotencyStore, fingerprint
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.router import BanditRouter

NO_DATA = DataLayerConfig(ledger_enabled=False, redis_enabled=False)


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def test_store_admits_replays_and_rejects() -> None:
    """NEW, then IN_FLIGHT until completed, then REPLAY; a different body is a MISMATCH."""
    store = IdempotencyStore()
    fp = fingerprint({"transaction_id": "t", "amount": 5.0})
    assert store.admit("t", fp).admission == Admission.NEW
    assert store.admit("t", fp).admission == Admission.IN_FLIGHT
    assert store.admit("t", fingerprint({"amount": 6.0})).admission == Admission.MISMATCH
    sentinel = object()
    store.complete("t", sentinel)  # type: ignore[arg-type]
    decision = store.admit("t", fp)
    assert decision.admission == Admission.REPLAY and decision.result is sentinel
    assert store.status()["replayed"] == 1


def test_store_expires_after_ttl_and_evicts_oldest() -> None:
    """Entries expire after the TTL; above the cap the oldest go first and are counted."""
    clock = FakeClock()
    store = IdempotencyStore(ttl_sec=10.0, max_entries=2, clock=clock)
    for tx in ("a", "b", "c"):
        assert store.admit(tx, "x").admission == Admission.NEW
    assert store.evicted == 1
    assert store.admit("a", "x").admission == Admission.NEW  # "a" was evicted
    clock.now = 11.0
    assert store.admit("b", "x").admission == Admission.NEW  # "b" expired
    assert len(store) == 1


def test_abandoned_request_can_be_retried() -> None:
    """A request that failed before producing a result does not block its retry."""
    store = IdempotencyStore()
    store.admit("t", "x")
    store.abandon("t")
    assert store.admit("t", "x").admission == Admission.NEW


def _app(handler: httpx.MockTransport) -> tuple[FastAPI, list[str]]:
    routes = [AcquirerRouteConfig(acquirer_id="a", base_url="http://acq")]
    router = BanditRouter(
        config=RouterConfig(routes=routes, seed=1),
        http_client=httpx.AsyncClient(transport=handler),
    )
    return create_router_app(router=router, data_config=NO_DATA), []


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


async def test_retry_replays_the_first_result_without_dispatching() -> None:
    """A completed duplicate returns the stored result; the acquirer is called once."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return _authorized("t1")

    app, _ = _app(httpx.MockTransport(handler))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://router",
    ) as client:
        body = {"transaction_id": "t1", "amount": 5.0}
        first = (await client.post("/route", json=body)).json()
        second = await client.post("/route", json=body)
    assert second.status_code == 200
    assert second.json()["replayed"] is True
    assert first["replayed"] is False
    assert second.json()["selected_acquirer"] == first["selected_acquirer"]
    assert len(calls) == 1


async def test_concurrent_duplicate_is_rejected_not_dispatched() -> None:
    """While the first request is in flight, a duplicate gets 409 with Retry-After."""
    release = asyncio.Event()
    calls: list[str] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        await release.wait()
        return _authorized("t1")

    app, _ = _app(httpx.MockTransport(handler))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://router",
    ) as client:
        body = {"transaction_id": "t1", "amount": 5.0}
        first = asyncio.create_task(client.post("/route", json=body))
        while not calls:
            await asyncio.sleep(0.001)
        duplicate = await client.post("/route", json=body)
        release.set()
        original = await first
    assert duplicate.status_code == 409
    assert duplicate.headers["retry-after"] == "1"
    assert original.status_code == 200
    assert len(calls) == 1


async def test_same_id_with_a_different_body_is_rejected() -> None:
    """Reusing a transaction_id for a different amount is a client error."""
    app, _ = _app(httpx.MockTransport(lambda _r: _authorized("t1")))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://router",
    ) as client:
        await client.post("/route", json={"transaction_id": "t1", "amount": 5.0})
        resp = await client.post("/route", json={"transaction_id": "t1", "amount": 9.0})
    assert resp.status_code == 422


async def test_health_reports_idempotency_counters(monkeypatch: pytest.MonkeyPatch) -> None:
    """/health shows the store's size and counters."""
    app, _ = _app(httpx.MockTransport(lambda _r: _authorized("t1")))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://router",
    ) as client:
        body = {"transaction_id": "t1", "amount": 5.0}
        await client.post("/route", json=body)
        await client.post("/route", json=body)
        idem = (await client.get("/health")).json()["idempotency"]
    assert idem["entries"] == 1 and idem["replayed"] == 1
    assert idem["ttl_sec"] == 86400.0 and idem["max_entries"] == 100_000
