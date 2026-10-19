"""Integration tests for Phase 7 Dashboard backend endpoints in router_core/app.py.

Verifies:
- WebSocket /ws/telemetry cold-start BOOTSTRAP payload
- Simulator admin proxies against an in-process simulator (success path)
- The same proxies against an unreachable simulator: HTTP 502 and no health alert (AUDIT F-18)
"""

import json
import socket
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from acquirer_sim.app import create_app
from acquirer_sim.models import OutageBehavior
from router_core.app import create_router_app
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig

IDS = ["acquirer_alpha", "acquirer_beta"]
OUTAGE_ON = {
    "active": True,
    "behavior": OutageBehavior.RETURN_DECLINE.value,
    "transition_seconds": 0.0,
}


def closed_port_url() -> str:
    """A localhost URL nothing listens on, so connections are refused."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        port = int(s.getsockname()[1])
    return f"http://127.0.0.1:{port}"


def make_test_router(base_url: str = "http://127.0.0.1:8001") -> BanditRouter:
    """Create a lightweight test router whose routes all point at ``base_url``."""
    config = RouterConfig(
        routes=[
            AcquirerRouteConfig(
                acquirer_id=acquirer_id,
                base_url=base_url,
                state_config=AcquirerStateConfig(alpha_prior=1.0, beta_prior=1.0),
            )
            for acquirer_id in IDS
        ]
    )
    return BanditRouter(config=config)


@pytest.fixture
def sim_app() -> FastAPI:
    return create_app(default_acquirers=IDS, default_base_rate=0.95, seed=42)


@pytest.fixture
def live_client(sim_app: FastAPI) -> Iterator[TestClient]:
    """Router app whose admin proxies reach an in-process simulator."""
    sim_client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=sim_app), base_url="http://sim"
    )
    app = create_router_app(router=make_test_router("http://sim"), simulator_client=sim_client)
    with TestClient(app) as client:
        yield client


@pytest.fixture
def offline_client() -> Iterator[TestClient]:
    """Router app whose acquirer routes point at a URL nothing listens on."""
    app = create_router_app(router=make_test_router(closed_port_url()))
    with TestClient(app) as client:
        yield client


def next_frame_after_ping(ws: Any) -> dict[str, Any]:
    """The first frame the server queues after a ping; PONG means nothing was broadcast."""
    ws.send_text("ping")
    return dict(json.loads(ws.receive_text()))


class TestPhase7DashboardBackend:
    """Test suite for Phase 7 dashboard WebSocket gateway and simulator proxy."""

    def test_websocket_telemetry_bootstrap(self) -> None:
        """Verify WebSocket /ws/telemetry emits a BOOTSTRAP event on connection."""
        router = make_test_router()
        app = create_router_app(router=router)

        with TestClient(app) as client:
            with client.websocket_connect("/ws/telemetry") as ws:
                raw_msg = ws.receive_text()
                data = json.loads(raw_msg)
                assert data["event_type"] == "BOOTSTRAP"
                assert "states" in data
                assert "acquirer_alpha" in data["states"]
                assert "acquirer_beta" in data["states"]
                assert data["states"]["acquirer_alpha"]["health_score"] == pytest.approx(1.0)


class TestSimulatorProxySuccess:
    """Proxies forward to the simulator, return its response and only then broadcast."""

    def test_toggle_outage_reaches_simulator_and_alerts(
        self, live_client: TestClient, sim_app: FastAPI
    ) -> None:
        with live_client.websocket_connect("/ws/telemetry") as ws:
            ws.receive_text()  # bootstrap
            resp = live_client.post(
                "/api/simulator/acquirers/acquirer_alpha/outage", json=OUTAGE_ON
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["outage_active"] is True
            assert data["acquirer_id"] == "acquirer_alpha"
            assert "simulated_offline" not in data
            alert = json.loads(ws.receive_text())
            assert alert["event_type"] == "HEALTH_ALERT"
            assert alert["acquirer_id"] == "acquirer_alpha"
            assert alert["severity"] == "CRITICAL"
        assert sim_app.state.registry.get("acquirer_alpha").is_outage_active is True

    def test_success_rate_reaches_simulator(
        self, live_client: TestClient, sim_app: FastAPI
    ) -> None:
        resp = live_client.post(
            "/api/simulator/acquirers/acquirer_alpha/success-rate",
            json={"success_rate": 0.60, "reason": "Brownout Test"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["effective_success_rate"] == pytest.approx(0.60)
        assert data["acquirer_id"] == "acquirer_alpha"
        sim = sim_app.state.registry.get("acquirer_alpha")
        assert sim.effective_success_rate == pytest.approx(0.60)

    def test_states_and_reset_reach_simulator(self, live_client: TestClient) -> None:
        states = live_client.get("/api/simulator/admin/states")
        assert states.status_code == 200
        assert states.json()["total_acquirers"] == len(IDS)
        reset = live_client.post("/api/simulator/admin/reset")
        assert reset.status_code == 200
        assert reset.json() != {"message": "Reset called"}

    def test_unknown_acquirer_passes_through_404_without_alert(
        self, live_client: TestClient
    ) -> None:
        with live_client.websocket_connect("/ws/telemetry") as ws:
            ws.receive_text()  # bootstrap
            resp = live_client.post("/api/simulator/acquirers/acquirer_nope/outage", json=OUTAGE_ON)
            assert resp.status_code == 404
            assert next_frame_after_ping(ws)["event_type"] == "PONG"


class TestSimulatorProxyUnreachable:
    """An unreachable simulator is a 502, never a fabricated success (AUDIT F-18)."""

    def test_toggle_outage_returns_502_and_does_not_alert(self, offline_client: TestClient) -> None:
        with offline_client.websocket_connect("/ws/telemetry") as ws:
            ws.receive_text()  # bootstrap
            resp = offline_client.post(
                "/api/simulator/acquirers/acquirer_alpha/outage", json=OUTAGE_ON
            )
            assert resp.status_code == 502
            assert "outage_active" not in resp.json()
            assert next_frame_after_ping(ws)["event_type"] == "PONG"

    def test_success_rate_returns_502(self, offline_client: TestClient) -> None:
        resp = offline_client.post(
            "/api/simulator/acquirers/acquirer_alpha/success-rate",
            json={"success_rate": 0.60, "reason": "Brownout Test"},
        )
        assert resp.status_code == 502
        assert "effective_success_rate" not in resp.json()

    def test_states_returns_502(self, offline_client: TestClient) -> None:
        assert offline_client.get("/api/simulator/admin/states").status_code == 502

    def test_reset_returns_502(self, offline_client: TestClient) -> None:
        assert offline_client.post("/api/simulator/admin/reset").status_code == 502
