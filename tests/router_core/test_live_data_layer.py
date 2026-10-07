"""The served router logs to SQLite and, optionally, Redis (AUDIT F-04)."""

from __future__ import annotations

import json
import pathlib
import sqlite3

import fakeredis.aioredis
import httpx
import pytest
from fastapi.testclient import TestClient

from data_layer.config import DataLayerConfig
from data_layer.live import RedisSidecar
from router_core.app import create_router_app
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.router import BanditRouter
from router_core.server import build_data_config, parse_args
from router_core.state import AcquirerStateConfig


def _reply(request: httpx.Request) -> httpx.Response:
    """Authorize every other payment; decline the rest with DO_NOT_HONOR."""
    body = json.loads(request.content)
    n = int(body["transaction_id"].rsplit("_", 1)[-1])
    ok = n % 2 == 0
    return httpx.Response(
        200,
        json={
            "transaction_id": body["transaction_id"],
            "acquirer_id": "a",
            "status": "AUTHORIZED" if ok else "DECLINED",
            "authorized": ok,
            "authorization_code": "AUTH_X" if ok else None,
            "decline_code": None if ok else "DO_NOT_HONOR",
            "decline_message": None,
            "simulated_latency_ms": 0.0,
            "timestamp": 0.0,
        },
    )


def _router() -> BanditRouter:
    routes = [
        AcquirerRouteConfig(
            acquirer_id=aid, base_url="http://acq", state_config=AcquirerStateConfig()
        )
        for aid in ("a", "b")
    ]
    client = httpx.AsyncClient(transport=httpx.MockTransport(_reply))
    return BanditRouter(config=RouterConfig(routes=routes, seed=3), http_client=client)


def _route_n(client: TestClient, n: int) -> None:
    for i in range(n):
        resp = client.post("/route", json={"transaction_id": f"tx_{i}", "amount": 5.0})
        assert resp.status_code == 200


def test_every_live_decision_is_logged(tmp_path: pathlib.Path) -> None:
    """The served app writes one ledger row per /route, with its outcome kind."""
    db_file = tmp_path / "ledger.db"
    cfg = DataLayerConfig(ledger_enabled=True, sqlite_db_path=str(db_file), redis_enabled=False)
    app = create_router_app(router=_router(), data_config=cfg)
    with TestClient(app) as client:
        _route_n(client, 6)
        health = client.get("/health").json()
        assert health["ledger"]["enabled"] is True
        assert health["ledger"]["status"] == "ok"
        assert health["redis"] == {"enabled": False}

    with sqlite3.connect(db_file) as conn:
        rows = conn.execute("SELECT transaction_id, outcome FROM transactions").fetchall()
        n_outcomes = conn.execute("SELECT COUNT(*) FROM acquirer_outcomes").fetchone()[0]
    assert len(rows) == 6 and n_outcomes == 6
    assert {outcome for _tx, outcome in rows} == {"AUTHORIZED", "ISSUER_DECLINE"}


def test_unopenable_ledger_refuses_to_start(tmp_path: pathlib.Path) -> None:
    """A ledger path that cannot be opened stops startup instead of running unaudited."""
    bad = tmp_path / "missing_dir" / "ledger.db"
    cfg = DataLayerConfig(ledger_enabled=True, sqlite_db_path=str(bad))
    app = create_router_app(router=_router(), data_config=cfg)
    with pytest.raises(sqlite3.OperationalError), TestClient(app):
        pass


def test_ledger_can_be_turned_off(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """With the ledger off, nothing is written and /health says so."""
    monkeypatch.chdir(tmp_path)
    app = create_router_app(router=_router(), data_config=DataLayerConfig(ledger_enabled=False))
    with TestClient(app) as client:
        _route_n(client, 2)
        assert client.get("/health").json()["ledger"] == {"enabled": False}
    assert list(tmp_path.iterdir()) == []


def test_runtime_ledger_losses_show_as_degraded(tmp_path: pathlib.Path) -> None:
    """Payments continue when the ledger loses records, and /health reports it."""
    cfg = DataLayerConfig(ledger_enabled=True, sqlite_db_path=str(tmp_path / "l.db"))
    app = create_router_app(router=_router(), data_config=cfg)
    with TestClient(app) as client:
        app.state.ledger._dropped_count = 3  # e.g. queue overflow
        _route_n(client, 2)
        ledger = client.get("/health").json()["ledger"]
    assert ledger["status"] == "degraded"
    assert ledger["dropped"] == 3


def test_cli_flags_override_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """--ledger/--no-ledger, --ledger-path and --redis override .env settings."""
    monkeypatch.setenv("LEDGER_ENABLED", "true")
    monkeypatch.setenv("REDIS_ENABLED", "false")
    assert build_data_config(parse_args([])).ledger_enabled is True
    cfg = build_data_config(parse_args(["--no-ledger", "--redis", "--ledger-path", "x.db"]))
    assert (cfg.ledger_enabled, cfg.redis_enabled, cfg.sqlite_db_path) == (False, True, "x.db")


def test_defaults_are_ledger_on_redis_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Out of the box the served router keeps a ledger and does not need Redis."""
    monkeypatch.delenv("LEDGER_ENABLED", raising=False)
    monkeypatch.delenv("REDIS_ENABLED", raising=False)
    cfg = DataLayerConfig(_env_file=None)  # type: ignore[call-arg]
    assert (cfg.ledger_enabled, cfg.redis_enabled) == (True, False)


# --- Redis: events and belief snapshots ---------------------------------------------


async def test_sidecar_publishes_events_and_restores_snapshots() -> None:
    """Events reach the routing channel, and a new router resumes from the snapshot."""
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    cfg = DataLayerConfig(redis_enabled=True, ledger_enabled=False, redis_snapshot_interval_sec=60)
    pubsub = redis.pubsub()
    await pubsub.subscribe(cfg.redis_channel_routing)

    first = _router()
    sidecar = RedisSidecar(registry=first.registry, config=cfg, client=redis)
    await sidecar.start()
    first.attach_data_hooks(event_publisher=sidecar)
    from acquirer_sim.models import AuthorizeRequest

    for i in range(4):
        await first.route(AuthorizeRequest(transaction_id=f"tx_{i}", amount=5.0))
    await sidecar.close()  # final flush and snapshot

    received = []
    for _ in range(20):
        msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=0.05)
        if msg is not None:
            received.append(json.loads(msg["data"])["transaction_id"])
    assert received == [f"tx_{i}" for i in range(4)]
    assert sidecar.status()["published"] == 4

    second = _router()
    restorer = RedisSidecar(registry=second.registry, config=cfg, client=redis)
    await restorer.start()
    assert restorer.restored == ["a", "b"]
    for aid in ("a", "b"):
        assert second.get_state(aid).total_count == first.get_state(aid).total_count
        assert second.get_state(aid).approval_beta == first.get_state(aid).approval_beta
    await restorer.close()


class DownRedis:
    """A Redis client whose server is unreachable."""

    async def ping(self) -> None:
        raise ConnectionError("connection refused")

    async def get(self, key: str) -> None:
        raise ConnectionError("connection refused")

    async def set(self, key: str, value: str) -> None:
        raise ConnectionError("connection refused")

    async def publish(self, channel: str, message: str) -> int:
        raise ConnectionError("connection refused")

    async def aclose(self) -> None:
        return None


def test_redis_down_does_not_block_payments(tmp_path: pathlib.Path) -> None:
    """With Redis unreachable the app starts, routes payments and reports Redis degraded."""
    cfg = DataLayerConfig(
        redis_enabled=True,
        ledger_enabled=False,
        redis_retry_interval_sec=0.01,
        redis_event_queue_size=3,
    )
    router = _router()
    app = create_router_app(router=router, data_config=cfg)
    sidecar: RedisSidecar = app.state.redis
    sidecar._client = DownRedis()  # type: ignore[assignment]
    with TestClient(app) as client:
        _route_n(client, 10)
        status = client.get("/health").json()["redis"]
    assert status["enabled"] is True
    assert status["status"] == "degraded"
    assert status["connected"] is False
    assert status["published"] == 0
    assert status["dropped"] >= 1
