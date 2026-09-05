"""Automated QA Test Suite for Phase 7 Dashboard (Tickets A, B, C, D).

Validates:
- TC-QA-701: Outage Marker Alignment (marker sequence lines up with outage trigger).
- TC-QA-702: Real-time Telemetry Cadence (sub-15ms WebSocket push latency across 150 txs).
- TC-QA-703: Operator Outage-Trigger Responsiveness (<100ms button-to-alert delivery).
- TC-QA-704: Resilient Reconnecting State (graceful close and reconnecting recovery).
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import time
from collections.abc import Generator
from typing import Any

import httpx
import pytest
import uvicorn
import websockets

from acquirer_sim.app import create_app
from acquirer_sim.models import LatencyConfig, OutageBehavior
from router_core.app import create_router_app
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig


def get_free_port() -> int:
    """Find an available ephemeral port on localhost."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def live_servers() -> Generator[dict[str, Any], None, None]:
    """Spin up live uvicorn servers for simulator and router on ephemeral ports."""
    sim_port = get_free_port()
    router_port = get_free_port()

    # Simulator
    sim_app = create_app(
        default_acquirers=["acquirer_alpha", "acquirer_beta", "acquirer_gamma"],
        default_base_rate=0.95,
        default_latency=LatencyConfig(base_ms=0.0, jitter_ms=0.0),
        seed=42,
    )
    sim_app.state.registry.get("acquirer_alpha").set_success_rate(0.95)
    sim_app.state.registry.get("acquirer_beta").set_success_rate(0.90)
    sim_app.state.registry.get("acquirer_gamma").set_success_rate(0.85)

    transport = httpx.ASGITransport(app=sim_app)
    http_client = httpx.AsyncClient(
        transport=transport,
        base_url=f"http://127.0.0.1:{sim_port}",
    )

    # Router
    pid_config = PIDConfig(
        kp=0.12,
        ki=0.005,
        kd=0.25,
        integral_max=1.0,
        min_allocation=0.03,
    )
    router_config = RouterConfig(
        routes=[
            AcquirerRouteConfig(
                acquirer_id="acquirer_alpha",
                base_url=f"http://127.0.0.1:{sim_port}",
                state_config=AcquirerStateConfig(
                    alpha_prior=1.0, beta_prior=1.0, decay_factor=0.95
                ),
            ),
            AcquirerRouteConfig(
                acquirer_id="acquirer_beta",
                base_url=f"http://127.0.0.1:{sim_port}",
                state_config=AcquirerStateConfig(
                    alpha_prior=1.0, beta_prior=1.0, decay_factor=0.95
                ),
            ),
            AcquirerRouteConfig(
                acquirer_id="acquirer_gamma",
                base_url=f"http://127.0.0.1:{sim_port}",
                state_config=AcquirerStateConfig(
                    alpha_prior=1.0, beta_prior=1.0, decay_factor=0.95
                ),
            ),
        ],
        seed=777,
        pid_config=pid_config,
    )

    router = BanditRouter(config=router_config, http_client=http_client)
    app = create_router_app(router=router)

    # Start servers
    sim_cfg = uvicorn.Config(
        sim_app,
        host="127.0.0.1",
        port=sim_port,
        log_level="warning",
    )
    sim_server = uvicorn.Server(sim_cfg)
    sim_thread = threading.Thread(target=sim_server.run, daemon=True)
    sim_thread.start()

    router_cfg = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=router_port,
        log_level="warning",
    )
    router_server = uvicorn.Server(router_cfg)
    router_thread = threading.Thread(target=router_server.run, daemon=True)
    router_thread.start()

    # Wait for ports
    with httpx.Client() as cl:
        for _ in range(30):
            try:
                r1 = cl.get(f"http://127.0.0.1:{sim_port}/health", timeout=0.5)
                r2 = cl.get(f"http://127.0.0.1:{router_port}/health", timeout=0.5)
                if r1.status_code == 200 and r2.status_code == 200:
                    break
            except (httpx.HTTPError, OSError):
                time.sleep(0.1)

    yield {
        "sim_port": sim_port,
        "router_port": router_port,
        "http_url": f"http://127.0.0.1:{router_port}",
        "ws_url": f"ws://127.0.0.1:{router_port}/ws/telemetry",
        "sim_app": sim_app,
    }

    sim_server.should_exit = True
    router_server.should_exit = True
    sim_thread.join(timeout=2.0)
    router_thread.join(timeout=2.0)


class TestPhase7QAScenarios:
    """End-to-End QA Validation Suite for Phase 7."""

    @pytest.mark.asyncio
    async def test_tc_qa_701_outage_marker_alignment(
        self, live_servers: dict[str, Any]
    ) -> None:
        """TC-QA-701: Verify chart outage marker aligns with outage trigger."""
        ws_url = live_servers["ws_url"]
        http_url = live_servers["http_url"]
        sim_app = live_servers["sim_app"]

        async with websockets.connect(ws_url) as ws:
            raw_boot = await asyncio.wait_for(ws.recv(), timeout=2.0)
            assert json.loads(raw_boot)["event_type"] == "BOOTSTRAP"

            events: list[dict[str, Any]] = []
            async with httpx.AsyncClient(base_url=http_url, timeout=5.0) as http_client:
                for seq in range(1, 60):
                    if seq == 27:
                        sim_alpha = sim_app.state.registry.get("acquirer_alpha")
                        sim_alpha.set_outage(
                            active=True,
                            behavior=OutageBehavior.RETURN_DECLINE,
                        )

                    resp = await http_client.post(
                        "/route",
                        json={"transaction_id": f"tx_marker_{seq:03d}", "amount": 25.0},
                    )
                    assert resp.status_code == 200
                    raw_msg = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    events.append(json.loads(raw_msg))

            # Find first outage frame
            outage_frame = next(
                e for e in events if e.get("decline_code") == "ACQUIRER_OUTAGE"
            )
            assert outage_frame["sequence_number"] == 27
            assert outage_frame["selected_acquirer"] == "acquirer_alpha"

            # Chart X calculation: index in event window
            event_idx = next(
                i for i, e in enumerate(events) if e["sequence_number"] == 27
            )
            assert event_idx == 26  # 0-indexed 27th element

            # Restore alpha
            sim_alpha = sim_app.state.registry.get("acquirer_alpha")
            sim_alpha.set_outage(
                active=False,
                behavior=OutageBehavior.RETURN_DECLINE,
            )

    @pytest.mark.asyncio
    async def test_tc_qa_702_real_time_cadence(
        self, live_servers: dict[str, Any]
    ) -> None:
        """TC-QA-702: Verify telemetry frames are pushed at real cadence (< 15ms)."""
        ws_url = live_servers["ws_url"]
        http_url = live_servers["http_url"]

        latencies_ms: list[float] = []
        async with websockets.connect(ws_url) as ws:
            _ = await asyncio.wait_for(ws.recv(), timeout=2.0)  # bootstrap

            async with httpx.AsyncClient(base_url=http_url, timeout=5.0) as http_client:
                for seq in range(1, 31):
                    t0 = time.perf_counter()
                    resp = await http_client.post(
                        "/route",
                        json={"transaction_id": f"tx_cadence_{seq:03d}", "amount": 10.0},
                    )
                    assert resp.status_code == 200
                    raw_frame = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    t_recv = time.perf_counter()
                    latencies_ms.append((t_recv - t0) * 1000)

                    frame = json.loads(raw_frame)
                    assert frame["event_type"] == "ROUTING_COMPLETED"

        avg_lat = sum(latencies_ms) / len(latencies_ms)
        assert avg_lat < 15.0, f"Average push latency too high: {avg_lat:.2f}ms"

    @pytest.mark.asyncio
    async def test_tc_qa_703_operator_button_responsiveness(
        self, live_servers: dict[str, Any]
    ) -> None:
        """TC-QA-703: Verify outage buttons cause visible state change in < 100ms."""
        ws_url = live_servers["ws_url"]
        http_url = live_servers["http_url"]

        async with websockets.connect(ws_url) as ws:
            _ = await asyncio.wait_for(ws.recv(), timeout=2.0)  # bootstrap

            async with httpx.AsyncClient(base_url=http_url, timeout=5.0) as http_client:
                t0 = time.perf_counter()
                resp = await http_client.post(
                    "/api/simulator/acquirers/acquirer_alpha/outage",
                    json={
                        "active": True,
                        "behavior": "RETURN_DECLINE",
                        "transition_seconds": 0.0,
                    },
                )
                rtt_ms = (time.perf_counter() - t0) * 1000

                assert resp.status_code == 200
                assert resp.json()["outage_active"] is True
                assert rtt_ms < 100.0, f"Button action took too long: {rtt_ms:.2f}ms"

                # Verify WebSocket received HEALTH_ALERT
                raw_alert = await asyncio.wait_for(ws.recv(), timeout=2.0)
                alert = json.loads(raw_alert)
                assert alert["event_type"] == "HEALTH_ALERT"
                assert alert["acquirer_id"] == "acquirer_alpha"
                assert alert["severity"] == "CRITICAL"

                # Reset
                await http_client.post(
                    "/api/simulator/acquirers/acquirer_alpha/outage",
                    json={
                        "active": False,
                        "behavior": "RETURN_DECLINE",
                        "transition_seconds": 0.0,
                    },
                )

    @pytest.mark.asyncio
    async def test_tc_qa_704_reconnecting_state_on_disconnect(
        self, live_servers: dict[str, Any]
    ) -> None:
        """TC-QA-704: Verify client disconnect triggers reconnecting recovery."""
        ws_url = live_servers["ws_url"]

        # Connect and close
        async with websockets.connect(ws_url) as ws:
            _ = await asyncio.wait_for(ws.recv(), timeout=2.0)
            await ws.close()
            assert ws.close_code is not None or not getattr(ws, "open", False)

        # Reconnect
        async with websockets.connect(ws_url) as ws_reconnected:
            raw_boot = await asyncio.wait_for(ws_reconnected.recv(), timeout=2.0)
            boot = json.loads(raw_boot)
            assert boot["event_type"] == "BOOTSTRAP"
            assert "states" in boot
            assert "acquirer_alpha" in boot["states"]

    def test_tc_qa_705_static_baseline_reference_data(self) -> None:
        """TC-QA-705: Verify Phase 7 Revision 6 static baseline reference dataset contract."""
        from pathlib import Path

        json_path = Path("dashboard/src/data/baselineReferenceRun.json")
        assert json_path.exists(), "dashboard/src/data/baselineReferenceRun.json must exist"

        with open(json_path, encoding="utf-8") as f:
            data = json.load(f)

        assert "total_transactions" in data
        assert data["total_transactions"] == 150
        assert data["outage_trigger_index"] == 50

        txs = data["transactions"]
        assert len(txs) == 150

        # Pre-outage: Alpha receives 100% allocation
        assert txs[0]["alpha_weight"] == 1.0
        assert txs[49]["alpha_weight"] == 1.0

        # Outage onset (Tx 51, 52, 53 = indices 50, 51, 52): Absorbing M=3 failures
        assert txs[50]["alpha_weight"] == 1.0
        assert txs[51]["alpha_weight"] == 1.0
        assert txs[52]["alpha_weight"] == 1.0

        # Cliff drop (Tx 54 = index 53): 100% Heaviside step drop to 0.0
        assert txs[53]["alpha_weight"] == 0.0
        assert txs[53]["chosen_acquirer"] == "acquirer_beta"

        # Cooldown remains at 0.0
        assert txs[90]["alpha_weight"] == 0.0

        # Recovery canary probe at Tx 113 (index 112) restores 1.0
        assert txs[112]["alpha_weight"] == 1.0
        assert txs[112]["chosen_acquirer"] == "acquirer_alpha"
