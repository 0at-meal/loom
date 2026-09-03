"""QA verification test suite for Phase 1 per-acquirer state and bandit beliefs.

Covers full lifecycle scenarios, mathematical decay attenuation proofs, flapping routes,
and edge case verification against the architect and backend implementation.
"""

from __future__ import annotations

import math

import pytest

from router_core.bandit import BanditStateRegistry
from router_core.state import AcquirerState, AcquirerStateConfig


class TestQACanonicalLifecycle:
    """End-to-end hand-scripted outcome lifecycle for a single acquirer."""

    def test_full_lifecycle_healthy_outage_recovery(self) -> None:
        """Hand-script a complete lifecycle: Baseline -> Outage -> Deep Outage -> Recovery.

        Verifies that EWMA health and Beta parameters transition predictably across phases.
        """
        config = AcquirerStateConfig(
            alpha_prior=1.0,
            beta_prior=1.0,
            decay_factor=0.90,
            initial_health=1.0,
        )
        state = AcquirerState("lifecycle_acquirer", config=config, initial_timestamp=100.0)

        # ---------------------------------------------------------------------
        # Phase 1: Healthy Baseline (10 consecutive successes)
        # ---------------------------------------------------------------------
        for i in range(1, 11):
            snap = state.record_outcome(success=True, timestamp=100.0 + i)
            # Health must stay locked at 1.0 on consecutive successes
            assert snap.health_score == pytest.approx(1.0, abs=1e-6)
            # Beta parameter should remain untouched at prior 1.0
            assert snap.beta == pytest.approx(1.0, abs=1e-6)
            # Alpha parameter must climb asymptotically toward 1 + 1/(1-0.90) = 11.0
            assert snap.alpha > 1.0
            assert snap.success_count == i
            assert snap.failure_count == 0

        # At step 10, alpha should be 1 + sum_{j=0}^9 (0.9)^j = 1 + 6.5132 = 7.5132
        alpha_at_baseline = snap.alpha
        assert alpha_at_baseline == pytest.approx(7.5132, abs=1e-3)
        assert snap.expected_success_rate > 0.88

        # ---------------------------------------------------------------------
        # Phase 2: Outage Onset (5 consecutive failures)
        # ---------------------------------------------------------------------
        for i in range(1, 6):
            snap = state.record_outcome(success=False, timestamp=110.0 + i)
            assert snap.beta > 1.0
            assert snap.failure_count == i

        # Health score must have dropped geometrically: 1.0 * (0.90)^5 = 0.59049
        assert snap.health_score == pytest.approx(math.pow(0.90, 5), abs=1e-5)
        # Alpha should have decayed: 1.0 + (7.5132 - 1.0) * (0.90)^5 = 1.0 + 3.8459 = 4.8459
        expected_decayed_alpha = 1.0 + (alpha_at_baseline - 1.0) * math.pow(0.90, 5)
        assert snap.alpha == pytest.approx(expected_decayed_alpha, abs=1e-3)
        # Beta: 1.0 + sum_{j=0}^4 (0.9)^j = 1.0 + (1 - 0.9^5)/0.1 = 1.0 + 4.0951 = 5.0951
        assert snap.beta == pytest.approx(5.0951, abs=1e-3)
        assert snap.expected_success_rate < 0.50

        # ---------------------------------------------------------------------
        # Phase 3: Deep Outage (15 more consecutive failures, 20 total failures)
        # ---------------------------------------------------------------------
        for i in range(6, 21):
            snap = state.record_outcome(success=False, timestamp=110.0 + i)

        # Health score must be severely depressed: (0.90)^20 approx 0.12157
        assert snap.health_score == pytest.approx(math.pow(0.90, 20), abs=1e-5)
        # Alpha decays back near prior 1.0
        expected_deep_alpha = 1.0 + (alpha_at_baseline - 1.0) * math.pow(0.90, 20)
        assert snap.alpha == pytest.approx(expected_deep_alpha, abs=1e-3)
        # Beta approaches steady-state: 1.0 + 1/(1-0.90) = 11.0
        assert snap.beta == pytest.approx(1.0 + (1.0 - math.pow(0.90, 20)) / 0.10, abs=1e-3)
        assert snap.expected_success_rate < 0.16

        # ---------------------------------------------------------------------
        # Phase 4: Recovery Probe (1 single successful transaction)
        # ---------------------------------------------------------------------
        snap = state.record_outcome(success=True, timestamp=140.0)
        # Single success must immediately add 1.0 to alpha
        # New health: 0.90 * prior_health + 0.10 * 1.0 = 0.90 * 0.12157 + 0.10 = 0.2094
        expected_h4 = 0.90 * math.pow(0.90, 20) + 0.10
        assert snap.health_score == pytest.approx(expected_h4, abs=1e-4)
        assert snap.beta < 10.0

        # ---------------------------------------------------------------------
        # Phase 5: Full Restoration (20 consecutive successes)
        # ---------------------------------------------------------------------
        for i in range(1, 21):
            snap = state.record_outcome(success=True, timestamp=140.0 + i)

        # Beta parameter should have decayed by 0.90^21 from its outage peak (~9.784):
        # Remaining excess beta = 8.78423 * 0.90^21 approx 0.9612 -> beta ~ 1.961
        expected_remaining_beta = 1.0 + ((1.0 - math.pow(0.90, 20)) / 0.10) * math.pow(0.90, 21)
        assert snap.beta == pytest.approx(expected_remaining_beta, abs=1e-3)
        assert snap.beta < 2.0  # Confirms over 88% of the 20-failure outage penalty has decayed
        # Health score recovers back toward 1.0 (> 85%)
        assert snap.health_score > 0.85
        # Posterior mean expected rate restored to healthy level (> 80%)
        assert snap.expected_success_rate > 0.80
        assert snap.total_count == 10 + 20 + 1 + 20


class TestQAFiveStepFailureDrag:
    """Evaluates whether a failure 5 steps ago exerts excess drag on current estimates."""

    @pytest.mark.parametrize("gamma", [0.80, 0.90, 0.95, 0.98])
    def test_failure_five_steps_ago_exact_mathematical_drag(self, gamma: float) -> None:
        """Prove that a failure 5 steps ago retains EXACTLY gamma^5 influence.

        Compares System A (1 failure, then 5 successes) against System B (6 successes).
        """
        config = AcquirerStateConfig(decay_factor=gamma, initial_health=1.0)

        # System A: 1 Failure at t=1, followed by 5 Successes (t=2..6)
        state_a = AcquirerState("system_a", config=config, initial_timestamp=0.0)
        state_a.record_outcome(success=False, timestamp=1.0)
        for t in range(2, 7):
            snap_a = state_a.record_outcome(success=True, timestamp=float(t))

        # System B: 0 Failures, 6 consecutive Successes (t=1..6)
        state_b = AcquirerState("system_b", config=config, initial_timestamp=0.0)
        for t in range(1, 7):
            snap_b = state_b.record_outcome(success=True, timestamp=float(t))

        # ---------------------------------------------------------------------
        # Analysis 1: Beta Parameter Residual Drag
        # In System B, beta remains exactly 1.0 (the prior).
        # In System A, the failure at t=1 introduced excess beta = +1.0.
        # After 5 subsequent successes, that excess beta must be attenuated by gamma^5.
        # ---------------------------------------------------------------------
        excess_beta_a = snap_a.beta - snap_b.beta
        expected_excess_beta = math.pow(gamma, 5) * 1.0
        msg_beta = (
            f"Beta drag deviation for gamma={gamma}: {excess_beta_a} != {expected_excess_beta}"
        )
        assert excess_beta_a == pytest.approx(expected_excess_beta, abs=1e-9), msg_beta

        # ---------------------------------------------------------------------
        # Analysis 2: Health Score Deficit Drag
        # In System B, health remains exactly 1.0.
        # In System A, at t=1 health dropped to gamma (deficit = 1 - gamma).
        # Over 5 subsequent successes, this deficit shrinks by gamma on each step.
        # At step 6, deficit = (1 - gamma) * gamma^5.
        # ---------------------------------------------------------------------
        health_deficit_a = snap_b.health_score - snap_a.health_score
        expected_health_deficit = (1.0 - gamma) * math.pow(gamma, 5)
        msg_health = (
            f"Health deviation for gamma={gamma}: {health_deficit_a} != {expected_health_deficit}"
        )
        assert health_deficit_a == pytest.approx(expected_health_deficit, abs=1e-9), msg_health

    def test_drag_comparison_across_decay_factors(self) -> None:
        """Demonstrate the operational difference of a 5-step-old failure across gamma values.

        Shows why gamma=0.98 retains >90% of a failure after 5 steps, while gamma=0.90 retains ~59%.
        """
        results: dict[float, dict[str, float]] = {}

        for gamma in [0.90, 0.95, 0.98]:
            config = AcquirerStateConfig(decay_factor=gamma, initial_health=1.0)
            state = AcquirerState(f"acq_{gamma}", config=config)
            state.record_outcome(success=False)  # t=1 Failure
            for _ in range(5):
                snap = state.record_outcome(success=True)  # t=2..6 Successes

            results[gamma] = {
                "health": snap.health_score,
                "health_deficit_pct": (1.0 - snap.health_score) * 100.0,
                "excess_beta": snap.beta - 1.0,
                "retention_factor_pct": math.pow(gamma, 5) * 100.0,
            }

        # For gamma=0.90: 5 steps decays the failure's weight down to ~59% (health deficit ~4.1%)
        assert results[0.90]["retention_factor_pct"] == pytest.approx(59.049, abs=1e-3)
        assert results[0.90]["health"] == pytest.approx(0.940951, abs=1e-4)

        # For gamma=0.98 (the default): 5 steps only decays the failure's weight down to ~90.4%
        # The failure still retains over 90% of its drag on beta!
        assert results[0.98]["retention_factor_pct"] == pytest.approx(90.392, abs=1e-3)
        assert results[0.98]["excess_beta"] == pytest.approx(0.90392, abs=1e-3)
        # Even though health is 0.9819 (because each failure only dropped health by 2%),
        # the memory retention is overwhelmingly present in beta.
        assert results[0.98]["health"] == pytest.approx(0.98192, abs=1e-4)


class TestQARouteFlappingAndStress:
    """Evaluates behavior under unstable flapping conditions and multi-acquirer isolation."""

    def test_flapping_route_oscillates_around_fifty_percent(self) -> None:
        """Verify that a 50% flapping route (alternating success and failure) converges to 0.50."""
        config = AcquirerStateConfig(decay_factor=0.95, initial_health=1.0)
        state = AcquirerState("flapping_acquirer", config=config)

        # Alternating: [Success, Failure] x 100
        for _ in range(100):
            state.record_outcome(success=True)
            snap = state.record_outcome(success=False)

        # Health score must oscillate tightly around 0.50
        assert 0.45 <= snap.health_score <= 0.55
        # Expected success rate must also stay centered near 0.50
        assert 0.45 <= snap.expected_success_rate <= 0.55
        # Total counts reflect equal split
        assert snap.success_count == 100
        assert snap.failure_count == 100

    def test_multi_acquirer_registry_zero_crosstalk_isolation(self) -> None:
        """Verify that heavy traffic on Acquirer A causes ZERO mutation on Acquirers B and C."""
        registry = BanditStateRegistry()
        registry.register_acquirer("acquirer_a")
        registry.register_acquirer("acquirer_b")
        registry.register_acquirer("acquirer_c")

        # Record 50 failures on A
        for _ in range(50):
            registry.record_outcome("acquirer_a", success=False)

        # Record 50 successes on B
        for _ in range(50):
            registry.record_outcome("acquirer_b", success=True)

        # Acquirer C received zero traffic
        state_c = registry.get_state("acquirer_c")
        assert state_c.total_count == 0
        assert state_c.success_count == 0
        assert state_c.failure_count == 0
        assert state_c.alpha == 1.0
        assert state_c.beta == 1.0
        assert state_c.health_score == 1.0
        assert state_c.expected_success_rate == 0.50

        # Verify A is deeply degraded while B is healthy
        state_a = registry.get_state("acquirer_a")
        state_b = registry.get_state("acquirer_b")
        assert state_a.health_score < 0.40
        assert state_b.health_score == 1.0
        assert state_a.beta > state_b.beta
        assert state_b.alpha > state_a.alpha

    def test_out_of_order_timestamps_preserve_mathematical_state(self) -> None:
        """Verify that if telemetry timestamps arrive out-of-order, state math remains stable.

        Tests whether non-monotonic timestamps corrupt state or fail loudly.
        """
        state = AcquirerState("timestamp_test")
        # Step 1 at t=100.0
        snap1 = state.record_outcome(success=True, timestamp=100.0)
        assert snap1.last_updated_at == 100.0

        # Step 2 with older timestamp t=90.0 (simulating delayed ack or clock skew)
        snap2 = state.record_outcome(success=False, timestamp=90.0)
        # In discrete event-driven EWMA, math is based on event order, not timestamp delta
        assert snap2.last_updated_at == 90.0
        assert snap2.total_count == 2
        assert snap2.beta == 2.0
