"""FastAPI application factory and route declarations for the bandit router service."""

from __future__ import annotations

import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.responses import JSONResponse

from acquirer_sim.models import AuthorizeRequest
from router_core.models import AcquirerRouteConfig, RouterConfig, RoutingResult
from router_core.router import BanditRouter
from router_core.state import AcquirerStateSnapshot


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
        active_router = BanditRouter(config=RouterConfig(routes=default_routes))

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
        """Manage router lifecycle and HTTP connection pool."""
        await active_router.start()
        yield
        await active_router.close()

    app = FastAPI(
        title="Loom Bandit Router Service",
        description=(
            "Dynamic payment router using Thompson Sampling over decaying Beta distributions."
        ),
        version="0.1.0",
        lifespan=lifespan,
    )

    app.state.router = active_router

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
        return await active_router.route(request)

    @app.get("/health", tags=["Operational"])
    async def get_health() -> dict[str, Any]:
        """Return router health status and registered route identifiers."""
        return {
            "status": "healthy",
            "timestamp": time.time(),
            "registered_acquirers": active_router.list_acquirer_ids(),
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

    return app
