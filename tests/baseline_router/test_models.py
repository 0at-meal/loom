"""Unit tests for baseline router configuration models and schemas."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from baseline_router.models import (
    BaselineRouterConfig,
    FailoverPolicyConfig,
    FailoverThresholdType,
    RouteHealthStatus,
    StaticRouteStateSnapshot,
)
from router_core.models import AcquirerRouteConfig


def test_failover_policy_config_defaults() -> None:
    """Test default values and constraints for FailoverPolicyConfig."""
    cfg = FailoverPolicyConfig()
    assert cfg.threshold_type == FailoverThresholdType.CONSECUTIVE_FAILURES
    assert cfg.consecutive_failure_threshold == 3
    assert cfg.window_size == 20
    assert cfg.window_failure_rate_threshold == 0.20
    assert cfg.cooldown_transactions == 30
    assert cfg.failback_mode == "probe"


def test_failover_policy_config_validation() -> None:
    """Test invalid parameter rejection in FailoverPolicyConfig."""
    with pytest.raises(ValidationError):
        FailoverPolicyConfig(consecutive_failure_threshold=0)

    with pytest.raises(ValidationError):
        FailoverPolicyConfig(window_size=2)

    with pytest.raises(ValidationError):
        FailoverPolicyConfig(window_failure_rate_threshold=1.5)

    with pytest.raises(ValidationError):
        FailoverPolicyConfig(cooldown_transactions=0)


def test_baseline_router_config_valid() -> None:
    """Test successful creation of BaselineRouterConfig with matched priority order."""
    routes = [
        AcquirerRouteConfig(acquirer_id="acquirer_alpha", base_url="http://127.0.0.1:8001"),
        AcquirerRouteConfig(acquirer_id="acquirer_beta", base_url="http://127.0.0.1:8002"),
    ]
    cfg = BaselineRouterConfig(
        routes=routes,
        priority_order=["acquirer_alpha", "acquirer_beta"],
    )
    assert len(cfg.routes) == 2
    assert cfg.priority_order == ["acquirer_alpha", "acquirer_beta"]


def test_baseline_router_config_mismatched_priority_order() -> None:
    """Test validation errors on mismatched, duplicate, or missing acquirer IDs."""
    routes = [
        AcquirerRouteConfig(acquirer_id="acquirer_alpha", base_url="http://127.0.0.1:8001"),
        AcquirerRouteConfig(acquirer_id="acquirer_beta", base_url="http://127.0.0.1:8002"),
    ]

    # Missing acquirer_beta in priority_order
    with pytest.raises(ValidationError, match="missing from priority_order"):
        BaselineRouterConfig(
            routes=routes,
            priority_order=["acquirer_alpha"],
        )

    # Extra acquirer_gamma in priority_order
    with pytest.raises(ValidationError, match="not defined in routes"):
        BaselineRouterConfig(
            routes=routes,
            priority_order=["acquirer_alpha", "acquirer_beta", "acquirer_gamma"],
        )

    # Duplicate in priority_order
    with pytest.raises(ValidationError, match="Duplicate acquirer_ids found in priority_order"):
        BaselineRouterConfig(
            routes=routes,
            priority_order=["acquirer_alpha", "acquirer_alpha"],
        )


def test_static_route_state_snapshot_properties() -> None:
    """Test empirical_success_rate property and immutability of snapshot."""
    snap = StaticRouteStateSnapshot(
        acquirer_id="acquirer_alpha",
        priority=1,
        status=RouteHealthStatus.HEALTHY,
        consecutive_failures=0,
        tripped_at_tx=None,
        success_count=18,
        failure_count=2,
        total_count=20,
        last_updated_at=1756973000.0,
    )
    assert snap.empirical_success_rate == 0.90

    empty_snap = StaticRouteStateSnapshot(
        acquirer_id="acquirer_alpha",
        priority=1,
        status=RouteHealthStatus.HEALTHY,
        consecutive_failures=0,
        tripped_at_tx=None,
        success_count=0,
        failure_count=0,
        total_count=0,
        last_updated_at=1756973000.0,
    )
    assert empty_snap.empirical_success_rate == 1.0

    with pytest.raises(ValidationError):
        snap.success_count = 19  # Immutable frozen model
