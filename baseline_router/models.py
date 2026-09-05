"""Data models and contract schemas for the static baseline router."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from router_core.models import AcquirerRouteConfig


class RouteHealthStatus(StrEnum):
    """Operational health state of a static route."""

    HEALTHY = "HEALTHY"  # Operational and eligible for primary priority traffic
    TRIPPED = "TRIPPED"  # Circuit breaker tripped; 0% traffic during cooldown
    PROBATION = "PROBATION"  # Cooldown expired; processing single canary probe transaction


class FailoverThresholdType(StrEnum):
    """Evaluation strategy for static failover tripping."""

    CONSECUTIVE_FAILURES = "CONSECUTIVE_FAILURES"  # Trips after M consecutive failures
    WINDOW_FAILURE_RATE = "WINDOW_FAILURE_RATE"  # Trips if failure rate in last W requests >= tau


class FailoverPolicyConfig(BaseModel):
    """Configuration for circuit breaker thresholds and failover timing."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    threshold_type: FailoverThresholdType = Field(
        default=FailoverThresholdType.CONSECUTIVE_FAILURES,
        description="Mechanism used to detect route failure.",
    )
    consecutive_failure_threshold: int = Field(
        default=3,
        ge=1,
        description="Number of consecutive failures required to trip the circuit breaker (M).",
    )
    window_size: int = Field(
        default=20,
        ge=5,
        description="Sliding window size (W) when threshold_type is WINDOW_FAILURE_RATE.",
    )
    window_failure_rate_threshold: float = Field(
        default=0.20,
        gt=0.0,
        lt=1.0,
        description="Failure rate threshold (tau) when threshold_type is WINDOW_FAILURE_RATE.",
    )
    cooldown_transactions: int = Field(
        default=30,
        ge=1,
        description="Transactions a tripped route must remain dormant before canary probe.",
    )
    failback_mode: Literal["probe", "snapback"] = Field(
        default="probe",
        description=(
            "Recovery policy: 'probe' sends a single canary transaction; "
            "'snapback' shifts 100% of traffic immediately upon cooldown expiry."
        ),
    )


class StaticRouteStateSnapshot(BaseModel):
    """Immutable point-in-time snapshot of an individual static route's operational state."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    acquirer_id: str
    priority: int  # 1-indexed priority rank (1 = Primary, 2 = Secondary, etc.)
    status: RouteHealthStatus
    consecutive_failures: int
    tripped_at_tx: int | None
    success_count: int
    failure_count: int
    total_count: int
    last_updated_at: float

    @property
    def empirical_success_rate(self) -> float:
        """Return observed historical success rate."""
        return self.success_count / self.total_count if self.total_count > 0 else 1.0


class BaselineRouterConfig(BaseModel):
    """Runtime configuration for the StaticBaselineRouter engine."""

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)

    routes: list[AcquirerRouteConfig] = Field(
        ...,
        min_length=1,
        description="List of configured acquirer candidate routes.",
    )
    priority_order: list[str] = Field(
        ...,
        min_length=1,
        description="Ordered list of acquirer IDs defining priority hierarchy.",
    )
    failover_policy: FailoverPolicyConfig = Field(
        default_factory=FailoverPolicyConfig,
        description="Static threshold and cooldown configuration.",
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

    @model_validator(mode="after")
    def validate_priority_order_matches_routes(self) -> BaselineRouterConfig:
        """Validate priority_order and routes match acquirer IDs without duplicates."""
        route_ids = [r.acquirer_id for r in self.routes]
        if len(route_ids) != len(set(route_ids)):
            raise ValueError(f"Duplicate acquirer_ids found in routes: {route_ids}")

        if len(self.priority_order) != len(set(self.priority_order)):
            raise ValueError(
                f"Duplicate acquirer_ids found in priority_order: {self.priority_order}"
            )

        missing_in_routes = set(self.priority_order) - set(route_ids)
        if missing_in_routes:
            raise ValueError(
                f"priority_order contains acquirer_ids not defined in routes: "
                f"{sorted(missing_in_routes)}"
            )

        missing_in_priority = set(route_ids) - set(self.priority_order)
        if missing_in_priority:
            raise ValueError(
                f"routes contains acquirer_ids missing from priority_order: "
                f"{sorted(missing_in_priority)}"
            )

        return self
