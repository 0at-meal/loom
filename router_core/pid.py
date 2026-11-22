"""Domain models and pure-function step engine for the Phase 4 PID smoothing layer."""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class PIDConfig(BaseModel):
    """Immutable configuration for the PID smoothing controller."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kp: float = Field(
        default=0.12,
        ge=0.0,
        description="Proportional gain constant. Controls immediate reaction to allocation error.",
    )
    ki: float = Field(
        default=0.005,
        ge=0.0,
        description="Integral gain constant. Eliminates steady-state offset.",
    )
    kd: float = Field(
        default=0.25,
        ge=0.0,
        description="Derivative gain constant. Dampens oscillation and rate of change.",
    )
    integral_max: float = Field(
        default=1.0,
        gt=0.0,
        description=(
            "Anti-windup clamping limit for the accumulated error: [-integral_max, +integral_max]."
        ),
    )
    integral_decay: float = Field(
        default=1.0,
        gt=0.0,
        le=1.0,
        description=(
            "Leaky integration retention factor gamma_I in (0.0, 1.0]. "
            "1.0 = standard clamped accumulator."
        ),
    )
    derivative_filter_alpha: float = Field(
        default=0.0,
        ge=0.0,
        lt=1.0,
        description="First-order low-pass filter smoothing coefficient beta_d for derivative term.",
    )
    derivative_on_measurement: bool = Field(
        default=True,
        description=(
            "If True, derivative is computed on actual allocation change (-dw/dt) "
            "to eliminate derivative kick."
        ),
    )
    min_allocation: float = Field(
        default=0.03,
        ge=0.0,
        lt=0.20,
        description="Exploration floor w_min per acquirer to prevent dormant route starvation.",
    )
    floor_rate_per_sec: float | None = Field(
        default=None,
        gt=0.0,
        description=(
            "Stage 1: express the exploration floor as payments per second instead of a share. "
            "Each acquirer's floor becomes min(min_allocation, floor_rate_per_sec / arrival "
            "rate), so the traffic it costs no longer grows with TPS. None keeps the share."
        ),
    )
    strict_integral_clamp: bool = Field(
        default=False,
        description=(
            "Stage 2 (AUDIT F-25): after the zero-sum centring, scale the integral vector "
            "back so no entry exceeds integral_max. Off keeps the original clamp-then-centre, "
            "which lets an entry reach about 1.3 x integral_max with three or more acquirers."
        ),
    )
    actuation_mode: Literal["stochastic", "deficit"] = Field(
        default="stochastic",
        description=(
            "Discrete transaction dispatch policy: 'stochastic' (categorical draw) "
            "or 'deficit' (Bresenham round-robin)."
        ),
    )


@dataclass(frozen=True, slots=True)
class PIDState:
    """Immutable snapshot of the PID controller internal state."""

    accumulated_error: dict[str, float]
    previous_error: dict[str, float]
    previous_allocation: dict[str, float]
    filtered_derivative: dict[str, float]
    step_count: int = 0

    @classmethod
    def initialize(
        cls,
        acquirer_ids: list[str],
        initial_allocation: dict[str, float] | None = None,
    ) -> PIDState:
        """Create a clean, initialized PID state for registered acquirers."""
        k = len(acquirer_ids)
        if k == 0:
            raise ValueError("Cannot initialize PIDState with zero acquirers.")
        default_w = 1.0 / k
        alloc = initial_allocation or {aid: default_w for aid in acquirer_ids}
        return cls(
            accumulated_error={aid: 0.0 for aid in acquirer_ids},
            previous_error={aid: 0.0 for aid in acquirer_ids},
            previous_allocation=dict(alloc),
            filtered_derivative={aid: 0.0 for aid in acquirer_ids},
            step_count=0,
        )


@dataclass(frozen=True, slots=True)
class PIDDiagnostics:
    """Detailed point-in-time calculation telemetry for observability and live dashboard."""

    error: dict[str, float]
    p_term: dict[str, float]
    i_term: dict[str, float]
    d_term: dict[str, float]
    raw_delta: dict[str, float]
    pre_projection_allocation: dict[str, float]


@dataclass(frozen=True, slots=True)
class PIDStepResult:
    """Immutable result of a single PID smoothing step."""

    smoothed_allocation: dict[str, float]
    next_state: PIDState
    diagnostics: PIDDiagnostics


def _require_finite(name: str, values: dict[str, float]) -> None:
    """Raise ValueError if any value is NaN or infinite (AUDIT F-32)."""
    bad = {k: v for k, v in values.items() if not math.isfinite(v)}
    if bad:
        raise ValueError(f"{name} must be finite, got {bad}")


def _project_with_floors(
    weights: dict[str, float], floors: Mapping[str, float]
) -> dict[str, float]:
    """Projection of ``project_to_bounded_simplex`` with a separate floor per acquirer."""
    _require_finite("floors", dict(floors))
    if set(floors) != set(weights):
        raise ValueError(f"floors {sorted(floors)} do not match weights {sorted(weights)}")
    if any(f < 0.0 for f in floors.values()) or sum(floors.values()) >= 1.0:
        raise ValueError(f"floors must be >= 0 and sum to < 1, got {dict(floors)}")
    k = len(weights)
    clamped = {aid: max(floors[aid], w) for aid, w in weights.items()}
    s = sum(clamped.values())
    if abs(s - 1.0) <= 1e-12:
        return {aid: w / s for aid, w in clamped.items()}
    if s > 1.0:
        excess = s - 1.0
        headroom = sum(clamped[aid] - floors[aid] for aid in clamped)
        result = {
            aid: w - excess * (w - floors[aid]) / headroom if headroom > 0.0 else 1.0 / k
            for aid, w in clamped.items()
        }
    else:
        deficit = 1.0 - s
        result = {aid: w + deficit * (w / s if s > 0 else 1.0 / k) for aid, w in clamped.items()}
    total = sum(result.values())
    return {aid: max(floors[aid], w / total) for aid, w in result.items()}


def project_to_bounded_simplex(
    weights: dict[str, float],
    min_floor: float = 0.0,
    floors: Mapping[str, float] | None = None,
) -> dict[str, float]:
    """Project an unconstrained allocation vector onto the probability simplex with a minimum floor.

    Ensures that for all i:
        w_i >= min_floor and sum(w_i) == 1.0

    ``floors``, when given, replaces ``min_floor`` with one floor per acquirer (Stage 1: zero
    for an acquirer whose gate is open, a rate-based floor for the others).

    Raises ValueError for NaN or infinite weights rather than mapping them to the floor.
    """
    _require_finite("weights", weights)
    if floors is not None and len(weights) > 1:
        return _project_with_floors(weights, floors)
    k = len(weights)
    if k == 0:
        return {}
    if k == 1:
        only_key = next(iter(weights))
        return {only_key: 1.0}

    if k * min_floor >= 1.0:
        raise ValueError(
            f"Minimum floor {min_floor} impossible for {k} components "
            f"(k * min_floor = {k * min_floor} >= 1.0)."
        )

    # Step 1: Initial floor clamping
    clamped = {aid: max(min_floor, w) for aid, w in weights.items()}
    s = sum(clamped.values())

    # If already summing to 1.0 within floating point precision
    if abs(s - 1.0) <= 1e-12:
        return {aid: w / s for aid, w in clamped.items()}

    if s > 1.0:
        # Excess mass to remove from arms that are above the minimum floor
        excess = s - 1.0
        adjustable_keys = [aid for aid, w in clamped.items() if w > min_floor]
        headroom = sum(clamped[aid] - min_floor for aid in adjustable_keys)

        if headroom > 0.0:
            result = {}
            for aid, w in clamped.items():
                if aid in adjustable_keys:
                    proportion = (w - min_floor) / headroom
                    result[aid] = w - excess * proportion
                else:
                    result[aid] = min_floor
        else:
            # Fallback: uniform distribution if no headroom
            result = {aid: 1.0 / k for aid in weights}
    else:
        # Deficit mass to add proportionally across all arms
        deficit = 1.0 - s
        result = {}
        for aid, w in clamped.items():
            result[aid] = w + deficit * (w / s if s > 0 else 1.0 / k)

    # Final normalization to eliminate any residual floating-point rounding
    total = sum(result.values())
    return {aid: max(min_floor, w / total) for aid, w in result.items()}


def calculate_pid_step(
    target_allocation: dict[str, float],
    current_allocation: dict[str, float],
    state: PIDState,
    config: PIDConfig,
    dt: float = 1.0,
    floors: Mapping[str, float] | None = None,
) -> PIDStepResult:
    """Calculate one discrete PID smoothing step as a pure, deterministic function.

    Guarantees:
    - Side-effect free, deterministic execution.
    - Zero-sum invariant: sum(e_i) == 0.0, sum(w_i) == 1.0.
    - Anti-windup clamping to [-config.integral_max, +config.integral_max].
    - Zero derivative kick when config.derivative_on_measurement is True.
    - Bounded simplex projection enforcing w_i >= config.min_allocation, or w_i >= floors[i]
      when per-acquirer ``floors`` are given.
    """
    if not math.isfinite(dt):
        raise ValueError(f"dt must be finite, got {dt}")
    if dt <= 0.0:
        raise ValueError(f"dt must be strictly positive, got {dt}")
    _require_finite("target_allocation", target_allocation)
    _require_finite("current_allocation", current_allocation)

    keys = sorted(target_allocation.keys())
    if len(keys) == 0:
        raise ValueError("Cannot calculate PID step with empty target allocation.")

    if set(current_allocation.keys()) != set(keys):
        raise ValueError(
            f"Mismatched acquirer keys between target {set(keys)} "
            f"and current {set(current_allocation.keys())}."
        )

    k = len(keys)

    # Degenerate single-arm topology
    if k == 1:
        only_key = keys[0]
        zero_diag = {only_key: 0.0}
        next_state = PIDState(
            accumulated_error=zero_diag,
            previous_error=zero_diag,
            previous_allocation={only_key: 1.0},
            filtered_derivative=zero_diag,
            step_count=state.step_count + 1,
        )
        diagnostics = PIDDiagnostics(
            error=zero_diag,
            p_term=zero_diag,
            i_term=zero_diag,
            d_term=zero_diag,
            raw_delta=zero_diag,
            pre_projection_allocation={only_key: 1.0},
        )
        return PIDStepResult(
            smoothed_allocation={only_key: 1.0},
            next_state=next_state,
            diagnostics=diagnostics,
        )

    if k * config.min_allocation >= 1.0:
        raise ValueError(
            f"Minimum allocation {config.min_allocation} impossible for {k} acquirers (sum >= 1.0)."
        )

    # 1. Error calculation & zero-sum mean centering
    raw_error = {aid: target_allocation[aid] - current_allocation[aid] for aid in keys}
    mean_error = sum(raw_error.values()) / k
    error = {aid: raw_error[aid] - mean_error for aid in keys}

    # 2. Proportional term
    p_term = {aid: config.kp * error[aid] for aid in keys}

    # 3. Integral term with anti-windup clamping and zero-sum centering
    raw_integral: dict[str, float] = {}
    clamped_integral: dict[str, float] = {}
    for aid in keys:
        prev_i = state.accumulated_error.get(aid, 0.0)
        accum = (config.integral_decay * prev_i) + (error[aid] * dt)
        raw_integral[aid] = accum
        clamped = max(-config.integral_max, min(config.integral_max, accum))
        clamped_integral[aid] = clamped

    mean_clamped_i = sum(clamped_integral.values()) / k
    accumulated_error = {aid: clamped_integral[aid] - mean_clamped_i for aid in keys}
    # Centring can push one entry past the clamp when K >= 3 (AUDIT F-25); scaling the
    # centred vector back keeps it zero-sum and every entry within +/- integral_max.
    peak = max(abs(v) for v in accumulated_error.values())
    if config.strict_integral_clamp and peak > config.integral_max:
        scale = config.integral_max / peak
        accumulated_error = {aid: v * scale for aid, v in accumulated_error.items()}
    i_term = {aid: config.ki * accumulated_error[aid] for aid in keys}

    # 4. Derivative term and derivative kick mitigation
    d_term: dict[str, float] = {}
    filtered_derivative: dict[str, float] = {}
    beta_d = config.derivative_filter_alpha

    for aid in keys:
        if config.derivative_on_measurement:
            # Derivative on process variable (-dw/dt) to eliminate derivative kick
            prev_w = state.previous_allocation.get(aid, current_allocation[aid])
            rate = -(current_allocation[aid] - prev_w) / dt
        else:
            # Derivative on error (de/dt)
            prev_e = state.previous_error.get(aid, error[aid])
            rate = (error[aid] - prev_e) / dt

        prev_filt_d = state.filtered_derivative.get(aid, 0.0)
        filt_d = (beta_d * prev_filt_d) + ((1.0 - beta_d) * rate)
        filtered_derivative[aid] = filt_d
        d_term[aid] = config.kd * filt_d

    # 5. Actuation delta and unconstrained smoothed allocation
    raw_delta = {aid: p_term[aid] + i_term[aid] + d_term[aid] for aid in keys}
    pre_projection = {aid: current_allocation[aid] + raw_delta[aid] for aid in keys}

    # 6. Bounded simplex projection enforcing exploration floor
    smoothed_allocation = project_to_bounded_simplex(
        weights=pre_projection,
        min_floor=config.min_allocation,
        floors=floors,
    )

    # 7. Construct immutable results
    next_state = PIDState(
        accumulated_error=accumulated_error,
        previous_error=error,
        previous_allocation=dict(current_allocation),
        filtered_derivative=filtered_derivative,
        step_count=state.step_count + 1,
    )

    diagnostics = PIDDiagnostics(
        error=error,
        p_term=p_term,
        i_term=i_term,
        d_term=d_term,
        raw_delta=raw_delta,
        pre_projection_allocation=pre_projection,
    )

    return PIDStepResult(
        smoothed_allocation=smoothed_allocation,
        next_state=next_state,
        diagnostics=diagnostics,
    )
