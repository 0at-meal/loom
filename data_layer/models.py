"""Pydantic v2 domain schemas for Loom Data Layer events and telemetry (Phase 5)."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from router_core.models import RoutingResult


class RoutingEvent(BaseModel):
    """Full point-in-time telemetry payload emitted to Redis Pub/Sub on every transaction."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(
        default_factory=lambda: uuid.uuid4().hex,
        description="Unique event identifier (UUID v4 hex string).",
        examples=["9f2b84c8e71b48b6a386123456789abc"],
    )
    sequence_number: int = Field(
        default=0,
        ge=0,
        description="Monotonic sequential counter for order verification and drop detection.",
        examples=[1],
    )
    event_type: Literal["ROUTING_COMPLETED"] = Field(
        default="ROUTING_COMPLETED",
        description="Event classification discriminator for subscriber routing.",
    )
    timestamp: float = Field(
        ...,
        description="Epoch timestamp (seconds) when routing completed.",
        examples=[1756973000.123],
    )
    transaction_id: str = Field(
        ...,
        description="Transaction identifier echoed from inbound request.",
        examples=["tx_12345678-abcd-ef01-2345-6789abcdef01"],
    )
    selected_acquirer: str = Field(
        ...,
        description="The acquirer route selected by the routing engine.",
        examples=["acquirer_alpha"],
    )
    status: Literal["AUTHORIZED", "DECLINED", "ERROR"] = Field(
        ...,
        description="Outcome classification status.",
        examples=["AUTHORIZED"],
    )
    authorized: bool = Field(
        ...,
        description="True if payment authorized successfully, False otherwise.",
        examples=[True],
    )
    success: bool = Field(
        ...,
        description="Binary outcome feedback fed back into the bandit state.",
        examples=[True],
    )
    decline_code: str | None = Field(
        default=None,
        description="Specific decline or error code if payment was not authorized.",
        examples=["ACQUIRER_OUTAGE"],
    )
    routing_latency_ms: float = Field(
        ...,
        ge=0.0,
        description="Time taken by the router to select arm and compute PID step in ms.",
        examples=[0.12],
    )
    acquirer_latency_ms: float = Field(
        ...,
        ge=0.0,
        description="Network and execution latency to the acquirer in ms.",
        examples=[22.45],
    )
    total_latency_ms: float = Field(
        ...,
        ge=0.0,
        description="Total round-trip transaction latency in ms.",
        examples=[22.57],
    )
    thompson_samples: dict[str, float] = Field(
        ...,
        description="Point-in-time Beta distribution samples drawn for all candidate routes.",
        examples=[{"acquirer_alpha": 0.892, "acquirer_beta": 0.741}],
    )
    target_allocation: dict[str, float] | None = Field(
        default=None,
        description="Raw bandit target allocation vector prior to PID smoothing.",
        examples=[{"acquirer_alpha": 1.0, "acquirer_beta": 0.0}],
    )
    smoothed_allocation: dict[str, float] | None = Field(
        default=None,
        description="Smoothed traffic allocation vector calculated by PID layer.",
        examples=[{"acquirer_alpha": 0.78, "acquirer_beta": 0.22}],
    )
    allocation_weight: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="The instantaneous smoothed allocation weight assigned to the chosen route.",
        examples=[0.78],
    )
    pid_diagnostics: dict[str, Any] | None = Field(
        default=None,
        description="Internal PID calculation diagnostics if PID layer was active.",
    )
    updated_state: dict[str, float] = Field(
        ...,
        description="Updated post-outcome snapshot for selected route (alpha, beta, health_score).",
        examples=[{"alpha": 2.9, "beta": 1.0, "health_score": 1.0, "expected_success_rate": 0.743}],
    )

    @classmethod
    def from_routing_result(
        cls,
        result: RoutingResult,
        sequence_number: int = 0,
    ) -> RoutingEvent:
        """Construct a strongly-typed RoutingEvent envelope from a RoutingResult."""
        # Derive allocation weight of the selected acquirer
        weight: float = 1.0
        if result.smoothed_allocation and result.selected_acquirer in result.smoothed_allocation:
            weight = result.smoothed_allocation[result.selected_acquirer]

        # Extract decline code if present in response payload
        decline_code: str | None = None
        if result.response_payload is not None:
            decline_code = result.response_payload.decline_code
        elif result.error_message is not None:
            decline_code = result.error_message

        # Serialize PID diagnostics if available
        pid_diag: dict[str, Any] | None = None
        if result.pid_diagnostics is not None:
            pid_diag = {
                "error": result.pid_diagnostics.error,
                "p_term": result.pid_diagnostics.p_term,
                "i_term": result.pid_diagnostics.i_term,
                "d_term": result.pid_diagnostics.d_term,
                "raw_delta": result.pid_diagnostics.raw_delta,
                "pre_projection_allocation": result.pid_diagnostics.pre_projection_allocation,
            }

        # Format updated snapshot values
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

        return cls(
            sequence_number=sequence_number,
            timestamp=result.timestamp,
            transaction_id=result.transaction_id,
            selected_acquirer=result.selected_acquirer,
            status=result.status,
            authorized=result.authorized,
            success=result.success,
            decline_code=decline_code,
            routing_latency_ms=result.routing_latency_ms,
            acquirer_latency_ms=result.acquirer_latency_ms,
            total_latency_ms=result.total_latency_ms,
            thompson_samples=result.thompson_samples,
            target_allocation=result.target_allocation,
            smoothed_allocation=result.smoothed_allocation,
            allocation_weight=weight,
            pid_diagnostics=pid_diag,
            updated_state=updated_state,
        )


class HealthAlertEvent(BaseModel):
    """Alert event emitted when an acquirer's health degrades or recovers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str = Field(
        default_factory=lambda: uuid.uuid4().hex,
        description="Unique alert identifier.",
    )
    event_type: Literal["HEALTH_ALERT"] = "HEALTH_ALERT"
    timestamp: float = Field(
        ...,
        description="Epoch timestamp when alert was triggered.",
    )
    acquirer_id: str = Field(
        ...,
        description="Acquirer whose health triggered the alert.",
    )
    old_health: float = Field(
        ...,
        description="Health score prior to the outcome.",
    )
    new_health: float = Field(
        ...,
        description="Health score following the outcome.",
    )
    severity: Literal["INFO", "WARNING", "CRITICAL"] = Field(
        default="WARNING",
        description="Alert severity tier.",
    )
    message: str = Field(
        ...,
        description="Human-readable summary of the health state transition.",
    )
