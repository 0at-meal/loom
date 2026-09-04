"""Test windup recovery response: evaluate what happens when target shifts back.

We simulate:
1. 200-transaction sustained outage where target for Alpha is 0.0 (and Beta is 1.0).
2. The accumulator integrates error e_A = 0.0 - 0.03 = -0.03 for 200 transactions.
3. At Tx 201, Alpha recovers and target shifts to 1.0 for Alpha.
4. Measure:
   - Accumulator value for Alpha right before target switch
   - Allocation curve w_A over the next 50 transactions under:
     a) Bounded accumulator (I_max = 1.0)
     b) Unbounded accumulator (I_max = 1000.0) with Ki = 0.005
     c) Unbounded accumulator (I_max = 1000.0) with Ki = 0.05
     d) Bounded accumulator (I_max = 1.0) with Ki = 0.05
"""

from __future__ import annotations

from typing import Any

from router_core.pid import PIDConfig, PIDState, calculate_pid_step


def run_windup_step_test(
    kp: float = 0.12,
    ki: float = 0.005,
    kd: float = 0.25,
    integral_max: float = 1.0,
    outage_steps: int = 200,
    recovery_steps: int = 50,
) -> dict[str, Any]:
    cfg = PIDConfig(
        kp=kp,
        ki=ki,
        kd=kd,
        integral_max=integral_max,
        min_allocation=0.03,
        derivative_on_measurement=True,
    )
    state = PIDState.initialize(["acquirer_alpha", "acquirer_beta"])
    curr = dict(state.previous_allocation)

    # Phase 1: Outage (target for Alpha is 0.0, Beta is 1.0)
    for _ in range(outage_steps):
        res = calculate_pid_step(
            target_allocation={"acquirer_alpha": 0.0, "acquirer_beta": 1.0},
            current_allocation=curr,
            state=state,
            config=cfg,
        )
        curr = res.smoothed_allocation
        state = res.next_state

    accum_at_switch = state.accumulated_error["acquirer_alpha"]
    i_term_at_switch = res.diagnostics.i_term["acquirer_alpha"]
    alloc_at_switch = curr["acquirer_alpha"]

    # Phase 2: Recovery (target for Alpha shifts to 1.0, Beta to 0.0)
    recovery_curve: list[float] = []
    overshoot = 0.0
    delay_to_rise: int | None = None

    for step in range(1, recovery_steps + 1):
        res = calculate_pid_step(
            target_allocation={"acquirer_alpha": 1.0, "acquirer_beta": 0.0},
            current_allocation=curr,
            state=state,
            config=cfg,
        )
        curr = res.smoothed_allocation
        state = res.next_state
        recovery_curve.append(curr["acquirer_alpha"])
        if curr["acquirer_alpha"] > alloc_at_switch + 0.01 and delay_to_rise is None:
            delay_to_rise = step
        if curr["acquirer_alpha"] > 0.97:  # target max given floor 0.03 is 0.97
            overshoot = max(overshoot, curr["acquirer_alpha"] - 0.97)

    return {
        "accum_at_switch": accum_at_switch,
        "i_term_at_switch": i_term_at_switch,
        "alloc_at_switch": alloc_at_switch,
        "delay_to_rise": delay_to_rise,
        "recovery_curve_first_10": recovery_curve[:10],
        "recovery_curve_step_10": recovery_curve[9],
        "recovery_curve_step_25": recovery_curve[24],
        "recovery_curve_step_50": recovery_curve[49],
        "overshoot": overshoot,
    }


def main() -> None:
    print("=" * 80)
    print("INTEGRAL WINDUP RECOVERY STEP COMPARISON")
    print("=" * 80)

    # 1. Bounded Baseline (Ki = 0.005, I_max = 1.0)
    bounded_nominal = run_windup_step_test(kp=0.12, ki=0.005, kd=0.25, integral_max=1.0)
    print("\n1. Tuned PID (Bounded I_max=1.0, Ki=0.005):")
    print(f"   Accumulator at Outage End: {bounded_nominal['accum_at_switch']:.4f}")
    print(f"   I-term at Outage End: {bounded_nominal['i_term_at_switch']:.4f}")
    print(f"   Alloc at Switch: {bounded_nominal['alloc_at_switch']:.4f}")
    print(f"   Delay to rise: {bounded_nominal['delay_to_rise']} steps")
    print(f"   Alloc at Step 10: {bounded_nominal['recovery_curve_step_10']:.4f}")
    print(f"   Alloc at Step 25: {bounded_nominal['recovery_curve_step_25']:.4f}")
    print(f"   Alloc at Step 50: {bounded_nominal['recovery_curve_step_50']:.4f}")
    print(f"   First 10 steps: {[round(x, 3) for x in bounded_nominal['recovery_curve_first_10']]}")

    # 2. Unbounded (Ki = 0.005, I_max = 1000.0)
    unbounded_nominal = run_windup_step_test(kp=0.12, ki=0.005, kd=0.25, integral_max=1000.0)
    print("\n2. Unbounded Accumulator (I_max=1000.0, Ki=0.005):")
    print(f"   Accumulator at Outage End: {unbounded_nominal['accum_at_switch']:.4f}")
    print(f"   I-term at Outage End: {unbounded_nominal['i_term_at_switch']:.4f}")
    print(f"   Alloc at Switch: {unbounded_nominal['alloc_at_switch']:.4f}")
    print(f"   Delay to rise: {unbounded_nominal['delay_to_rise']} steps")
    print(f"   Alloc at Step 10: {unbounded_nominal['recovery_curve_step_10']:.4f}")
    print(f"   Alloc at Step 25: {unbounded_nominal['recovery_curve_step_25']:.4f}")
    print(f"   Alloc at Step 50: {unbounded_nominal['recovery_curve_step_50']:.4f}")
    rec_unbounded_first_10 = [round(x, 3) for x in unbounded_nominal["recovery_curve_first_10"]]
    print(f"   First 10 steps: {rec_unbounded_first_10}")

    # 3. High Ki Bounded (Ki = 0.05, I_max = 1.0)
    bounded_high_ki = run_windup_step_test(kp=0.12, ki=0.05, kd=0.25, integral_max=1.0)
    print("\n3. Bounded High Ki (I_max=1.0, Ki=0.05):")
    print(f"   Accumulator at Outage End: {bounded_high_ki['accum_at_switch']:.4f}")
    print(f"   I-term at Outage End: {bounded_high_ki['i_term_at_switch']:.4f}")
    print(f"   Alloc at Switch: {bounded_high_ki['alloc_at_switch']:.4f}")
    print(f"   Delay to rise: {bounded_high_ki['delay_to_rise']} steps")
    print(f"   Alloc at Step 10: {bounded_high_ki['recovery_curve_step_10']:.4f}")
    print(f"   Alloc at Step 25: {bounded_high_ki['recovery_curve_step_25']:.4f}")
    print(f"   Alloc at Step 50: {bounded_high_ki['recovery_curve_step_50']:.4f}")
    rec_high_ki_first_10 = [round(x, 3) for x in bounded_high_ki["recovery_curve_first_10"]]
    print(f"   First 10 steps: {rec_high_ki_first_10}")

    # 4. High Ki Unbounded (Ki = 0.05, I_max = 1000.0)
    unbounded_high_ki = run_windup_step_test(kp=0.12, ki=0.05, kd=0.25, integral_max=1000.0)
    print("\n4. Unbounded High Ki (I_max=1000.0, Ki=0.05) [CATASTROPHIC WINDUP]:")
    print(f"   Accumulator at Outage End: {unbounded_high_ki['accum_at_switch']:.4f}")
    print(f"   I-term at Outage End: {unbounded_high_ki['i_term_at_switch']:.4f}")
    print(f"   Alloc at Switch: {unbounded_high_ki['alloc_at_switch']:.4f}")
    print(f"   Delay to rise: {unbounded_high_ki['delay_to_rise']} steps")
    print(f"   Alloc at Step 10: {unbounded_high_ki['recovery_curve_step_10']:.4f}")
    print(f"   Alloc at Step 25: {unbounded_high_ki['recovery_curve_step_25']:.4f}")
    print(f"   Alloc at Step 50: {unbounded_high_ki['recovery_curve_step_50']:.4f}")
    rec_unbounded_high_ki = [round(x, 3) for x in unbounded_high_ki["recovery_curve_first_10"]]
    print(f"   First 10 steps: {rec_unbounded_high_ki}")


if __name__ == "__main__":
    main()
