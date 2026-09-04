"""Unit tests for acquirer_sim Pydantic models and serialization schemas."""

import pytest
from pydantic import ValidationError

from acquirer_sim.models import (
    AcquirerConfig,
    AdminStateResponse,
    AuthorizeRequest,
    AuthorizeResponse,
    LatencyConfig,
    OutageBehavior,
    OutageToggleRequest,
    ResetResponse,
    SuccessRateUpdateRequest,
)


class TestLatencyConfig:
    """Tests for LatencyConfig validation."""

    def test_default_values(self) -> None:
        """Verify default latency configuration values."""
        cfg = LatencyConfig()
        assert cfg.base_ms == 20.0
        assert cfg.jitter_ms == 5.0
        assert cfg.outage_spike_ms == 500.0

    def test_negative_values_raise(self) -> None:
        """Verify negative latency parameters are rejected."""
        with pytest.raises(ValidationError):
            LatencyConfig(base_ms=-1.0)
        with pytest.raises(ValidationError):
            LatencyConfig(jitter_ms=-0.5)
        with pytest.raises(ValidationError):
            LatencyConfig(outage_spike_ms=-10.0)


class TestAcquirerConfig:
    """Tests for AcquirerConfig validation."""

    def test_valid_config(self) -> None:
        """Verify valid acquirer config construction."""
        cfg = AcquirerConfig(acquirer_id="acquirer_test", base_success_rate=0.90)
        assert cfg.acquirer_id == "acquirer_test"
        assert cfg.base_success_rate == 0.90
        assert cfg.latency.base_ms == 20.0

    def test_invalid_rate_raises(self) -> None:
        """Verify success rate must be in [0.0, 1.0]."""
        with pytest.raises(ValidationError):
            AcquirerConfig(acquirer_id="test", base_success_rate=-0.01)
        with pytest.raises(ValidationError):
            AcquirerConfig(acquirer_id="test", base_success_rate=1.01)

    def test_empty_id_raises(self) -> None:
        """Verify empty acquirer_id is rejected."""
        with pytest.raises(ValidationError):
            AcquirerConfig(acquirer_id="", base_success_rate=0.90)


class TestAuthorizeRequest:
    """Tests for AuthorizeRequest validation."""

    def test_valid_request(self) -> None:
        """Verify valid authorization request."""
        req = AuthorizeRequest(
            transaction_id="tx_123",
            amount=50.25,
            currency="USD",
        )
        assert req.transaction_id == "tx_123"
        assert req.amount == 50.25
        assert req.currency == "USD"
        assert req.merchant_id == "merchant_loom_default"
        assert req.payment_method == "card"
        assert req.acquirer_id is None

    def test_invalid_amount_raises(self) -> None:
        """Verify zero or negative transaction amounts are rejected."""
        with pytest.raises(ValidationError):
            AuthorizeRequest(transaction_id="tx_1", amount=0.0)
        with pytest.raises(ValidationError):
            AuthorizeRequest(transaction_id="tx_1", amount=-10.0)

    def test_invalid_currency_pattern_raises(self) -> None:
        """Verify currency code must be 3 uppercase letters."""
        with pytest.raises(ValidationError):
            AuthorizeRequest(transaction_id="tx_1", amount=10.0, currency="usd")
        with pytest.raises(ValidationError):
            AuthorizeRequest(transaction_id="tx_1", amount=10.0, currency="US")
        with pytest.raises(ValidationError):
            AuthorizeRequest(transaction_id="tx_1", amount=10.0, currency="USDT")

    def test_extra_fields_forbidden(self) -> None:
        """Verify extra attributes are strictly rejected."""
        with pytest.raises(ValidationError):
            AuthorizeRequest(transaction_id="tx_1", amount=10.0, unknown_field="test")  # type: ignore[call-arg]


class TestAuthorizeResponse:
    """Tests for AuthorizeResponse schema."""

    def test_authorized_response_structure(self) -> None:
        """Verify successful authorization response."""
        res = AuthorizeResponse(
            transaction_id="tx_1",
            acquirer_id="acquirer_alpha",
            status="AUTHORIZED",
            authorized=True,
            authorization_code="AUTH_123456",
            simulated_latency_ms=18.5,
            timestamp=1756972000.0,
        )
        assert res.authorized is True
        assert res.status == "AUTHORIZED"
        assert res.authorization_code == "AUTH_123456"
        assert res.decline_code is None

    def test_declined_response_structure(self) -> None:
        """Verify declined authorization response."""
        res = AuthorizeResponse(
            transaction_id="tx_2",
            acquirer_id="acquirer_alpha",
            status="DECLINED",
            authorized=False,
            decline_code="DO_NOT_HONOR",
            decline_message="Transaction declined by bank",
            simulated_latency_ms=19.0,
            timestamp=1756972000.0,
        )
        assert res.authorized is False
        assert res.status == "DECLINED"
        assert res.decline_code == "DO_NOT_HONOR"


class TestAdminSchemas:
    """Tests for admin endpoint payload schemas."""

    def test_success_rate_update_validation(self) -> None:
        """Verify success rate update payload bounds."""
        req = SuccessRateUpdateRequest(success_rate=0.75, reason="Testing")
        assert req.success_rate == 0.75
        assert req.reason == "Testing"

        with pytest.raises(ValidationError):
            SuccessRateUpdateRequest(success_rate=-0.1)
        with pytest.raises(ValidationError):
            SuccessRateUpdateRequest(success_rate=1.1)

    def test_outage_toggle_request_validation(self) -> None:
        """Verify outage toggle payload defaults and behaviors."""
        req = OutageToggleRequest(active=True)
        assert req.active is True
        assert req.behavior == OutageBehavior.RETURN_DECLINE
        assert req.transition_seconds == 0.0

        custom = OutageToggleRequest(
            active=True,
            behavior=OutageBehavior.HTTP_503,
            acquirer_id="acquirer_beta",
        )
        assert custom.behavior == OutageBehavior.HTTP_503
        assert custom.acquirer_id == "acquirer_beta"

    def test_admin_state_response_serialization(self) -> None:
        """Verify admin state response serialization."""
        state = AdminStateResponse(
            acquirer_id="acquirer_alpha",
            base_success_rate=0.95,
            effective_success_rate=0.95,
            outage_active=False,
            outage_behavior=OutageBehavior.RETURN_DECLINE,
            latency=LatencyConfig(),
            total_requests=100,
            authorized_count=95,
            declined_count=5,
            outage_declines=0,
            empirical_success_rate=0.95,
            uptime_seconds=12.5,
        )
        data = state.model_dump()
        assert data["acquirer_id"] == "acquirer_alpha"
        assert data["empirical_success_rate"] == 0.95

    def test_reset_response(self) -> None:
        """Verify reset response serialization."""
        res = ResetResponse(acquirer_id="acquirer_alpha", message="Reset OK", timestamp=1000.0)
        assert res.acquirer_id == "acquirer_alpha"
