"""Unit tests for AcquirerState, AcquirerStateConfig, and AcquirerStateSnapshot."""

from __future__ import annotations

import math

import numpy as np
import pytest

from router_core.state import (
    AcquirerState,
    AcquirerStateConfig,
    AcquirerStateSnapshot,
)


class TestAcquirerStateConfig:
    """Tests configuration parameter validation and defaults."""

    def test_default_config_valid(self) -> None:
        """Verify default configuration has expected parameters."""
        config = AcquirerStateConfig()
        assert config.alpha_prior == 1.0
        assert config.beta_prior == 1.0
        assert config.decay_factor == 0.98
        assert config.initial_health == 1.0

    @pytest.mark.parametrize("invalid_alpha", [0.0, -1.0, -0.001])
    def test_invalid_alpha_prior_raises(self, invalid_alpha: float) -> None:
        """Verify non-positive alpha_prior raises ValueError."""
        with pytest.raises(ValueError, match=r"alpha_prior must be > 0\.0"):
            AcquirerStateConfig(alpha_prior=invalid_alpha)

    @pytest.mark.parametrize("invalid_beta", [0.0, -0.5, -10.0])
    def test_invalid_beta_prior_raises(self, invalid_beta: float) -> None:
        """Verify non-positive beta_prior raises ValueError."""
        with pytest.raises(ValueError, match=r"beta_prior must be > 0\.0"):
            AcquirerStateConfig(beta_prior=invalid_beta)

    @pytest.mark.parametrize("invalid_decay", [0.0, 1.0, -0.1, 1.5])
    def test_invalid_decay_factor_raises(self, invalid_decay: float) -> None:
        """Verify decay_factor outside (0, 1) raises ValueError."""
        with pytest.raises(ValueError, match=r"decay_factor must be in \(0.0, 1.0\)"):
            AcquirerStateConfig(decay_factor=invalid_decay)

    @pytest.mark.parametrize("invalid_health", [-0.01, 1.01, -1.0, 2.0])
    def test_invalid_initial_health_raises(self, invalid_health: float) -> None:
        """Verify initial_health outside [0, 1] raises ValueError."""
        with pytest.raises(ValueError, match=r"initial_health must be in \[0.0, 1.0\]"):
            AcquirerStateConfig(initial_health=invalid_health)


class TestAcquirerStateInit:
    """Tests acquirer state initialization and invariant checks."""

    def test_initial_state_defaults(self) -> None:
        """Verify initial state matches uniform prior and optimistic health."""
        state = AcquirerState("stripe_us", initial_timestamp=1000.0)
        assert state.acquirer_id == "stripe_us"
        assert state.config.alpha_prior == 1.0

        snapshot = state.get_state()
        assert snapshot.acquirer_id == "stripe_us"
        assert snapshot.alpha == 1.0
        assert snapshot.beta == 1.0
        assert snapshot.health_score == 1.0
        assert snapshot.success_count == 0
        assert snapshot.failure_count == 0
        assert snapshot.total_count == 0
        assert snapshot.last_updated_at == 1000.0
        assert snapshot.expected_success_rate == 0.5
        assert snapshot.effective_sample_size == 0.0

    @pytest.mark.parametrize("bad_id", ["", "   ", "\t\n"])
    def test_empty_acquirer_id_raises(self, bad_id: str) -> None:
        """Verify empty or whitespace-only acquirer_id raises ValueError."""
        with pytest.raises(ValueError, match="acquirer_id must be a non-empty string"):
            AcquirerState(bad_id)


class TestAcquirerStateUpdates:
    """Tests state update transitions on success and failure."""

    def test_single_success_update(self) -> None:
        """Verify state update after a single successful transaction."""
        config = AcquirerStateConfig(decay_factor=0.90)
        state = AcquirerState("acquirer_a", config=config, initial_timestamp=100.0)

        snapshot = state.record_outcome(success=True, timestamp=101.0)
        assert snapshot.alpha == 2.0  # 1.0 + 0.9*(0) + 1.0
        assert snapshot.beta == 1.0  # 1.0 + 0.9*(0) + 0.0
        assert snapshot.health_score == 1.0  # 0.9*1.0 + 0.1*1.0
        assert snapshot.success_count == 1
        assert snapshot.failure_count == 0
        assert snapshot.total_count == 1
        assert snapshot.last_updated_at == 101.0
        assert math.isclose(snapshot.expected_success_rate, 2.0 / 3.0)

    def test_single_failure_update(self) -> None:
        """Verify state update after a single failed transaction."""
        config = AcquirerStateConfig(decay_factor=0.90)
        state = AcquirerState("acquirer_a", config=config, initial_timestamp=100.0)

        snapshot = state.record_outcome(success=False, timestamp=101.0)
        assert snapshot.alpha == 1.0  # 1.0 + 0.9*(0) + 0.0
        assert snapshot.beta == 2.0  # 1.0 + 0.9*(0) + 1.0
        assert snapshot.health_score == 0.90  # 0.9*1.0 + 0.1*0.0
        assert snapshot.success_count == 0
        assert snapshot.failure_count == 1
        assert snapshot.total_count == 1
        assert snapshot.last_updated_at == 101.0
        assert math.isclose(snapshot.expected_success_rate, 1.0 / 3.0)

    def test_architect_spec_verification_vector(self) -> None:
        """Verify exact values match the Section 5 test vector from the architect spec."""
        config = AcquirerStateConfig(
            alpha_prior=1.0,
            beta_prior=1.0,
            decay_factor=0.90,
            initial_health=1.0,
        )
        state = AcquirerState("verify_acquirer", config=config, initial_timestamp=0.0)

        # Sequence of outcomes: 3 Successes, 3 Failures, 1 Success
        outcomes = [True, True, True, False, False, False, True]

        expected_trace = [
            # (step, alpha, beta, health, expected_rate)
            (1, 2.000000, 1.000000, 1.000000, 0.666667),
            (2, 2.900000, 1.000000, 1.000000, 0.743590),
            (3, 3.710000, 1.000000, 1.000000, 0.787686),
            (4, 3.439000, 2.000000, 0.900000, 0.632285),
            (5, 3.195100, 2.900000, 0.810000, 0.524208),
            (6, 2.975590, 3.710000, 0.729000, 0.445089),
            (7, 3.778031, 3.439000, 0.756100, 0.523485),
        ]

        for success, (step, exp_a, exp_b, exp_h, exp_rate) in zip(
            outcomes, expected_trace, strict=True
        ):
            snapshot = state.record_outcome(success=success, timestamp=float(step))
            assert snapshot.alpha == pytest.approx(exp_a, abs=1e-4)
            assert snapshot.beta == pytest.approx(exp_b, abs=1e-4)
            assert snapshot.health_score == pytest.approx(exp_h, abs=1e-4)
            assert snapshot.expected_success_rate == pytest.approx(exp_rate, abs=1e-4)
            assert snapshot.total_count == step


class TestEdgeCasesAndNumerics:
    """Tests numerical boundaries, floating-point stability, and sustained outage/recovery."""

    def test_sustained_outage_never_drops_alpha_below_prior(self) -> None:
        """Verify 500 consecutive failures decay alpha toward 1.0 without dropping below."""
        config = AcquirerStateConfig(decay_factor=0.95)
        state = AcquirerState("stress_outage", config=config)

        # Initial boost with 20 successes
        for _ in range(20):
            state.record_outcome(success=True)

        # Severe sustained outage: 500 failures
        for _ in range(500):
            snapshot = state.record_outcome(success=False)
            assert snapshot.alpha >= 1.0, f"Alpha dropped below prior: {snapshot.alpha}"
            assert snapshot.health_score >= 0.0, f"Health dropped below 0: {snapshot.health_score}"

        # Alpha must be floored at 1.0 (within IEEE 754 precision)
        assert snapshot.alpha == pytest.approx(1.0, abs=1e-6)
        # Beta must converge to steady state: beta_0 + 1/(1-gamma) = 1 + 20 = 21.0
        assert snapshot.beta == pytest.approx(21.0, abs=1e-3)
        # Health score must decay to zero
        assert snapshot.health_score == pytest.approx(0.0, abs=1e-6)

        # Beta distribution sampling must remain strictly valid
        sample_val = state.sample()
        assert 0.0 <= sample_val <= 1.0

    def test_sustained_success_never_drops_beta_below_prior(self) -> None:
        """Verify 500 consecutive successes decay beta toward 1.0 without dropping below."""
        config = AcquirerStateConfig(decay_factor=0.95)
        state = AcquirerState("stress_success", config=config)

        for _ in range(500):
            snapshot = state.record_outcome(success=True)
            assert snapshot.beta >= 1.0, f"Beta dropped below prior: {snapshot.beta}"
            assert snapshot.health_score <= 1.0, f"Health exceeded 1.0: {snapshot.health_score}"

        assert snapshot.beta == pytest.approx(1.0, abs=1e-6)
        assert snapshot.alpha == pytest.approx(21.0, abs=1e-3)
        assert snapshot.health_score == pytest.approx(1.0, abs=1e-6)

    def test_snapshot_derived_properties(self) -> None:
        """Verify mathematical calculation of snapshot properties."""
        snapshot = AcquirerStateSnapshot(
            acquirer_id="test_props",
            alpha=3.0,
            beta=1.0,
            health_score=0.95,
            success_count=10,
            failure_count=2,
            total_count=12,
            last_updated_at=100.0,
            alpha_prior=1.0,
            beta_prior=1.0,
        )
        assert snapshot.expected_success_rate == 0.75
        # Var = (3 * 1) / (4^2 * 5) = 3 / 80 = 0.0375
        assert snapshot.variance == pytest.approx(0.0375)
        # Effective sample size = (3 - 1) + (1 - 1) = 2.0
        assert snapshot.effective_sample_size == 2.0

    def test_thompson_sampling_distribution(self) -> None:
        """Verify Thompson sampling draws are bounded and converge toward posterior mean."""
        state = AcquirerState("ts_test")
        # Record 10 successes and 0 failures: alpha = 1 + ~9.5, beta = 1
        for _ in range(10):
            state.record_outcome(success=True)

        snapshot = state.get_state()
        expected_mean = snapshot.expected_success_rate

        # Seeded generator for deterministic reproducibility
        rng = np.random.default_rng(seed=42)
        samples = [state.sample(rng=rng) for _ in range(5000)]

        for s in samples:
            assert 0.0 <= s <= 1.0

        empirical_mean = sum(samples) / len(samples)
        assert empirical_mean == pytest.approx(expected_mean, abs=0.015)
