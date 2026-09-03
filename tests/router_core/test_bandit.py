"""Unit tests for BanditStateRegistry and calculate_gamma_from_half_life."""

from __future__ import annotations

import math

import numpy as np
import pytest

from router_core.bandit import BanditStateRegistry, calculate_gamma_from_half_life
from router_core.state import AcquirerStateConfig


class TestCalculateGamma:
    """Tests half-life to decay factor mathematical conversion."""

    def test_calculate_gamma_basic(self) -> None:
        """Verify decay factor formula gamma = 0.5^(1 / (half_life * tps))."""
        # Half life = 10s, TPS = 10 -> N_half = 100 transactions
        gamma = calculate_gamma_from_half_life(half_life_seconds=10.0, expected_tps=10.0)
        assert gamma == pytest.approx(math.pow(0.5, 1.0 / 100.0))
        assert math.isclose(math.pow(gamma, 100), 0.5, rel_tol=1e-6)

    def test_default_env_params(self) -> None:
        """Verify derivation for DECAY_HALF_LIFE_SEC=60.0 with 5 TPS."""
        gamma = calculate_gamma_from_half_life(half_life_seconds=60.0, expected_tps=5.0)
        n_half = 300.0
        assert gamma == pytest.approx(math.pow(0.5, 1.0 / n_half))

    @pytest.mark.parametrize("bad_hl", [0.0, -5.0])
    def test_non_positive_half_life_raises(self, bad_hl: float) -> None:
        """Verify non-positive half_life raises ValueError."""
        with pytest.raises(ValueError, match=r"half_life_seconds must be > 0\.0"):
            calculate_gamma_from_half_life(half_life_seconds=bad_hl, expected_tps=10.0)

    @pytest.mark.parametrize("bad_tps", [0.0, -1.0])
    def test_non_positive_tps_raises(self, bad_tps: float) -> None:
        """Verify non-positive tps raises ValueError."""
        with pytest.raises(ValueError, match=r"expected_tps must be > 0\.0"):
            calculate_gamma_from_half_life(half_life_seconds=10.0, expected_tps=bad_tps)


class TestBanditStateRegistry:
    """Tests multi-acquirer registration, coordination, and Thompson sampling."""

    def test_register_and_get_state(self) -> None:
        """Verify registering acquirers and reading snapshots."""
        registry = BanditStateRegistry()
        state = registry.register_acquirer("acquirer_1")
        assert state.acquirer_id == "acquirer_1"

        snapshot = registry.get_state("acquirer_1")
        assert snapshot.acquirer_id == "acquirer_1"
        assert snapshot.alpha == 1.0
        assert snapshot.beta == 1.0

    def test_duplicate_registration_raises(self) -> None:
        """Verify registering identical acquirer_id fails loudly."""
        registry = BanditStateRegistry()
        registry.register_acquirer("stripe")
        with pytest.raises(ValueError, match="Acquirer 'stripe' is already registered"):
            registry.register_acquirer("stripe")

    def test_unknown_acquirer_operations_raise(self) -> None:
        """Verify accessing unknown acquirer raises KeyError."""
        registry = BanditStateRegistry()
        with pytest.raises(KeyError, match="Acquirer 'unknown' not found"):
            registry.record_outcome("unknown", success=True)
        with pytest.raises(KeyError, match="Acquirer 'unknown' not found"):
            registry.get_state("unknown")

    def test_multi_acquirer_sample_all(self) -> None:
        """Verify sample_all returns independent samples for all registered routes."""
        registry = BanditStateRegistry()
        registry.register_acquirer("acq_a")
        registry.register_acquirer("acq_b")
        registry.register_acquirer("acq_c")

        # Train acq_a with successes, acq_c with failures
        for _ in range(10):
            registry.record_outcome("acq_a", success=True)
            registry.record_outcome("acq_c", success=False)

        rng = np.random.default_rng(seed=123)
        samples = registry.sample_all(rng=rng)

        assert set(samples.keys()) == {"acq_a", "acq_b", "acq_c"}
        for val in samples.values():
            assert 0.0 <= val <= 1.0

        all_states = registry.get_all_states()
        assert set(all_states.keys()) == {"acq_a", "acq_b", "acq_c"}
        assert all_states["acq_a"].health_score > all_states["acq_c"].health_score

    def test_custom_default_config(self) -> None:
        """Verify registry propagates default config to newly registered acquirers."""
        custom_config = AcquirerStateConfig(decay_factor=0.85, initial_health=0.8)
        registry = BanditStateRegistry(default_config=custom_config)
        state = registry.register_acquirer("custom_acq")

        assert state.config.decay_factor == 0.85
        snapshot = state.get_state()
        assert snapshot.health_score == 0.8

    def test_list_acquirer_ids(self) -> None:
        """Verify list_acquirer_ids returns list of registered identifiers."""
        registry = BanditStateRegistry()
        registry.register_acquirer("acq_1")
        registry.register_acquirer("acq_2")
        assert registry.list_acquirer_ids() == ["acq_1", "acq_2"]

    @pytest.mark.parametrize("bad_id", ["", "   ", "\t"])
    def test_invalid_acquirer_id_raises(self, bad_id: str) -> None:
        """Verify registering invalid acquirer_id raises ValueError."""
        registry = BanditStateRegistry()
        with pytest.raises(ValueError, match="acquirer_id must be a non-empty string"):
            registry.register_acquirer(bad_id)
