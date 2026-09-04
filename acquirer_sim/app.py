"""FastAPI application factory and route declarations for simulated acquirer service."""

from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse

from acquirer_sim.models import (
    AcquirerConfig,
    AdminStateResponse,
    AuthorizeRequest,
    AuthorizeResponse,
    LatencyConfig,
    MultiAdminStateResponse,
    OutageToggleRequest,
    ResetResponse,
    SuccessRateUpdateRequest,
)
from acquirer_sim.simulator import (
    AcquirerOutageHttpException,
    AcquirerSimulator,
    MultiAcquirerSimulator,
)


def create_app(
    default_acquirers: list[str] | None = None,
    default_base_rate: float = 0.95,
    default_latency: LatencyConfig | None = None,
    seed: int | None = None,
) -> FastAPI:
    """Create and configure a FastAPI application instance representing one or more acquirers."""
    app = FastAPI(
        title="Loom Simulated Acquirer Service",
        description=(
            "Scriptable payment acquirer simulator with configurable success rates and outages."
        ),
        version="0.1.0",
    )

    # Initialize simulation registry (defaulting to standard tri-acquirer topology if none)
    initial_acquirers = default_acquirers or ["acquirer_alpha", "acquirer_beta", "acquirer_gamma"]
    registry = MultiAcquirerSimulator(
        default_acquirers=initial_acquirers,
        default_base_rate=default_base_rate,
        default_latency=default_latency,
        seed=seed,
    )

    # Attach registry to application state for inspection
    app.state.registry = registry

    # -------------------------------------------------------------------------
    # Exception Handlers
    # -------------------------------------------------------------------------

    @app.exception_handler(AcquirerOutageHttpException)
    async def outage_http_exception_handler(
        _request: Request, exc: AcquirerOutageHttpException
    ) -> JSONResponse:
        """Handle HTTP 503 outage exceptions from simulator."""
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "detail": exc.message,
                "acquirer_id": exc.acquirer_id,
            },
        )

    @app.exception_handler(ValueError)
    async def value_error_handler(_request: Request, exc: ValueError) -> JSONResponse:
        """Handle validation and state constraint errors with HTTP 422."""
        return JSONResponse(
            status_code=422,
            content={"detail": str(exc)},
        )

    # -------------------------------------------------------------------------
    # Dependency Helper: Acquirer Resolution
    # -------------------------------------------------------------------------

    def resolve_simulator(
        acquirer_id_param: str | None = None,
        x_acquirer_id: str | None = Header(default=None, alias="X-Acquirer-Id"),
        query_acquirer_id: str | None = Query(default=None, alias="acquirer_id"),
    ) -> AcquirerSimulator:
        """Resolve the target AcquirerSimulator instance from path, header, query, or defaults."""
        target_id = acquirer_id_param or x_acquirer_id or query_acquirer_id

        if not target_id:
            all_ids = registry.list_acquirer_ids()
            if len(all_ids) == 1:
                target_id = all_ids[0]
            else:
                target_id = "acquirer_alpha"

        sim = registry.get(target_id)
        if sim is None:
            # Auto-register unknown acquirers on demand with default configuration
            sim = registry.get_or_create(target_id)
        return sim

    # -------------------------------------------------------------------------
    # Health & Discovery Endpoints
    # -------------------------------------------------------------------------

    @app.get("/health", tags=["Operational"])
    async def get_health() -> dict[str, Any]:
        """Return operational health status and registered acquirer identifiers."""
        return {
            "status": "healthy",
            "timestamp": time.time(),
            "registered_acquirers": registry.list_acquirer_ids(),
        }

    @app.get("/acquirers", tags=["Operational"])
    async def list_acquirers() -> list[str]:
        """Return list of all registered acquirer identifiers."""
        return registry.list_acquirer_ids()

    @app.post("/acquirers", tags=["Operational"], status_code=status.HTTP_201_CREATED)
    async def register_acquirer(config: AcquirerConfig) -> AdminStateResponse:
        """Register a new acquirer instance with custom initial configuration."""
        try:
            sim = registry.register(config)
            return sim.get_telemetry_snapshot()
        except ValueError as err:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(err),
            ) from err

    # -------------------------------------------------------------------------
    # Authorization Endpoints
    # -------------------------------------------------------------------------

    @app.post("/authorize", tags=["Authorization"], response_model=AuthorizeResponse)
    async def authorize_transaction(
        request: AuthorizeRequest,
        x_acquirer_id: str | None = Header(default=None, alias="X-Acquirer-Id"),
        query_acquirer_id: str | None = Query(default=None, alias="acquirer_id"),
    ) -> AuthorizeResponse:
        """Execute a payment authorization against the resolved acquirer."""
        target_id = request.acquirer_id or x_acquirer_id or query_acquirer_id
        sim = resolve_simulator(acquirer_id_param=target_id)
        return await sim.execute_authorization(request)

    @app.post(
        "/acquirers/{acquirer_id}/authorize",
        tags=["Authorization"],
        response_model=AuthorizeResponse,
    )
    async def authorize_transaction_keyed(
        acquirer_id: str,
        request: AuthorizeRequest,
    ) -> AuthorizeResponse:
        """Execute a payment authorization against a specific keyed acquirer."""
        sim = resolve_simulator(acquirer_id_param=acquirer_id)
        return await sim.execute_authorization(request)

    # -------------------------------------------------------------------------
    # Admin Control Plane Endpoints
    # -------------------------------------------------------------------------

    @app.post(
        "/admin/success-rate",
        tags=["Admin Control"],
        response_model=AdminStateResponse,
    )
    async def update_success_rate(
        payload: SuccessRateUpdateRequest,
        query_acquirer_id: str | None = Query(default=None, alias="acquirer_id"),
    ) -> AdminStateResponse:
        """Update live base success rate for the target acquirer."""
        target_id = payload.acquirer_id or query_acquirer_id
        sim = resolve_simulator(acquirer_id_param=target_id)
        sim.set_success_rate(payload.success_rate)
        return sim.get_telemetry_snapshot()

    @app.post(
        "/acquirers/{acquirer_id}/admin/success-rate",
        tags=["Admin Control"],
        response_model=AdminStateResponse,
    )
    async def update_success_rate_keyed(
        acquirer_id: str,
        payload: SuccessRateUpdateRequest,
    ) -> AdminStateResponse:
        """Update live base success rate for a specific keyed acquirer."""
        sim = resolve_simulator(acquirer_id_param=acquirer_id)
        sim.set_success_rate(payload.success_rate)
        return sim.get_telemetry_snapshot()

    @app.post(
        "/admin/outage",
        tags=["Admin Control"],
        response_model=AdminStateResponse,
    )
    async def toggle_outage(
        payload: OutageToggleRequest,
        query_acquirer_id: str | None = Query(default=None, alias="acquirer_id"),
    ) -> AdminStateResponse:
        """Toggle outage state on or off for the target acquirer."""
        target_id = payload.acquirer_id or query_acquirer_id
        sim = resolve_simulator(acquirer_id_param=target_id)
        sim.set_outage(
            active=payload.active,
            behavior=payload.behavior,
            transition_seconds=payload.transition_seconds,
        )
        return sim.get_telemetry_snapshot()

    @app.post(
        "/acquirers/{acquirer_id}/admin/outage",
        tags=["Admin Control"],
        response_model=AdminStateResponse,
    )
    async def toggle_outage_keyed(
        acquirer_id: str,
        payload: OutageToggleRequest,
    ) -> AdminStateResponse:
        """Toggle outage state on or off for a specific keyed acquirer."""
        sim = resolve_simulator(acquirer_id_param=acquirer_id)
        sim.set_outage(
            active=payload.active,
            behavior=payload.behavior,
            transition_seconds=payload.transition_seconds,
        )
        return sim.get_telemetry_snapshot()

    @app.get(
        "/admin/state",
        tags=["Admin Control"],
        response_model=AdminStateResponse,
    )
    async def get_acquirer_state(
        x_acquirer_id: str | None = Header(default=None, alias="X-Acquirer-Id"),
        query_acquirer_id: str | None = Query(default=None, alias="acquirer_id"),
    ) -> AdminStateResponse:
        """Return point-in-time telemetry snapshot for the resolved acquirer."""
        sim = resolve_simulator(x_acquirer_id=x_acquirer_id, query_acquirer_id=query_acquirer_id)
        return sim.get_telemetry_snapshot()

    @app.get(
        "/acquirers/{acquirer_id}/admin/state",
        tags=["Admin Control"],
        response_model=AdminStateResponse,
    )
    async def get_acquirer_state_keyed(
        acquirer_id: str,
    ) -> AdminStateResponse:
        """Return point-in-time telemetry snapshot for a specific keyed acquirer."""
        sim = resolve_simulator(acquirer_id_param=acquirer_id)
        return sim.get_telemetry_snapshot()

    @app.get(
        "/admin/states",
        tags=["Admin Control"],
        response_model=MultiAdminStateResponse,
    )
    async def get_all_acquirer_states() -> MultiAdminStateResponse:
        """Return telemetry snapshots across all registered acquirers."""
        snapshots = registry.get_all_telemetry()
        return MultiAdminStateResponse(
            acquirers=snapshots,
            total_acquirers=len(snapshots),
        )

    @app.post(
        "/admin/reset",
        tags=["Admin Control"],
        response_model=ResetResponse,
    )
    async def reset_telemetry(
        query_acquirer_id: str | None = Query(default=None, alias="acquirer_id"),
    ) -> ResetResponse:
        """Reset telemetry counters and restore default configuration."""
        if query_acquirer_id:
            sim = resolve_simulator(acquirer_id_param=query_acquirer_id)
            return sim.reset()

        registry.reset_all()
        return ResetResponse(
            acquirer_id="all",
            message="All acquirers reset and default states restored",
            timestamp=time.time(),
        )

    @app.post(
        "/acquirers/{acquirer_id}/admin/reset",
        tags=["Admin Control"],
        response_model=ResetResponse,
    )
    async def reset_telemetry_keyed(
        acquirer_id: str,
    ) -> ResetResponse:
        """Reset telemetry counters and restore default configuration for a keyed acquirer."""
        sim = resolve_simulator(acquirer_id_param=acquirer_id)
        return sim.reset()

    return app
