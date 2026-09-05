"""Static baseline payment router package (Phase 6).

Implements the active-passive priority list with circuit breaker failover,
reproducing herd migration and slow failover failure modes for apples-to-apples
PSR lift comparison against Loom's dynamic router.
"""

from __future__ import annotations

from baseline_router.models import (
    BaselineRouterConfig,
    FailoverPolicyConfig,
    FailoverThresholdType,
    RouteHealthStatus,
    StaticRouteStateSnapshot,
)
from baseline_router.router import StaticBaselineRouter

__all__ = [
    "BaselineRouterConfig",
    "FailoverPolicyConfig",
    "FailoverThresholdType",
    "RouteHealthStatus",
    "StaticBaselineRouter",
    "StaticRouteStateSnapshot",
]
