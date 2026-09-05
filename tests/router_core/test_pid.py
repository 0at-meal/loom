"""Unit tests for Phase 4 Ticket A: Pure-function PID step engine and bounded simplex projection."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from router_core.pid import (
    PIDConfig,
    PIDState,
    calculate_pid_step,
    project_to_bounded_simplex,
)


class TestPIDCoreContractAndValidation:
    """Verify input validation, immutability, and basic constraints."""

    def test_default_config_valid(self) -> None:
        """Default PIDConfig should have valid defaults per spec."""
        config = PIDConfig()
        assert config.kp == 0.12
        assert config.ki == 0.005
        assert config.kd == 0.25
        assert config.integral_max == 1.0
        assert config.integral_decay == 1.0
        assert config.derivative_filter_alpha == 0.0
        assert config.derivative_on_measurement is True
        assert config.min_allocation == 0.03
        assert config.actuation_mode == "stochastic"

    def test_invalid_config_raises(self) -> None:
        """Negative gains or invalid bounds must raise ValidationError."""
        with pytest.raises(ValidationError):
            PIDConfig(kp=-0.1)
        with pytest.raises(ValidationError):
            PIDConfig(ki=-0.01)
        with pytest.raises(ValidationError):
            PIDConfig(kd=-0.05)
        with pytest.raises(ValidationError):
            PIDConfig(integral_max=0.0)
        with pytest.raises(ValidationError):
            PIDConfig(integral_decay=0.0)
        with pytest.raises(ValidationError):
            PIDConfig(integral_decay=1.05)
        with pytest.raises(ValidationError):
            PIDConfig(derivative_filter_alpha=1.0)
        with pytest.raises(ValidationError):
            PIDConfig(min_allocation=0.25)

    def test_state_initialization(self) -> None:
        """PIDState.initialize should create equal split across acquirers."""
        state = PIDState.initialize(["acquirer_alpha", "acquirer_beta"])
        assert state.accumulated_error == {"acquirer_alpha": 0.0, "acquirer_beta": 0.0}
        assert state.previous_allocation == {"acquirer_alpha": 0.5, "acquirer_beta": 0.5}
        assert state.step_count == 0

    def test_state_initialization_empty_raises(self) -> None:
        """Initializing with empty list must raise ValueError."""
        with pytest.raises(ValueError, match="zero acquirers"):
            PIDState.initialize([])

    def test_invalid_dt_raises(self) -> None:
        """dt <= 0.0 must raise ValueError."""
        config = PIDConfig()
        state = PIDState.initialize(["a", "b"])
        with pytest.raises(ValueError, match="strictly positive"):
            calculate_pid_step({"a": 1.0, "b": 0.0}, {"a": 0.5, "b": 0.5}, state, config, dt=0.0)
        with pytest.raises(ValueError, match="strictly positive"):
            calculate_pid_step({"a": 1.0, "b": 0.0}, {"a": 0.5, "b": 0.5}, state, config, dt=-1.0)

    def test_mismatched_keys_raises(self) -> None:
        """Mismatched acquirer keys must raise ValueError."""
        config = PIDConfig()
        state = PIDState.initialize(["a", "b"])
        with pytest.raises(ValueError, match="Mismatched acquirer keys"):
            calculate_pid_step({"a": 1.0, "b": 0.0}, {"a": 0.5, "c": 0.5}, state, config)

    def test_empty_keys_raises(self) -> None:
        """Empty allocation dictionaries must raise ValueError."""
        config = PIDConfig()
        state = PIDState(
            accumulated_error={},
            previous_error={},
            previous_allocation={},
            filtered_derivative={},
        )
        with pytest.raises(ValueError, match="empty target allocation"):
            calculate_pid_step({}, {}, state, config)

    def test_pure_function_determinism(self) -> None:
        """Calling calculate_pid_step with identical inputs produces exact same outputs."""
        config = PIDConfig(kp=0.3, ki=0.05, kd=0.1)
        state = PIDState.initialize(["a", "b"])
        targ = {"a": 0.8, "b": 0.2}
        curr = {"a": 0.5, "b": 0.5}

        res1 = calculate_pid_step(targ, curr, state, config, dt=1.0)
        res2 = calculate_pid_step(targ, curr, state, config, dt=1.0)

        assert res1.smoothed_allocation == res2.smoothed_allocation
        assert res1.next_state.accumulated_error == res2.next_state.accumulated_error
        assert res1.diagnostics.raw_delta == res2.diagnostics.raw_delta


class TestArchitectSpecVectors:
    """Verify test vectors TC-PID-01 through TC-PID-06 from docs/phase4-pid-spec.md."""

    def test_tc_pid_01_proportional_response(self) -> None:
        """TC-PID-01: Proportional response to step error.

        Kp = 0.5, Ki = 0, Kd = 0, target = (1.0, 0.0), current = (0.5, 0.5).
        Error = (+0.5, -0.5). Delta = (+0.25, -0.25).
        Smoothed allocation = (0.75, 0.25).
        """
        config = PIDConfig(kp=0.5, ki=0.0, kd=0.0, min_allocation=0.0)
        state = PIDState.initialize(["a", "b"])
        targ = {"a": 1.0, "b": 0.0}
        curr = {"a": 0.5, "b": 0.5}

        res = calculate_pid_step(targ, curr, state, config)
        assert res.diagnostics.error["a"] == pytest.approx(0.5, abs=1e-9)
        assert res.diagnostics.error["b"] == pytest.approx(-0.5, abs=1e-9)
        assert res.diagnostics.p_term["a"] == pytest.approx(0.25, abs=1e-9)
        assert res.diagnostics.p_term["b"] == pytest.approx(-0.25, abs=1e-9)
        assert res.smoothed_allocation["a"] == pytest.approx(0.75, abs=1e-9)
        assert res.smoothed_allocation["b"] == pytest.approx(0.25, abs=1e-9)

    def test_tc_pid_02_anti_windup_clamping(self) -> None:
        """TC-PID-02: Anti-windup accumulator bounding at +/- integral_max.

        Sustained error over 100 steps must clamp accumulator strictly at limit.
        """
        config = PIDConfig(kp=0.0, ki=0.01, kd=0.0, integral_max=1.0, min_allocation=0.0)
        state = PIDState.initialize(["a", "b"])
        targ = {"a": 0.0, "b": 1.0}
        curr = {"a": 0.5, "b": 0.5}

        for _ in range(100):
            res = calculate_pid_step(targ, curr, state, config, dt=1.0)
            state = res.next_state

        # Error is -0.5 for A, +0.5 for B.
        # Over 100 steps without anti-windup, integral would be -50.0 and +50.0.
        # With anti-windup clamping, it must be strictly clamped to -1.0 and +1.0.
        assert state.accumulated_error["a"] == pytest.approx(-1.0, abs=1e-9)
        assert state.accumulated_error["b"] == pytest.approx(1.0, abs=1e-9)
        assert res.diagnostics.i_term["a"] == pytest.approx(-0.01, abs=1e-9)
        assert res.diagnostics.i_term["b"] == pytest.approx(0.01, abs=1e-9)

    def test_tc_pid_03_derivative_kick_elimination(self) -> None:
        """TC-PID-03: Instantaneous target step produces zero derivative kick on step turn.

        With derivative_on_measurement=True, derivative depends on (w_current - w_prev).
        If w_current hasn't moved yet (w_current == w_prev), rate is 0.0.
        """
        config = PIDConfig(kp=0.2, ki=0.0, kd=0.5, derivative_on_measurement=True)
        # Previous allocation was (0.5, 0.5), current is (0.5, 0.5)
        state = PIDState(
            accumulated_error={"a": 0.0, "b": 0.0},
            previous_error={"a": 0.0, "b": 0.0},
            previous_allocation={"a": 0.5, "b": 0.5},
            filtered_derivative={"a": 0.0, "b": 0.0},
        )
        curr = {"a": 0.5, "b": 0.5}
        # Sudden target cliff from (0.9, 0.1) to (0.1, 0.9)
        targ = {"a": 0.1, "b": 0.9}

        res = calculate_pid_step(targ, curr, state, config)
        # Derivative-on-measurement rate = -(0.5 - 0.5) / 1.0 = 0.0 => D term is 0.0!
        assert res.diagnostics.d_term["a"] == pytest.approx(0.0, abs=1e-9)
        assert res.diagnostics.d_term["b"] == pytest.approx(0.0, abs=1e-9)

        # Contrast with derivative on error (derivative_on_measurement=False)
        config_error_deriv = PIDConfig(kp=0.2, ki=0.0, kd=0.5, derivative_on_measurement=False)
        res_kick = calculate_pid_step(targ, curr, state, config_error_deriv)
        # Error changed from 0.0 to -0.4, causing huge negative derivative kick
        assert res_kick.diagnostics.d_term["a"] < -0.15

    def test_tc_pid_04_exploration_floor_enforcement(self) -> None:
        """TC-PID-04: Minimum allocation floor is strictly preserved under deep outage."""
        config = PIDConfig(kp=1.0, ki=0.0, kd=0.0, min_allocation=0.03)
        state = PIDState.initialize(["a", "b"])
        # Severe outage driving A completely to 0.0
        targ = {"a": 0.0, "b": 1.0}
        curr = {"a": 0.05, "b": 0.95}

        res = calculate_pid_step(targ, curr, state, config)
        # Allocation to A must not drop below min_allocation 0.03
        assert res.smoothed_allocation["a"] >= 0.03
        assert res.smoothed_allocation["b"] <= 0.97
        assert sum(res.smoothed_allocation.values()) == pytest.approx(1.0, abs=1e-9)

    def test_tc_pid_05_zero_sum_centering(self) -> None:
        """TC-PID-05: Sum of errors and clamped integrals across 3 arms is strictly zero."""
        config = PIDConfig(kp=0.2, ki=0.05, kd=0.1, integral_max=1.0)
        state = PIDState.initialize(["a", "b", "c"])
        targ = {"a": 0.7, "b": 0.2, "c": 0.1}
        curr = {"a": 0.3333333333333333, "b": 0.3333333333333333, "c": 0.3333333333333333}

        res = calculate_pid_step(targ, curr, state, config)
        assert sum(res.diagnostics.error.values()) == pytest.approx(0.0, abs=1e-12)
        assert sum(res.next_state.accumulated_error.values()) == pytest.approx(0.0, abs=1e-12)
        assert sum(res.smoothed_allocation.values()) == pytest.approx(1.0, abs=1e-9)

    def test_tc_pid_06_degenerate_single_arm(self) -> None:
        """TC-PID-06: Single-acquirer setup trivially allocates 1.0 with zero error."""
        config = PIDConfig()
        state = PIDState.initialize(["only_one"])
        targ = {"only_one": 1.0}
        curr = {"only_one": 1.0}

        res = calculate_pid_step(targ, curr, state, config)
        assert res.smoothed_allocation == {"only_one": 1.0}
        assert res.diagnostics.error["only_one"] == 0.0
        assert res.diagnostics.raw_delta["only_one"] == 0.0


class TestPIDControlDynamics:
    """Core control theory properties requested by user: integral closing & derivative damping."""

    def test_constant_error_eventually_closed_by_integral_term(self) -> None:
        """Verify that steady-state error is eliminated over time by the integral term."""
        # PI controller with Kp=0.15, Ki=0.03, Kd=0.05, anti-windup=1.0
        config = PIDConfig(kp=0.15, ki=0.03, kd=0.05, integral_max=1.0, min_allocation=0.0)
        state = PIDState.initialize(["a", "b"])

        # Target has a persistent offset at (0.8, 0.2), starting at (0.5, 0.5)
        target = {"a": 0.8, "b": 0.2}
        curr = {"a": 0.5, "b": 0.5}

        # Step through closed loop: output of previous step becomes current allocation for next step
        initial_error = target["a"] - curr["a"]  # 0.30
        for _ in range(45):
            res = calculate_pid_step(target, curr, state, config, dt=1.0)
            curr = res.smoothed_allocation
            state = res.next_state

        final_error = abs(target["a"] - curr["a"])
        # Integral term must have accumulated and closed the gap: allocation reaches > 0.79
        assert curr["a"] > 0.79
        assert curr["b"] < 0.21
        # Error must have been closed by > 96%
        assert final_error < 0.01
        assert final_error < initial_error

    def test_constant_error_monotonically_accumulates_until_anti_windup_bound(self) -> None:
        """Verify constant error accumulates integral correction monotonically to bound."""
        # integral_max = 1.0. With error = 0.20, it takes 5 steps to reach 1.0
        config = PIDConfig(kp=0.0, ki=0.02, kd=0.0, integral_max=1.0, min_allocation=0.0)
        state = PIDState.initialize(["a", "b"])

        # Hold constant error of +0.20 on A by holding target=(0.7, 0.3) and current=(0.5, 0.5)
        target = {"a": 0.7, "b": 0.3}
        curr = {"a": 0.5, "b": 0.5}

        i_terms: list[float] = []
        for _ in range(10):
            res = calculate_pid_step(target, curr, state, config, dt=1.0)
            state = res.next_state
            i_terms.append(res.diagnostics.i_term["a"])

        # Steps 0 to 4 must strictly increase as error accumulates
        for t in range(1, 5):
            assert i_terms[t] > i_terms[t - 1]

        # Step 5 onward must be clamped strictly at integral_max * ki = 1.0 * 0.02 = 0.02
        for t in range(5, 10):
            assert i_terms[t] == pytest.approx(0.02, abs=1e-9)

    def test_rapidly_closing_error_dampened_by_derivative_term_without_overshoot(self) -> None:
        """Verify rapidly moving allocation is braked by Kd, preventing overshoot."""
        target = {"a": 0.8, "b": 0.2}

        # Scenario without derivative damping (Kp=0.4, Kd=0.0):
        # Allocation is already rapidly approaching: previous was 0.5, current is 0.78
        state_no_d = PIDState(
            accumulated_error={"a": 0.0, "b": 0.0},
            previous_error={"a": 0.3, "b": -0.3},
            previous_allocation={"a": 0.5, "b": 0.5},
            filtered_derivative={"a": 0.0, "b": 0.0},
        )
        curr = {"a": 0.78, "b": 0.22}

        # With strong derivative damping (Kd = 0.40):
        config_with_d = PIDConfig(kp=0.4, ki=0.0, kd=0.40, derivative_on_measurement=True)
        res_d = calculate_pid_step(target, curr, state_no_d, config_with_d)

        # Without derivative damping (Kd = 0.0):
        config_no_d = PIDConfig(kp=0.4, ki=0.0, kd=0.0, derivative_on_measurement=True)
        res_no_d = calculate_pid_step(target, curr, state_no_d, config_no_d)

        # Rate of change is (0.78 - 0.5) = +0.28.
        # D term should be -Kd * 0.28 = -0.4 * 0.28 = -0.112.
        assert res_d.diagnostics.d_term["a"] < -0.10
        assert res_no_d.diagnostics.d_term["a"] == 0.0

        # The allocation with derivative damping should be smaller (braked) than without D
        assert res_d.smoothed_allocation["a"] < res_no_d.smoothed_allocation["a"]
        # And it must not overshoot target 0.8
        assert res_d.smoothed_allocation["a"] <= 0.80

    def test_low_pass_filtered_derivative(self) -> None:
        """Verify that derivative filter coefficient beta_d smooths rate spikes."""
        config = PIDConfig(
            kp=0.0, ki=0.0, kd=1.0, derivative_filter_alpha=0.8, derivative_on_measurement=True
        )
        state = PIDState(
            accumulated_error={"a": 0.0, "b": 0.0},
            previous_error={"a": 0.0, "b": 0.0},
            previous_allocation={"a": 0.5, "b": 0.5},
            filtered_derivative={"a": 0.0, "b": 0.0},
        )
        # Large jump: 0.5 -> 0.8 => rate = -0.3
        curr = {"a": 0.8, "b": 0.2}
        res = calculate_pid_step({"a": 0.8, "b": 0.2}, curr, state, config)

        # Filter: 0.8 * 0.0 + (1 - 0.8) * (-0.3) = -0.06
        assert res.diagnostics.d_term["a"] == pytest.approx(-0.06, abs=1e-5)

    def test_leaky_integration(self) -> None:
        """Verify integral_decay < 1.0 attenuates accumulated error when error is 0."""
        config = PIDConfig(kp=0.0, ki=0.0, kd=0.0, integral_decay=0.90)
        state = PIDState(
            accumulated_error={"a": 1.0, "b": -1.0},
            previous_error={"a": 0.0, "b": 0.0},
            previous_allocation={"a": 0.5, "b": 0.5},
            filtered_derivative={"a": 0.0, "b": 0.0},
        )
        # Error is 0.0
        curr = {"a": 0.5, "b": 0.5}
        res = calculate_pid_step({"a": 0.5, "b": 0.5}, curr, state, config)

        # Accumulated error should have attenuated by 0.90
        assert res.next_state.accumulated_error["a"] == pytest.approx(0.90, abs=1e-5)
        assert res.next_state.accumulated_error["b"] == pytest.approx(-0.90, abs=1e-5)


class TestSimplexProjection:
    """Verify bounded simplex projection edge cases and constraints."""

    def test_empty_and_single_arm_projection(self) -> None:
        """Empty and single-arm dictionaries behave predictably."""
        assert project_to_bounded_simplex({}) == {}
        assert project_to_bounded_simplex({"a": 0.4}) == {"a": 1.0}

    def test_impossible_floor_raises(self) -> None:
        """k * min_floor >= 1.0 must raise ValueError."""
        with pytest.raises(ValueError, match="impossible"):
            project_to_bounded_simplex({"a": 0.5, "b": 0.5}, min_floor=0.6)

    def test_exact_sum_and_floor_preservation(self) -> None:
        """Simplex projection guarantees w_i >= floor and sum == 1.0 across diverse vectors."""
        test_vectors = [
            ({"a": -0.5, "b": 1.5}, 0.05),
            ({"a": 0.0, "b": 0.0, "c": 0.0}, 0.03),
            ({"a": 10.0, "b": -5.0, "c": 2.0}, 0.02),
            ({"a": 0.33, "b": 0.33, "c": 0.34}, 0.0),
        ]
        for vec, floor in test_vectors:
            proj = project_to_bounded_simplex(vec, min_floor=floor)
            assert sum(proj.values()) == pytest.approx(1.0, abs=1e-9)
            for _aid, w in proj.items():
                assert w >= floor - 1e-9
