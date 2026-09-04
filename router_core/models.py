"""Data models and contract schemas for the bandit router core."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from acquirer_sim.models import AuthorizeResponse
from router_core.state import AcquirerStateConfig, AcquirerStateSnapshot


class AcquirerRouteConfig(BaseModel):
    """Configuration for an individual acquirer route endpoint."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    acquirer_id: str = Field(
        ...,
        min_length=1,
        description="Unique acquirer route identifier matching simulator instance.",
        examples=["acquirer_alpha"],
    )
    base_url: str = Field(
        ...,
        min_length=1,
        description="Base HTTP URL for the acquirer service (e.g. 'http://127.0.0.1:8001').",
        examples=["http://127.0.0.1:8001"],
    )
    auth_path_template: str = Field(
        default="/acquirers/{acquirer_id}/authorize",
        description="Path template for authorization endpoint.",
        examples=["/acquirers/{acquirer_id}/authorize"],
    )
    timeout_sec: float = Field(
        default=2.0,
        gt=0.0,
        description="HTTP request timeout in seconds.",
        examples=[2.0],
    )
    state_config: AcquirerStateConfig = Field(
        default_factory=AcquirerStateConfig,
        description="Bandit prior and decay configuration for this route.",
    )

    def get_authorize_url(self) -> str:
        """Construct full authoritative authorization URL."""
        path = self.auth_path_template.format(acquirer_id=self.acquirer_id)
        return f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"


class RouterConfig(BaseModel):
    """Configuration for the overall BanditRouter engine."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    routes: list[AcquirerRouteConfig] = Field(
        ...,
        min_length=1,
        description="List of configured acquirer candidate routes.",
    )
    max_connections: int = Field(
        default=100,
        gt=0,
        description="Maximum pooled HTTP client connections.",
    )
    max_keepalive_connections: int = Field(
        default=20,
        gt=0,
        description="Maximum idle keepalive HTTP connections.",
    )
    seed: int | None = Field(
        default=None,
        description="Optional seed for Thompson Sampling PRNG (for deterministic testing).",
    )


class RoutingResult(BaseModel):
    """Complete envelope detailing the routing decision, execution outcome, and state mutation."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    transaction_id: str = Field(
        ...,
        description="Transaction identifier echoed from request.",
        examples=["tx_12345678-abcd-ef01-2345-6789abcdef01"],
    )
    selected_acquirer: str = Field(
        ...,
        description="The acquirer selected by Thompson Sampling argmax.",
        examples=["acquirer_alpha"],
    )
    thompson_samples: dict[str, float] = Field(
        ...,
        description="Point-in-time Beta distribution samples drawn for all candidate routes.",
        examples=[{"acquirer_alpha": 0.892, "acquirer_beta": 0.741}],
    )
    status: Literal["AUTHORIZED", "DECLINED", "ERROR"] = Field(
        ...,
        description="Outcome classification status.",
        examples=["AUTHORIZED"],
    )
    authorized: bool = Field(
        ...,
        description="True if payment authorized, False if declined or gateway error.",
        examples=[True],
    )
    success: bool = Field(
        ...,
        description="Binary outcome feedback value x fed back to update the bandit state.",
        examples=[True],
    )
    response_payload: AuthorizeResponse | None = Field(
        default=None,
        description="Raw AuthorizeResponse payload if acquirer returned HTTP 200.",
    )
    error_message: str | None = Field(
        default=None,
        description="Error description if transport failure, timeout, or HTTP 5xx occurred.",
        examples=[None],
    )
    routing_latency_ms: float = Field(
        ...,
        ge=0.0,
        description="Time taken by the router to sample beliefs and select route in ms.",
        examples=[0.05],
    )
    acquirer_latency_ms: float = Field(
        ...,
        ge=0.0,
        description="Round-trip network and processing latency to the acquirer in ms.",
        examples=[22.4],
    )
    total_latency_ms: float = Field(
        ...,
        ge=0.0,
        description="Total end-to-end execution latency in ms.",
        examples=[22.45],
    )
    state_snapshot: AcquirerStateSnapshot = Field(
        ...,
        description="Updated snapshot of the selected acquirer immediately following the outcome.",
    )
    timestamp: float = Field(
        ...,
        description="Epoch timestamp when the routing decision completed.",
        examples=[1756973000.123],
    )
