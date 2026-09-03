# QA Test Plan & Verification Report: Phase 1 Router Core State

**Role**: QA / Test Engineer
**Scope**: Phase 1 standalone state (`router_core.state` & `router_core.bandit`)
**Target Specification**: [phase1-state-spec.md](file:///d:/loom/docs/phase1-state-spec.md) & [decisions-log.md](file:///d:/loom/docs/decisions-log.md)
**Test Suite**: [`tests/router_core/test_qa_scenarios.py`](file:///d:/loom/tests/router_core/test_qa_scenarios.py) (alongside `test_state.py` and `test_bandit.py`)
**Status**: Passed (51/51 Tests Passing, 100% Statement Coverage)

---

## 1. Test Objectives & Acceptance Criteria

The QA mandate for Phase 1 is to verify that:
1. The mathematical transitions for both the **Beta parameters ($\alpha, \beta$)** and the **EWMA health score ($H$)** behave exactly as specified under hand-scripted outcome streams.
2. A failure 5 steps in the past attenuates at the precise mathematical rate dictated by $\gamma^5$, with zero phantom drag or arithmetic leakage.
3. System stability is preserved across severe degradation (500-step outages), recovery probing, route flapping (50/50 alternating jitter), and concurrent multi-acquirer registration.
4. All ambiguities or edge cases in the specification are surfaced and logged in [decisions-log.md](file:///d:/loom/docs/decisions-log.md).

---

## 2. Test Plan & Scenario Coverage Matrix

| Scenario ID | Test Name | Objective / Stimulus | Expected Behavior | Result |
| :--- | :--- | :--- | :--- | :---: |
| **QA-LC-01** | `test_full_lifecycle_healthy_outage_recovery` | 10 Successes $\to$ 5 Failures $\to$ 15 Deep Failures $\to$ 1 Recovery Probe $\to$ 20 Restoration Successes | State transitions smoothly: health drops geometrically; beta rises during outage; probe arrests decline; restoration decays beta back toward 1.0. | **PASS** |
| **QA-DG-01** | `test_failure_five_steps_ago_exact_mathematical_drag` | Compare System A (1 failure $+ 5$ successes) vs System B (6 successes) across $\gamma \in \{0.80, 0.90, 0.95, 0.98\}$. | Difference in $\beta$ equals *exactly* $\gamma^5 \times 1.0$. Deficit in $H$ equals *exactly* $(1 - \gamma)\gamma^5$. Zero excess drag. | **PASS** |
| **QA-DG-02** | `test_drag_comparison_across_decay_factors` | Quantify residual drag across $\gamma = 0.90, 0.95, 0.98$. | Validates why default $\gamma=0.98$ retains $>90\%$ of failure drag, whereas $\gamma=0.90$ retains $\sim 59\%$. | **PASS** |
| **QA-FL-01** | `test_flapping_route_oscillates_around_fifty_percent` | 200 alternating outcomes: `[Success, Failure] x 100`. | $H$ oscillates tightly in $[0.45, 0.55]$; posterior mean $\mathbb{E}[\theta]$ stays centered at $0.50 \pm 0.05$. | **PASS** |
| **QA-IS-01** | `test_multi_acquirer_registry_zero_crosstalk_isolation` | 50 failures on Acquirer A; 50 successes on Acquirer B; Acquirer C idle. | Acquirer C remains pristine ($0$ transactions, $H=1.0, \alpha=1, \beta=1$). Zero cross-talk leakage. | **PASS** |
| **QA-TM-01** | `test_out_of_order_timestamps_preserve_mathematical_state` | Record outcomes with non-monotonic timestamps ($t=100$, then $t=90$). | State updates correctly on event order without crashing or corrupting math. | **PASS** |

---

## 3. Deep-Dive Investigation: Does a Failure 5 Steps Ago Drag Down the Estimate More Than It Should?

### 3.1 Mathematical Proof & Verification
We isolated the exact contribution of a single failure 5 steps in the past:

1. **Beta Excess Attenuation**:
   - At step $t=1$ (Failure): $\beta_1 = \beta_0 + 1.0 = 2.0$. Excess $\Delta \beta_1 = +1.0$.
   - At steps $t=2, 3, 4, 5, 6$ (5 consecutive Successes):
     $$\Delta \beta_{1+k} = \gamma^k \cdot \Delta \beta_1 = \gamma^5 \cdot 1.0$$
   - Verification test `test_failure_five_steps_ago_exact_mathematical_drag` verified that:
     $$\beta_{\text{actual}} - \beta_{\text{baseline}} = \gamma^5 \quad (\text{error} < 10^{-9})$$
2. **Health Score Deficit Attenuation**:
   - At step $t=1$ (Failure): $H_1 = \gamma \cdot 1.0$. Initial deficit: $\Delta H_1 = (1 - \gamma)$.
   - After $k=5$ consecutive Successes:
     $$\Delta H_{1+k} = (1 - \gamma) \cdot \gamma^5$$
   - Verification test confirmed:
     $$1.0 - H_{\text{actual}} = (1 - \gamma)\gamma^5 \quad (\text{error} < 10^{-9})$$

**Verdict**: The implementation has **zero excess drag**. A failure 5 steps ago drags down the estimate by **precisely $\gamma^5$**, strictly adhering to the mathematical definition of exponential discounting.

### 3.2 Operational Reality Across Decay Factors
While mathematically exact, the *perceived* drag depends heavily on the configured decay factor:

| Decay Factor ($\gamma$) | Retention After 5 Steps ($\gamma^5$) | Health After 1 Failure $+ 5$ Successes | Lingering Beta Penalty | Effective Window ($N_{\text{eff}}$) |
| :---: | :---: | :---: | :---: | :---: |
| **$0.80$** | **$32.77\%$** | $0.9345$ ($93.5\%$) | $+0.328$ | $5$ transactions |
| **$0.90$** | **$59.05\%$** | $0.9410$ ($94.1\%$) | $+0.590$ | $10$ transactions |
| **$0.95$** | **$77.38\%$** | $0.9613$ ($96.1\%$) | $+0.774$ | $20$ transactions |
| **$0.98$ (Default)** | **$90.39\%$** | $0.9819$ ($98.2\%$) | **$+0.904$** | **$50$ transactions** |

**Crucial QA Finding**: Under the default configuration ($\gamma = 0.98$), a failure 5 transactions ago still exerts **over 90.3% of its original penalty** on the Beta distribution.
- If an acquirer suffers a 10-failure outage, it requires **35 consecutive successes** to cut the penalty in half, and **115 consecutive successes** to eliminate 90% of the penalty.
- In low-throughput scenarios (e.g. 1 TPS), recovery will take nearly 2 minutes to fade. This is not a bug in the code, but an inherent property of selecting $N_{\text{eff}} = 50$.

---

## 4. Spec Ambiguities & Findings Surfaced

During test suite construction, three ambiguities in the specification were identified and logged in [decisions-log.md](file:///d:/loom/docs/decisions-log.md):

### 1. Timestamp Decoupling from Event Decay
- **Finding**: `record_outcome(success, timestamp)` accepts a `timestamp: float | None`, which updates `last_updated_at`. However, the mathematical decay $\gamma$ applied in `record_outcome` is the discrete per-outcome parameter (`config.decay_factor`), independent of elapsed wall-clock seconds ($\Delta t$).
- **Impact**: If an acquirer sits dormant for 30 minutes during an outage with 0 transactions, its state does not decay during the idle period. It only steps when an actual transaction outcome arrives.
- **Action**: Confirmed with Architect spec that event-driven discrete decay was an intentional Phase 1 design to guarantee deterministic replay in tests. Pointed out that dormant exploration must be handled in the routing layer (Phase 3/4).

### 2. Cold-Start Cognitive Disconnect
- **Finding**: On initialization, an acquirer reports `health_score = 1.0` (optimistic assumption that new routes are operational), but its Beta parameters $\alpha_0 = 1.0, \beta_0 = 1.0$ yield an expected success rate $\mathbb{E}[\theta] = \frac{1}{1+1} = 0.50$ (uninformative uniform prior).
- **Impact**: If the live dashboard in Phase 7 displays both metrics simultaneously, operators may perceive a bug (e.g., "Health is 100%, but router expectation is 50%").
- **Action**: Documented in open risks. The dashboard must clarify that `health_score` is operational health, while $\mathbb{E}[\theta]$ reflects Bayesian confidence under unobserved prior entropy.

### 3. Asymmetric Recovery Dynamics
- **Finding**: In Bayesian Beta updates with prior floor ($\alpha_0=1, \beta_0=1$), failures drop the posterior expectation faster than successes restore it. For example, if an acquirer has $\alpha=10, \beta=1$ ($\mathbb{E}[\theta] \approx 90.9\%$), a single failure drops the expectation by $8.9\%$ (to $82.0\%$), but a subsequent success only restores it by $1.0\%$ (to $83.0\%$).
- **Impact**: The router will appear "slow to forgive" an acquirer after an outage, which is statistically sound for risk mitigation, but must be factored into PID gain tuning in Phase 4.

---

## 5. Test Execution & Quality Gate Summary

```bash
============================= test session starts =============================
platform win32 -- Python 3.11.1, pytest-9.1.1
rootdir: D:\loom, configfile: pyproject.toml
collected 51 items

tests/router_core/test_bandit.py ...............                          [ 29%]
tests/router_core/test_qa_scenarios.py .........                          [ 47%]
tests/router_core/test_smoke.py .                                         [ 49%]
tests/router_core/test_state.py ..........................                [100%]

=============================== tests coverage ================================
Name                      Stmts   Miss  Cover
---------------------------------------------
router_core\__init__.py       3      0   100%
router_core\bandit.py        42      0   100%
router_core\state.py         78      0   100%
---------------------------------------------
TOTAL                       123      0   100%
============================= 51 passed in 0.43s ==============================
```

- **Ruff linter**: 0 errors, 0 warnings.
- **Mypy strict**: 0 errors across 13 source files.
- **Coverage**: 100% statement coverage across `router_core`.
- **Phase 1 Quality Gate Status**: **APPROVED & READY FOR PHASE 2**.
