# Phase 8 QA Verification Report: Value-Scaled Exploration

**Date:** 2026-09-19
**Author:** QA Test Engineer
**Status:** APPROVED / GREEN
**Test Suite:** [`tests/router_core/test_qa_phase8_value_scaled.py`](file:///d:/loom/tests/router_core/test_qa_phase8_value_scaled.py), [`tests/router_core/test_value_scaled_exploration.py`](file:///d:/loom/tests/router_core/test_value_scaled_exploration.py)
**Verification Harness:** [`scripts/qa_phase8_controlled_test.py`](file:///d:/loom/scripts/qa_phase8_controlled_test.py)

---

## 1. Executive Summary

Phase 8 introduces **Value-Scaled Exploration**, an affine post-sampling policy layer that dynamically narrows the Thompson Sampling exploration width as transaction amount increases:
$$\tilde{\theta}_i = (1 - \lambda(V))\,\theta_i + \lambda(V)\,\hat{\mu}_i, \quad \lambda(V) = 1 - e^{-V/\tau}$$

This QA report validates the policy layer under two empirical test regimes:
1. **Controlled Paired-Draw Experiment (5,000 trials)**: Comparing routing decisions across varying transaction values ($1 to $5,000) under identical random seeds and posterior states.
2. **End-to-End Outage Regression Audit (150 transactions)**: Testing against the full Phase 3/4 acquirer outage lifecycle to verify strict backwards compatibility and bitwise parity when disabled.

**Key Findings:**
- **Controlled Exploitation:** High-value transactions ($250+) achieve **100.00% routing to the confident leader**, suppressing competitor exploration to **0.00%**.
- **Preserved Exploration:** Low-value transactions ($1) preserve full stochastic exploration, sending **24.18% of traffic to the competitor** (exploration ratio = 0.3189).
- **Zero Regression:** Disabling value-scaling (`enabled=False` or `value_scaled_config=None`) results in **100% bitwise parity** with historical Phase 3 baseline behavior (identical warmup splits, routing sequences, and failure absorption counts).
- **Zero Distribution Contamination:** Confirmed that transaction value ($V$) never enters the posterior update $(\alpha, \beta)$ or EWMA health calculation $(H)$.

---

## 2. Controlled Paired-Draw Experiment (5,000 Trials)

### Methodology & Setup
To eliminate confounding variance from dynamic state evolution, both acquirers were initialized to fixed, realistic posterior states:
- **Acquirer Alpha (Confident Leader):** $\alpha=19.0, \beta=1.0 \implies \hat{\mu}=0.950$, sample $\sigma \approx 0.0463$
- **Acquirer Beta (Exploring Competitor):** $\alpha=18.0, \beta=2.0 \implies \hat{\mu}=0.900$, sample $\sigma \approx 0.0640$
- **Sensitivity Parameter:** $\tau = \$100.00$
- **Sample Size:** $N = 5,000$ paired draws per transaction value, with PRNG seed fixed to `42`.

For every trial, a single paired raw draw $(\theta_\alpha, \theta_\beta)$ was sampled and evaluated across 6 transaction amounts.

### Empirical Results

| Transaction Amount ($V$) | Shrinkage ($\lambda(V)$) | Alpha Win Rate | Beta Win Rate (Exploration) | Sample Std Dev ($\sigma_\alpha$) | Exploration Ratio ($\beta/\alpha$) |
|:-------------------------|:-------------------------|:---------------|:----------------------------|:---------------------------------|:-----------------------------------|
| **$1.00**                | `0.0100`                 | **75.82%**     | **24.18%**                  | `0.04589`                        | `0.3189`                           |
| **$25.00**               | `0.2212`                 | **81.78%**     | **18.22%**                  | `0.03610`                        | `0.2228`                           |
| **$100.00** ($\tau$)     | `0.6321`                 | **96.62%**     | **3.38%**                   | `0.01705`                        | `0.0350`                           |
| **$250.00**              | `0.9179`                 | **100.00%**    | **0.00%**                   | `0.00380`                        | `0.0000`                           |
| **$1,000.00**            | `1.0000`                 | **100.00%**    | **0.00%**                   | `0.00000`                        | `0.0000`                           |
| **$5,000.00**            | `1.0000`                 | **100.00%**    | **0.00%**                   | `0.00000`                        | `0.0000`                           |

### Analysis & Invariant Verification
1. **Monotonic Exploration Compression:** As transaction value scales from $1 to $250, competitor exploration drops monotonically from $24.18\% \to 18.22\% \to 3.38\% \to 0.00\%$.
2. **Variance Compression Invariant:** The observed standard deviation of adjusted samples $\tilde{\theta}_\alpha$ conforms precisely to the theoretical prediction:
   $$\sigma(\tilde{\theta}_i) = (1 - \lambda(V)) \cdot \sigma(\theta_i)$$
   At $V=\$100$ ($\lambda=0.6321$), $\sigma_\alpha = (1 - 0.6321) \times 0.0463 \approx 0.01705$.
3. **No Inversion of Ranks:** For all trials where raw samples favored the leader and the leader had higher $\hat{\mu}$, the policy never altered the ranking. It strictly clamped variance toward the expected mean.

---

## 3. End-to-End Outage Regression Audit

### Scenario Design
Using [`scripts/qa_phase8_controlled_test.py`](file:///d:/loom/scripts/qa_phase8_controlled_test.py) and simulated acquirers running on ASGI test transports, we executed a 150-transaction lifecycle:
- **Transactions 1–50 (Warmup):** Alpha @ 95% success rate, Beta @ 94% success rate.
- **Transactions 51–100 (Outage):** Alpha simulated outage activated (returns 500 / failure).
- **Transactions 101–150 (Recovery):** Alpha outage deactivated (returns 95% success rate).

### Comparative Audit Results

| Metric | Phase 3 Baseline (No P8) | Phase 8 Disabled (`enabled=False`) | Phase 8 Micro ($1.00) | Phase 8 Medium ($50.00) | Phase 8 High ($1,000.00) |
|:---|:---|:---|:---|:---|:---|
| **Warmup Alpha Route Count** | **40** | **40** | **40** | 46 | 50 |
| **Warmup Beta Route Count** | **10** | **10** | **10** | 4 | 0 |
| **Outage Flips (Txs 51–100)** | **13** | **13** | **13** | 3 | 1 |
| **Outage Alpha Traffic Count** | **7** | **7** | **7** | 6 | 13 |
| **Outage Failures Absorbed** | **11** | **11** | **11** | 10 | 17 |
| **Recovery Alpha Traffic** | **3** | **3** | **3** | 0 | 0 |

### Key Observations
1. **Bitwise Parity (Additive Invariant):**
   When comparing **Phase 3 Baseline** against **Phase 8 Disabled**, every single transaction produced the exact same route decision:
   - Identical warmup split: 40 Alpha / 10 Beta.
   - Identical outage flip count: 13 flips.
   - Identical failure counts: 11 failures absorbed during outage.
   - Identical internal state snapshots: Alpha $\alpha=38.48, \beta=12.22, H=0.0000$.
2. **Behavior Under Micro-Transactions ($1.00):**
   At $\$1.00$ ($\lambda=0.0100$), the system behavior is indistinguishable from standard Phase 3 Thompson Sampling (40 warmup, 13 flips, 11 failures).
3. **Behavior Under High Value ($1,000.00):**
   When transaction value is very high, shrinkage forces $\tilde{\theta}_i \approx \hat{\mu}_i$. During warmup, Alpha is selected for all 50 transactions (50/0 split vs 40/10). During an abrupt outage, the router maintains Alpha until the EWMA health score and Bayesian decay reduce Alpha's mean below Beta's. Once flipped, flipping back is completely suppressed (1 flip total vs 13 oscillatory flips).

---

## 4. Acceptance Criteria Verification Matrix

| Requirement | Target Criteria | Test Method | Result |
|:---|:---|:---|:---|
| **High-Value Exploitation** | $\ge 99.9\%$ confident arm selection for $V \ge \$1,000$ | 5,000-draw paired test | **PASSED** (100.00%) |
| **Low-Value Exploration** | $\ge 15.0\%$ competitor exploration for $V = \$1.00$ | 5,000-draw paired test | **PASSED** (24.18%) |
| **Monotonicity** | Leader win rate monotonically non-decreasing with $V$ | Array sort assertion across 6 tiers | **PASSED** |
| **Variance Compression** | Effective standard deviation strictly non-increasing | Array sort assertion across 6 tiers | **PASSED** |
| **Zero Side-Effects on State** | Beta distribution $(\alpha, \beta)$ and EWMA ($H$) independent of $V$ | State inspection across varying amounts | **PASSED** |
| **Backwards Compatibility** | Zero regression when disabled or $V \le 0$ | Bitwise regression test against Phase 3 | **PASSED** |
| **Full Regression Suite** | 252 repository tests passing | `pytest` suite | **PASSED** (252/252) |

---

## 5. Conclusion & Sign-Off

The Phase 8 Value-Scaled Exploration layer satisfies all architectural constraints and QA acceptance criteria:
- The policy layer is mathematically sound, elegant, and non-destructive.
- It operates strictly at decision time without mutating state updates.
- It delivers significant risk mitigation on high-value transactions while preserving empirical discovery on low-value transactions.
- It is ready for technical lead review.
