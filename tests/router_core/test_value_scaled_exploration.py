"""Unit tests for Phase 8 Value-Scaled Exploration policy layer."""

from __future__ import annotations

import math
import time

import httpx
import pytest
from pydantic import ValidationError

from acquirer_sim.models import AuthorizeRequest
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig
from router_core.value_policy import (
    ValueScaledExplorationConfig,
    apply_value_scaled_policy,
)


class TestValueScaledExplorationConfig:
    """Tests for ValueScaledExplorationConfig schema and mathematical shrinkage."""

    def test_default_config(self) -> None:
        """Default config is disabled with tau=100.0."""
        config = ValueScaledExplorationConfig()
        assert config.enabled is False
        assert config.tau == 100.0
        assert math.isclose(config.sensitivity, 0.01, rel_tol=1e-6)

    def test_invalid_tau_raises(self) -> None:
        """Tau must be strictly positive."""
        with pytest.raises(ValidationError):
            ValueScaledExplorationConfig(tau=0.0)
        with pytest.raises(ValidationError):
            ValueScaledExplorationConfig(tau=-10.0)

    def test_extra_fields_forbidden(self) -> None:
        """Extra configuration parameters are rejected."""
        with pytest.raises(ValidationError):
            ValueScaledExplorationConfig(invalid_param=123)  # type: ignore[call-arg]

    def test_calculate_shrinkage_disabled(self) -> None:
        """When disabled, shrinkage factor is identically 0.0."""
        config = ValueScaledExplorationConfig(enabled=False, tau=100.0)
        assert config.calculate_shrinkage(0.0) == 0.0
        assert config.calculate_shrinkage(50.0) == 0.0
        assert config.calculate_shrinkage(10_000.0) == 0.0

    def test_calculate_shrinkage_boundary_values(self) -> None:
        """Verify mathematical shrinkage at key reference thresholds."""
        config = ValueScaledExplorationConfig(enabled=True, tau=100.0)

        # Zero or negative amount produces zero shrinkage
        assert config.calculate_shrinkage(0.0) == 0.0
        assert config.calculate_shrinkage(-25.0) == 0.0

        # At amount = tau: lambda = 1 - e^(-1) ~= 0.63212
        expected_tau = 1.0 - math.exp(-1.0)
        assert math.isclose(config.calculate_shrinkage(100.0), expected_tau, rel_tol=1e-6)

        # At amount = 2*tau: lambda = 1 - e^(-2) ~= 0.86466
        expected_2tau = 1.0 - math.exp(-2.0)
        assert math.isclose(config.calculate_shrinkage(200.0), expected_2tau, rel_tol=1e-6)

        # At amount = 5*tau: lambda = 1 - e^(-5) ~= 0.99326
        expected_5tau = 1.0 - math.exp(-5.0)
        assert math.isclose(config.calculate_shrinkage(500.0), expected_5tau, rel_tol=1e-6)

        # Large amounts approach 1.0 asymptotically
        assert math.isclose(config.calculate_shrinkage(100_000.0), 1.0, rel_tol=1e-5)


class TestApplyValueScaledPolicy:
    """Tests for apply_value_scaled_policy transformation."""

    def test_bypassed_when_disabled_or_none(self) -> None:
        """Returns unmodified samples when config is None or disabled."""
        samples = {"arm_a": 0.85, "arm_b": 0.92}
        means = {"arm_a": 0.90, "arm_b": 0.80}

        # None config
        adj, lam = apply_value_scaled_policy(samples, means, amount=500.0, config=None)
        assert adj == samples
        assert lam == 0.0

        # Disabled config
        cfg = ValueScaledExplorationConfig(enabled=False, tau=100.0)
        adj, lam = apply_value_scaled_policy(samples, means, amount=500.0, config=cfg)
        assert adj == samples
        assert lam == 0.0

    def test_bypassed_for_zero_or_negative_amount(self) -> None:
        """Returns unmodified samples when amount <= 0."""
        samples = {"arm_a": 0.85, "arm_b": 0.92}
        means = {"arm_a": 0.90, "arm_b": 0.80}
        cfg = ValueScaledExplorationConfig(enabled=True, tau=100.0)

        adj_zero, lam_zero = apply_value_scaled_policy(samples, means, amount=0.0, config=cfg)
        assert adj_zero == samples
        assert lam_zero == 0.0

        adj_neg, lam_neg = apply_value_scaled_policy(samples, means, amount=-10.0, config=cfg)
        assert adj_neg == samples
        assert lam_neg == 0.0

    def test_exact_affine_shrinkage(self) -> None:
        """Verify theta_tilde = (1 - lambda)*theta + lambda*mu."""
        samples = {"arm_a": 0.70, "arm_b": 0.90}
        means = {"arm_a": 0.95, "arm_b": 0.80}
        cfg = ValueScaledExplorationConfig(enabled=True, tau=100.0)

        amount = 100.0  # lambda = 1 - e^(-1) ~= 0.6321205588
        lam = 1.0 - math.exp(-1.0)

        adj, calculated_lam = apply_value_scaled_policy(samples, means, amount=amount, config=cfg)
        assert math.isclose(calculated_lam, lam, rel_tol=1e-6)

        expected_a = (1.0 - lam) * 0.70 + lam * 0.95
        expected_b = (1.0 - lam) * 0.90 + lam * 0.80
        assert math.isclose(adj["arm_a"], expected_a, rel_tol=1e-6)
        assert math.isclose(adj["arm_b"], expected_b, rel_tol=1e-6)


class TestHighValueVsLowValueDecisionSeparation:
    """Core verification: high-value transactions measurably favor the best-known arm."""

    @pytest.mark.asyncio
    async def test_high_value_measurably_favors_best_known_arm(self) -> None:
        """High-value transactions compress sampling width toward the posterior mean.

        Given identical underlying distributions:
        - Acquirer Alpha (best-known leader): alpha=19, beta=1 -> mean = 0.95
        - Acquirer Beta (suboptimal arm with exploration variance): alpha=18, beta=2 -> mean = 0.90

        Under low-value transactions ($1), sampling variance causes Beta to win whenever
        theta_B > theta_A (~15-20% of trials).
        Under high-value transactions ($5,000), sampling variance is compressed to near-zero,
        forcing Alpha (mean 0.95 > 0.90) to win virtually 100% of trials.
        """
        router_config = RouterConfig(
            routes=[
                AcquirerRouteConfig(
                    acquirer_id="acquirer_alpha",
                    base_url="http://mock-gateway",
                    state_config=AcquirerStateConfig(alpha_prior=1.0, beta_prior=1.0),
                ),
                AcquirerRouteConfig(
                    acquirer_id="acquirer_beta",
                    base_url="http://mock-gateway",
                    state_config=AcquirerStateConfig(alpha_prior=1.0, beta_prior=1.0),
                ),
            ],
            value_scaled_config=ValueScaledExplorationConfig(enabled=True, tau=100.0),
            seed=42,
        )
        router = BanditRouter(config=router_config)

        # Seed identical state distributions
        acq_a = router.registry._acquirers["acquirer_alpha"]
        acq_b = router.registry._acquirers["acquirer_beta"]

        # Leader: 19 successes, 1 failure -> mean = 0.95
        acq_a._alpha = 19.0
        acq_a._beta = 1.0

        # Competitor: 18 successes, 2 failures -> mean = 0.90
        acq_b._alpha = 18.0
        acq_b._beta = 2.0

        assert math.isclose(acq_a.get_state().expected_success_rate, 0.95, rel_tol=1e-5)
        assert math.isclose(acq_b.get_state().expected_success_rate, 0.90, rel_tol=1e-5)

        trials = 1000
        low_val_alpha_wins = 0
        high_val_alpha_wins = 0

        # Pinned paired draws across both amount regimes
        for _ in range(trials):
            # Draw raw samples
            raw_samples = router.registry.sample_all(rng=router._rng)
            means = {
                aid: s.expected_success_rate for aid, s in router.registry.get_all_states().items()
            }

            # 1. Low-value transaction ($1.00)
            adj_low, _ = apply_value_scaled_policy(
                samples=raw_samples,
                posterior_means=means,
                amount=1.0,
                config=router.value_scaled_config,
            )
            choice_low = max(adj_low.keys(), key=lambda aid: (adj_low[aid], aid))
            if choice_low == "acquirer_alpha":
                low_val_alpha_wins += 1

            # 2. High-value transaction ($5000.00)
            adj_high, _ = apply_value_scaled_policy(
                samples=raw_samples,
                posterior_means=means,
                amount=5000.0,
                config=router.value_scaled_config,
            )
            choice_high = max(adj_high.keys(), key=lambda aid: (adj_high[aid], aid))
            if choice_high == "acquirer_alpha":
                high_val_alpha_wins += 1

        pct_low = (low_val_alpha_wins / trials) * 100.0
        pct_high = (high_val_alpha_wins / trials) * 100.0

        # Low-value explores normally: Alpha wins roughly 75-85%, Beta explores 15-25%
        assert 70.0 <= pct_low <= 90.0, f"Expected low-value Alpha win rate ~80%, got {pct_low}%"

        # High-value concentrates on leader: Alpha wins > 99.5%
        assert pct_high >= 99.5, f"Expected high-value Alpha win rate >= 99.5%, got {pct_high}%"

        # Measurable, statistically significant difference
        assert (
            pct_high > pct_low + 10.0
        ), f"High-value ({pct_high}%) should measurably favor leader over low-value ({pct_low}%)"


class TestBanditRouterIntegrationAndParity:
    """Tests confirming BanditRouter integrates policy without mutating state."""

    @pytest.mark.asyncio
    async def test_underlying_state_update_untouched_by_amount(self) -> None:
        """Beta distribution updates must increment by 1.0, never by transaction dollar amount."""

        def handler(request: httpx.Request) -> httpx.Response:
            payload = {
                "transaction_id": "tx_big_money",
                "acquirer_id": "acquirer_alpha",
                "authorized": True,
                "status": "AUTHORIZED",
                "simulated_latency_ms": 5.0,
                "timestamp": time.time(),
            }
            return httpx.Response(200, json=payload)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        router_config = RouterConfig(
            routes=[
                AcquirerRouteConfig(
                    acquirer_id="acquirer_alpha",
                    base_url="http://mock-gateway",
                    state_config=AcquirerStateConfig(
                        alpha_prior=1.0, beta_prior=1.0, decay_factor=0.98
                    ),
                )
            ],
            value_scaled_config=ValueScaledExplorationConfig(enabled=True, tau=100.0),
        )
        router = BanditRouter(config=router_config, http_client=client)

        # Route a $50,000 transaction
        req = AuthorizeRequest(transaction_id="tx_big_money", amount=50_000.0)
        res = await router.route(req)

        # Verify telemetry fields populated
        assert res.adjusted_samples is not None
        assert res.exploration_shrinkage is not None
        assert res.exploration_shrinkage > 0.999

        # Verify alpha did NOT increase by $50,000!
        # Initial alpha was 1.0. One success with decay=0.98 gives 1.0 + 0.98*(1.0-1.0) + 1.0 = 2.0.
        state = router.get_state("acquirer_alpha")
        assert math.isclose(
            state.alpha, 2.0, rel_tol=1e-5
        ), f"Alpha should be 2.0 after 1 success, got {state.alpha}!"
        assert state.success_count == 1
        assert state.total_count == 1

        await client.aclose()

    @pytest.mark.asyncio
    async def test_disabled_policy_matches_raw_sampling(self) -> None:
        """Disabled policy sets adjusted_samples and exploration_shrinkage to None."""

        def handler(request: httpx.Request) -> httpx.Response:
            payload = {
                "transaction_id": "tx_disabled",
                "acquirer_id": "acquirer_alpha",
                "authorized": True,
                "status": "AUTHORIZED",
                "simulated_latency_ms": 5.0,
                "timestamp": time.time(),
            }
            return httpx.Response(200, json=payload)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        router_config = RouterConfig(
            routes=[
                AcquirerRouteConfig(
                    acquirer_id="acquirer_alpha",
                    base_url="http://mock-gateway",
                )
            ],
            value_scaled_config=ValueScaledExplorationConfig(enabled=False, tau=100.0),
        )
        router = BanditRouter(config=router_config, http_client=client)

        req = AuthorizeRequest(transaction_id="tx_disabled", amount=500.0)
        res = await router.route(req)

        assert res.adjusted_samples is None
        assert res.exploration_shrinkage is None

        await client.aclose()
