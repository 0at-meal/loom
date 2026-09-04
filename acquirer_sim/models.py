"""Data models and serialization schemas for the simulated acquirer service."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class OutageBehavior(StrEnum):
    """Failure response behavior when an acquirer outage is active."""

    RETURN_DECLINE = "RETURN_DECLINE"  # HTTP 200 with status=DECLINED (default payment behavior)
    HTTP_503 = "HTTP_503"  # HTTP 503 Service Unavailable (gateway failure)
    LATENCY_SPIKE = "LATENCY_SPIKE"  # Injects massive delay before declining


class LatencyConfig(BaseModel):
    """Configuration for artificial latency injection."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_ms: float = Field(
        default=20.0,
        ge=0.0,
        description="Baseline network and processing latency in milliseconds.",
        examples=[20.0],
    )
    jitter_ms: float = Field(
        default=5.0,
        ge=0.0,
        description="Uniform random jitter (+/- ms) added to baseline latency.",
        examples=[5.0],
    )
    outage_spike_ms: float = Field(
        default=500.0,
        ge=0.0,
        description="Additional latency injected when outage_behavior is LATENCY_SPIKE.",
        examples=[500.0],
    )


class AcquirerConfig(BaseModel):
    """Runtime configuration for a simulated acquirer instance."""

    model_config = ConfigDict(extra="forbid")

    acquirer_id: str = Field(
        ...,
        min_length=1,
        description="Unique identifier for this acquirer instance.",
        examples=["acquirer_alpha"],
    )
    base_success_rate: float = Field(
        default=0.95,
        ge=0.0,
        le=1.0,
        description="Configured target success rate under normal operating conditions.",
        examples=[0.95],
    )
    latency: LatencyConfig = Field(
        default_factory=LatencyConfig,
        description="Latency simulation parameters.",
    )


# ---------------------------------------------------------------------------
# Authorization Contract Schemas
# ---------------------------------------------------------------------------


class AuthorizeRequest(BaseModel):
    """Inbound payment authorization request from router or client."""

    model_config = ConfigDict(extra="forbid")

    transaction_id: str = Field(
        ...,
        min_length=1,
        description="Unique identifier for the transaction (UUID or idempotency key).",
        examples=["tx_987e6543-e21b-12d3-a456-426614174000"],
    )
    amount: float = Field(
        ...,
        gt=0.0,
        description="Transaction amount in major currency units.",
        examples=[100.50],
    )
    currency: str = Field(
        default="USD",
        pattern=r"^[A-Z]{3}$",
        description="ISO 4217 three-letter currency code.",
        examples=["USD"],
    )
    merchant_id: str = Field(
        default="merchant_loom_default",
        min_length=1,
        description="Merchant identifier originating the payment.",
        examples=["merchant_loom_default"],
    )
    payment_method: str = Field(
        default="card",
        min_length=1,
        description="Payment instrument type.",
        examples=["card"],
    )
    acquirer_id: str | None = Field(
        default=None,
        min_length=1,
        description="Optional target acquirer identifier when using shared/multi-tenant endpoint.",
        examples=["acquirer_alpha"],
    )
    timestamp: float | None = Field(
        default=None,
        ge=0.0,
        description="Client epoch timestamp in seconds. Defaults to server time if omitted.",
        examples=[1756972000.123],
    )


class AuthorizeResponse(BaseModel):
    """Outbound payment authorization result."""

    model_config = ConfigDict(extra="forbid")

    transaction_id: str = Field(
        ...,
        description="Transaction identifier echoed from request.",
        examples=["tx_987e6543-e21b-12d3-a456-426614174000"],
    )
    acquirer_id: str = Field(
        ...,
        description="Identifier of the acquirer answering the authorization.",
        examples=["acquirer_alpha"],
    )
    status: Literal["AUTHORIZED", "DECLINED"] = Field(
        ...,
        description="Payment outcome status.",
        examples=["AUTHORIZED"],
    )
    authorized: bool = Field(
        ...,
        description="Direct boolean convenience flag (True if AUTHORIZED, False if DECLINED).",
        examples=[True],
    )
    authorization_code: str | None = Field(
        default=None,
        description="Unique bank auth code generated on success (e.g. 'AUTH_123456').",
        examples=["AUTH_987654"],
    )
    decline_code: str | None = Field(
        default=None,
        description="Categorical reason code on decline (e.g. 'DO_NOT_HONOR', 'ACQUIRER_OUTAGE').",
        examples=[None],
    )
    decline_message: str | None = Field(
        default=None,
        description="Human-readable explanation of decline.",
        examples=[None],
    )
    simulated_latency_ms: float = Field(
        ...,
        ge=0.0,
        description="Actual simulated processing delay applied in milliseconds.",
        examples=[21.4],
    )
    timestamp: float = Field(
        ...,
        description="Server epoch timestamp when authorization was processed.",
        examples=[1756972000.145],
    )


# ---------------------------------------------------------------------------
# Admin API Contract Schemas
# ---------------------------------------------------------------------------


class SuccessRateUpdateRequest(BaseModel):
    """Payload to update the live base success rate."""

    model_config = ConfigDict(extra="forbid")

    success_rate: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="New target success rate in range [0.0, 1.0].",
        examples=[0.85],
    )
    acquirer_id: str | None = Field(
        default=None,
        min_length=1,
        description="Optional acquirer ID target when updating via shared endpoint.",
        examples=["acquirer_alpha"],
    )
    reason: str | None = Field(
        default=None,
        description="Optional audit note explaining the rate adjustment.",
        examples=["Simulating daytime network congestion"],
    )


class OutageToggleRequest(BaseModel):
    """Payload to toggle or update the outage state."""

    model_config = ConfigDict(extra="forbid")

    active: bool = Field(
        ...,
        description="True to engage outage mode, False to restore normal operations.",
        examples=[True],
    )
    acquirer_id: str | None = Field(
        default=None,
        min_length=1,
        description="Optional acquirer ID target when updating via shared endpoint.",
        examples=["acquirer_alpha"],
    )
    behavior: OutageBehavior = Field(
        default=OutageBehavior.RETURN_DECLINE,
        description="Failure mode behavior while outage is active.",
        examples=[OutageBehavior.RETURN_DECLINE],
    )
    transition_seconds: float = Field(
        default=0.0,
        ge=0.0,
        description="Optional duration for gradual degrade. Must be 0.0 for v1.",
        examples=[0.0],
    )


class AdminStateResponse(BaseModel):
    """Telemetry and ground truth state snapshot of an acquirer."""

    model_config = ConfigDict(extra="forbid")

    acquirer_id: str = Field(..., description="Unique acquirer identifier.")
    base_success_rate: float = Field(..., description="Configured baseline success rate.")
    effective_success_rate: float = Field(
        ...,
        description=(
            "Current operational success rate (0.0 if outage is active, else base_success_rate)."
        ),
    )
    outage_active: bool = Field(..., description="Whether outage mode is currently engaged.")
    outage_behavior: OutageBehavior = Field(..., description="Active outage failure behavior.")
    latency: LatencyConfig = Field(..., description="Current latency injection parameters.")
    total_requests: int = Field(..., ge=0, description="Total authorizations received.")
    authorized_count: int = Field(..., ge=0, description="Total authorized transactions.")
    declined_count: int = Field(..., ge=0, description="Total declined transactions.")
    outage_declines: int = Field(
        ...,
        ge=0,
        description="Declines directly caused by active outage.",
    )
    empirical_success_rate: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description=(
            "Empirical lifetime success rate: authorized_count / total_requests (1.0 if total=0)."
        ),
    )
    uptime_seconds: float = Field(..., ge=0.0, description="Seconds since service initialization.")


class MultiAdminStateResponse(BaseModel):
    """Aggregate state response across all registered acquirers."""

    model_config = ConfigDict(extra="forbid")

    acquirers: dict[str, AdminStateResponse] = Field(
        ...,
        description="Map of acquirer_id to individual telemetry snapshot.",
    )
    total_acquirers: int = Field(..., ge=0, description="Total count of registered acquirers.")


class ResetResponse(BaseModel):
    """Response after resetting telemetry counters."""

    model_config = ConfigDict(extra="forbid")

    acquirer_id: str = Field(..., description="Target acquirer identifier.")
    message: str = Field(..., description="Result message.")
    timestamp: float = Field(..., description="Epoch timestamp when reset occurred.")
