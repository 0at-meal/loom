"""Integration tests for Phase 7 Dashboard backend endpoints in router_core/app.py.

Verifies:
- WebSocket /ws/telemetry cold-start BOOTSTRAP payload
- Simulator outage and success-rate proxy routes with graceful fallback
- Admin reset proxy endpoint
"""

import json

import pytest
from starlette.testclient import TestClient

from acquirer_sim.models import OutageBehavior
from router_core.app import create_router_app
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig


def make_test_router() -> BanditRouter:
    """Create a lightweight test router with two mock routes."""
    config = RouterConfig(
        routes=[
            AcquirerRouteConfig(
                acquirer_id="acquirer_alpha",
                base_url="http://127.0.0.1:8001",
                state_config=AcquirerStateConfig(alpha_prior=1.0, beta_prior=1.0),
            ),
            AcquirerRouteConfig(
                acquirer_id="acquirer_beta",
                base_url="http://127.0.0.1:8002",
                state_config=AcquirerStateConfig(alpha_prior=1.0, beta_prior=1.0),
            ),
        ]
    )
    return BanditRouter(config=config)


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

    def test_proxy_toggle_outage_endpoint(self) -> None:
        """Verify POST /api/simulator/acquirers/{id}/outage forwards or falls back gracefully."""
        router = make_test_router()
        app = create_router_app(router=router)

        with TestClient(app) as client:
            resp = client.post(
                "/api/simulator/acquirers/acquirer_alpha/outage",
                json={
                    "active": True,
                    "behavior": OutageBehavior.RETURN_DECLINE.value,
                    "transition_seconds": 0.0,
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["outage_active"] is True
            assert data["acquirer_id"] == "acquirer_alpha"

    def test_proxy_success_rate_endpoint(self) -> None:
        """Verify POST /api/simulator/acquirers/{id}/success-rate updates base rate."""
        router = make_test_router()
        app = create_router_app(router=router)

        with TestClient(app) as client:
            resp = client.post(
                "/api/simulator/acquirers/acquirer_alpha/success-rate",
                json={
                    "success_rate": 0.60,
                    "reason": "Brownout Test",
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["effective_success_rate"] == pytest.approx(0.60)
            assert data["acquirer_id"] == "acquirer_alpha"

    def test_proxy_admin_reset_endpoint(self) -> None:
        """Verify POST /api/simulator/admin/reset resets simulation harness."""
        router = make_test_router()
        app = create_router_app(router=router)

        with TestClient(app) as client:
            resp = client.post("/api/simulator/admin/reset")
            assert resp.status_code == 200
            assert resp.json()["message"] == "Reset called"
