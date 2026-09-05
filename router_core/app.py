"""FastAPI application factory and route declarations for the bandit router service."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from acquirer_sim.models import (
    AuthorizeRequest,
    OutageToggleRequest,
    SuccessRateUpdateRequest,
)
from router_core.models import AcquirerRouteConfig, RouterConfig, RoutingResult
from router_core.pid import PIDConfig
from router_core.router import BanditRouter
from router_core.state import AcquirerStateSnapshot

logger = logging.getLogger("loom.router_core.app")


def create_router_app(
    config: RouterConfig | None = None,
    router: BanditRouter | None = None,
) -> FastAPI:
    """Create and configure a FastAPI application instance hosting the BanditRouter."""
    if router is not None:
        active_router = router
    elif config is not None:
        active_router = BanditRouter(config=config)
    else:
        # Default fallback topology pointing to standard acquirer simulator ports
        default_routes = [
            AcquirerRouteConfig(
                acquirer_id="acquirer_alpha",
                base_url="http://127.0.0.1:8001",
            ),
            AcquirerRouteConfig(
                acquirer_id="acquirer_beta",
                base_url="http://127.0.0.1:8001",
            ),
            AcquirerRouteConfig(
                acquirer_id="acquirer_gamma",
                base_url="http://127.0.0.1:8001",
            ),
        ]
        active_router = BanditRouter(
            config=RouterConfig(
                routes=default_routes,
                pid_config=PIDConfig(),
            )
        )

    active_websockets: set[WebSocket] = set()
    ws_sequence_number = 0
    ws_lock = asyncio.Lock()

    async def broadcast_payload(payload: dict[str, Any]) -> None:
        """Broadcast JSON payload to all active WebSocket clients non-blockingly."""
        if not active_websockets:
            return
        message = json.dumps(payload, default=str)
        dead_websockets: set[WebSocket] = set()
        for ws in list(active_websockets):
            try:
                await ws.send_text(message)
            except (WebSocketDisconnect, RuntimeError, ConnectionResetError, OSError):
                dead_websockets.add(ws)
        active_websockets.difference_update(dead_websockets)

    async def broadcast_routing_result(result: RoutingResult) -> None:
        """Broadcast a routing result envelope to connected WebSocket subscribers."""
        nonlocal ws_sequence_number
        async with ws_lock:
            ws_sequence_number += 1
            seq = ws_sequence_number

        weight: float = 1.0
        if result.smoothed_allocation and result.selected_acquirer in result.smoothed_allocation:
            weight = result.smoothed_allocation[result.selected_acquirer]

        decline_code: str | None = None
        if result.response_payload is not None:
            decline_code = result.response_payload.decline_code
        elif result.error_message is not None:
            decline_code = result.error_message

        snap = result.state_snapshot
        updated_state = {
            "alpha": snap.alpha,
            "beta": snap.beta,
            "health_score": snap.health_score,
            "expected_success_rate": snap.expected_success_rate,
            "success_count": float(snap.success_count),
            "failure_count": float(snap.failure_count),
            "total_count": float(snap.total_count),
        }

        event_payload = {
            "event_type": "ROUTING_COMPLETED",
            "sequence_number": seq,
            "timestamp": result.timestamp,
            "transaction_id": result.transaction_id,
            "selected_acquirer": result.selected_acquirer,
            "status": result.status,
            "authorized": result.authorized,
            "success": result.success,
            "decline_code": decline_code,
            "routing_latency_ms": result.routing_latency_ms,
            "acquirer_latency_ms": result.acquirer_latency_ms,
            "total_latency_ms": result.total_latency_ms,
            "thompson_samples": result.thompson_samples,
            "target_allocation": result.target_allocation,
            "smoothed_allocation": result.smoothed_allocation,
            "allocation_weight": weight,
            "updated_state": updated_state,
        }
        await broadcast_payload(event_payload)

    async def _redis_forwarder_loop() -> None:
        """Forward Redis Pub/Sub messages to WebSockets if data layer is active."""
        try:
            from data_layer.redis_pubsub import AsyncEventSubscriber

            async with AsyncEventSubscriber(channels=["events:routing", "events:health"]) as sub:
                async for event in sub.listen():
                    if active_websockets:
                        await broadcast_payload(event.model_dump())
        except (ConnectionError, OSError, TimeoutError) as exc:
            logger.debug("Redis forwarder loop idle or stopped: %s", exc)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
        """Manage router lifecycle, HTTP connection pool, and WebSocket forwarders."""
        await active_router.start()
        redis_task = asyncio.create_task(_redis_forwarder_loop())
        yield
        redis_task.cancel()
        try:
            await redis_task
        except asyncio.CancelledError:
            pass
        await active_router.close()

    app = FastAPI(
        title="Loom Bandit Router Service",
        description=(
            "Dynamic payment router using Thompson Sampling over decaying Beta distributions."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    # Enable CORS for dashboard UI
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.state.router = active_router
    app.state.active_websockets = active_websockets

    # -------------------------------------------------------------------------
    # Exception Handlers
    # -------------------------------------------------------------------------

    @app.exception_handler(ValueError)
    async def value_error_handler(_request: Request, exc: ValueError) -> JSONResponse:
        """Handle validation and routing errors with HTTP 422."""
        return JSONResponse(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            content={"detail": str(exc)},
        )

    @app.exception_handler(KeyError)
    async def key_error_handler(_request: Request, exc: KeyError) -> JSONResponse:
        """Handle unknown acquirer lookup errors with HTTP 404."""
        return JSONResponse(
            status_code=status.HTTP_404_NOT_FOUND,
            content={"detail": str(exc)},
        )

    # -------------------------------------------------------------------------
    # Routing Endpoints
    # -------------------------------------------------------------------------

    @app.post(
        "/route",
        tags=["Routing"],
        response_model=RoutingResult,
        status_code=status.HTTP_200_OK,
    )
    async def route_transaction(request: AuthorizeRequest) -> RoutingResult:
        """Execute Thompson Sampling route selection, dispatch to acquirer, and update state."""
        result = await active_router.route(request)
        await broadcast_routing_result(result)
        return result

    @app.get("/health", tags=["Operational"])
    async def get_health() -> dict[str, Any]:
        """Return router health status and registered route identifiers."""
        return {
            "status": "healthy",
            "timestamp": time.time(),
            "registered_acquirers": active_router.list_acquirer_ids(),
            "active_websockets": len(active_websockets),
        }

    @app.get(
        "/state",
        tags=["Observability"],
        response_model=dict[str, AcquirerStateSnapshot],
    )
    async def get_all_states() -> dict[str, AcquirerStateSnapshot]:
        """Return live belief and health snapshots across all candidate routes."""
        return active_router.get_all_states()

    @app.get(
        "/state/{acquirer_id}",
        tags=["Observability"],
        response_model=AcquirerStateSnapshot,
    )
    async def get_route_state(acquirer_id: str) -> AcquirerStateSnapshot:
        """Return live belief and health snapshot for a specific acquirer route."""
        try:
            return active_router.get_state(acquirer_id)
        except KeyError as err:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Acquirer '{acquirer_id}' not found",
            ) from err

    # -------------------------------------------------------------------------
    # WebSocket Streaming Endpoint (Phase 7 Ticket D)
    # -------------------------------------------------------------------------

    @app.websocket("/ws/telemetry")
    async def websocket_telemetry_endpoint(websocket: WebSocket) -> None:
        """Stream real-time RoutingEvents and HealthAlertEvents to browser."""
        await websocket.accept()
        active_websockets.add(websocket)
        logger.info("Dashboard WebSocket client connected from %s", websocket.client)

        # 1. Send initial cold-start bootstrap snapshot
        states_dict: dict[str, Any] = {}
        for aid, snap in active_router.get_all_states().items():
            states_dict[aid] = {
                "acquirer_id": snap.acquirer_id,
                "alpha": snap.alpha,
                "beta": snap.beta,
                "health_score": snap.health_score,
                "expected_success_rate": snap.expected_success_rate,
                "success_count": snap.success_count,
                "failure_count": snap.failure_count,
                "total_count": snap.total_count,
                "last_updated_at": snap.last_updated_at,
            }

        bootstrap_payload = {
            "event_type": "BOOTSTRAP",
            "timestamp": time.time(),
            "states": states_dict,
            "registered_acquirers": active_router.list_acquirer_ids(),
        }
        await websocket.send_text(json.dumps(bootstrap_payload))

        try:
            while True:
                data = await websocket.receive_text()
                if data == "ping":
                    await websocket.send_text(
                        json.dumps({"event_type": "PONG", "timestamp": time.time()})
                    )
        except (WebSocketDisconnect, ConnectionResetError):
            logger.info("Dashboard WebSocket client disconnected")
        except (RuntimeError, OSError) as exc:
            logger.debug("WebSocket client dropped: %s", exc)
        finally:
            active_websockets.discard(websocket)

    # -------------------------------------------------------------------------
    # Simulator Proxy Endpoints (Phase 7 Ticket C)
    # -------------------------------------------------------------------------

    @app.post(
        "/api/simulator/acquirers/{acquirer_id}/outage",
        tags=["Simulator Proxy"],
    )
    async def proxy_toggle_outage(
        acquirer_id: str,
        payload: OutageToggleRequest,
    ) -> Any:
        """Proxy outage toggle request to target acquirer simulator."""
        route = next((r for r in active_router.config.routes if r.acquirer_id == acquirer_id), None)
        target_base = route.base_url if route else "http://127.0.0.1:8001"
        url = f"{target_base.rstrip('/')}/acquirers/{acquirer_id}/admin/outage"

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, json=payload.model_dump())
                if resp.status_code != 200:
                    raise HTTPException(status_code=resp.status_code, detail=resp.text)
                data = resp.json()
        except (httpx.HTTPError, OSError) as exc:
            logger.warning("Simulator offline or unreachable at %s: %s", url, exc)
            data = {
                "acquirer_id": acquirer_id,
                "outage_active": payload.active,
                "outage_behavior": (
                    payload.behavior.value
                    if hasattr(payload.behavior, "value")
                    else str(payload.behavior)
                ),
                "simulated_offline": True,
            }

        # Emit health alert to WebSockets immediately
        alert_payload = {
            "event_type": "HEALTH_ALERT",
            "timestamp": time.time(),
            "acquirer_id": acquirer_id,
            "old_health": 1.0 if not payload.active else 0.0,
            "new_health": 0.0 if payload.active else 1.0,
            "severity": "CRITICAL" if payload.active else "INFO",
            "message": f"Outage {'injected' if payload.active else 'cleared'} on {acquirer_id}",
        }
        await broadcast_payload(alert_payload)
        return data

    @app.post(
        "/api/simulator/acquirers/{acquirer_id}/success-rate",
        tags=["Simulator Proxy"],
    )
    async def proxy_success_rate(
        acquirer_id: str,
        payload: SuccessRateUpdateRequest,
    ) -> Any:
        """Proxy success rate update to target acquirer simulator."""
        route = next((r for r in active_router.config.routes if r.acquirer_id == acquirer_id), None)
        target_base = route.base_url if route else "http://127.0.0.1:8001"
        url = f"{target_base.rstrip('/')}/acquirers/{acquirer_id}/admin/success-rate"

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, json=payload.model_dump())
                if resp.status_code != 200:
                    raise HTTPException(status_code=resp.status_code, detail=resp.text)
                return resp.json()
        except (httpx.HTTPError, OSError) as exc:
            logger.warning("Simulator offline or unreachable at %s: %s", url, exc)
            return {
                "acquirer_id": acquirer_id,
                "effective_success_rate": payload.success_rate,
                "simulated_offline": True,
            }

    @app.get("/api/simulator/admin/states", tags=["Simulator Proxy"])
    async def proxy_get_states() -> Any:
        """Proxy telemetry retrieval across all acquirers."""
        target_base = (
            active_router.config.routes[0].base_url
            if active_router.config.routes
            else "http://127.0.0.1:8001"
        )
        url = f"{target_base.rstrip('/')}/admin/states"
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                resp = await client.get(url)
                if resp.status_code == 200:
                    return resp.json()
        except (httpx.HTTPError, OSError):
            pass
        return {"acquirers": {}, "total_acquirers": 0}

    @app.post("/api/simulator/admin/reset", tags=["Simulator Proxy"])
    async def proxy_reset_all() -> Any:
        """Proxy reset telemetry across all acquirers."""
        target_base = (
            active_router.config.routes[0].base_url
            if active_router.config.routes
            else "http://127.0.0.1:8001"
        )
        url = f"{target_base.rstrip('/')}/admin/reset"
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url)
                if resp.status_code == 200:
                    return resp.json()
        except (httpx.HTTPError, OSError) as exc:
            logger.warning("Failed to reset acquirers: %s", exc)
        return {"message": "Reset called"}

    return app


# Default application instance for ASGI servers (e.g. uvicorn router_core.app:app)
app = create_router_app()
