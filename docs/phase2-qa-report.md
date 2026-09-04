# QA Test Plan & Verification Report: Phase 2 Simulated Acquirer Service

**Role**: QA / Test Engineer
**Scope**: Phase 2 Simulated Acquirer Service (`acquirer_sim`)
**Target Specification**: [`docs/phase2-simulator-spec.md`](file:///d:/loom/docs/phase2-simulator-spec.md) & [`docs/decisions-log.md`](file:///d:/loom/docs/decisions-log.md)
**Test Suite**: [`tests/acquirer_sim/test_qa_scenarios.py`](file:///d:/loom/tests/acquirer_sim/test_qa_scenarios.py) (alongside `test_api.py`, `test_simulator.py`, and `test_models.py`)
**Status**: Passed (95/95 Tests Passing, 93% Statement Coverage in `acquirer_sim`, 100% in `router_core`)

---

## 1. Test Objectives & Acceptance Criteria

The QA mandate for Phase 2 is to rigorously audit the simulated acquirer service against the requirements of the PRD and the Architect's specification:

1. **Scriptability & Statistical Convergence**:
   - Setting a target success rate $p \in [0.0, 1.0]$ produces authorization outcomes whose sample mean $\hat{p} = \frac{k}{N}$ converges to $p$ within strict statistical bounds ($3\sigma$ binomial confidence interval) over large sample sizes ($N \ge 2,000$).
   - Dynamic rate changes mid-stream must take effect immediately on future calls without process restarts or lingering state.
2. **Outage Step-Function Dynamics**:
   - Engaging an outage mid-run must change `authorize`'s behavior on the **very next call** (call $N+1$), with zero lag, trailing probability decay, or buffer delay.
   - Disengaging an outage must restore normal authorization behavior on the **very next call** (call $M+1$).
3. **Boundary Conditions & Edge Cases**:
   - Absolute boundary success rates ($p = 0.0$ and $p = 1.0$) must behave with 100% determinism (no rogue authorizations at 0%, no rogue declines at 100%).
   - Rapid flapping (toggling outage ON and immediately OFF without intervening calls) must leave the simulator in a cleanly restored operational state.
   - Alternating flapping (toggling on every call) must alternate outcomes without counter corruption or state drift.
   - Concurrent asynchronous executions must exhibit thread-safe atomic counter consistency ($N_{\text{total}} = N_{\text{auth}} + N_{\text{declined}}$).
4. **Contract Ambiguity Identification**:
   - Surface any gaps, edge cases, or ambiguous behaviors in the specification that required implementation assumptions, so Phase 3's router does not guess.

---

## 2. Test Plan & Scenario Coverage Matrix

| Scenario ID | Test Name | Objective / Stimulus | Expected Behavior | Result |
| :--- | :--- | :--- | :--- | :---: |
| **QA-SR-01** | `test_statistical_convergence_across_spectrum` | 2,500 authorizations across $p \in \{0.25, 0.50, 0.70, 0.90\}$. | Empirical PSR falls within $3\sigma$ binomial confidence interval: $p \pm 3\sqrt{\frac{p(1-p)}{N}}$. | **PASS** |
| **QA-SR-02** | `test_scriptable_rate_mutation_mid_stream_via_api` | Stream of 100 calls via HTTP: $p=1.0$ for 50 calls, then mutated via `POST /admin/success-rate` to $p=0.0$ for 50 calls. | Calls 1–50 are 100% authorized; calls 51–100 are 100% declined. Telemetry reports exactly 50% lifetime PSR. | **PASS** |
| **QA-OT-01** | `test_mid_run_outage_step_function_with_zero_delay` | 50 healthy calls ($p=1.0$) $\to$ Trigger outage $\to$ Call 51 $\to$ 49 outage calls $\to$ Clear outage $\to$ Call 101. | Call 51 declines *immediately* with `ACQUIRER_OUTAGE`. Call 101 authorizes *immediately*. Zero lag. | **PASS** |
| **QA-EC-01** | `test_edge_case_zero_percent_rate` | $p = 0.0$ evaluated across 100 consecutive requests. | 100% declined with `DO_NOT_HONOR`. Exactly 0 authorizations. | **PASS** |
| **QA-EC-02** | `test_edge_case_hundred_percent_rate` | $p = 1.0$ evaluated across 100 consecutive requests. | 100% authorized with valid auth codes. Exactly 0 declines. | **PASS** |
| **QA-EC-03** | `test_edge_case_toggle_on_then_immediately_off` | Outage toggled ON then immediately OFF (0 intervening calls), and reversed (OFF then ON). | Immediate state update; zero residual outage penalty on subsequent calls. | **PASS** |
| **QA-EC-04** | `test_edge_case_high_frequency_flapping` | Alternate outage state on every single transaction for 40 transactions. | Strict 1:1 alternating sequence of Declines and Authorizations. Zero counter drift. | **PASS** |
| **QA-CO-01** | `test_concurrent_request_counter_consistency` | 100 concurrent asynchronous requests fired simultaneously via `asyncio.gather`. | Atomic lock prevents lost updates: `total_requests == authorized + declined`. | **PASS** |

---

## 3. Deep-Dive Audit 1: Scriptable Success Rate Statistical Convergence

### 3.1 Mathematical Methodology
For a Bernoulli process with parameter $p$ and sample size $N$, the sample proportion $\hat{p} = \frac{k}{N}$ follows approximately a normal distribution $\mathcal{N}\left(p, \frac{p(1-p)}{N}\right)$ by the Central Limit Theorem.

The $3\sigma$ (99.73% confidence) bounds are defined as:
$$\text{Lower} = p - 3\sqrt{\frac{p(1-p)}{N}}, \quad \text{Upper} = p + 3\sqrt{\frac{p(1-p)}{N}}$$

### 3.2 Observed Results ($N = 2,500$ Trials per Rate)

| Configured Rate ($p$) | Standard Deviation ($\sigma$) | $3\sigma$ Tolerance Window | Observed Successes ($k$) | Empirical Rate ($\hat{p}$) | Deviation from Target | Status |
| :---: | :---: | :---: | :---: | :---: | :---: | :---: |
| **$0.2500$** ($25\%$) | $0.00866$ | $[0.2240, 0.2760]$ | $612$ | **$0.2448$** ($24.48\%$) | $-0.52\%$ | **PASS** |
| **$0.5000$** ($50\%$) | $0.01000$ | $[0.4700, 0.5300]$ | $1,262$ | **$0.5048$** ($50.48\%$) | $+0.48\%$ | **PASS** |
| **$0.7000$** ($70\%$) | $0.00917$ | $[0.6725, 0.7275]$ | $1,744$ | **$0.6976$** ($69.76\%$) | $-0.24\%$ | **PASS** |
| **$0.9000$** ($90\%$) | $0.00600$ | $[0.8820, 0.9180]$ | $2,253$ | **$0.9012$** ($90.12\%$) | $+0.12\%$ | **PASS** |

**Conclusion**: The PRNG outcome generation engine in `AcquirerSimulator` is mathematically unbiased and tracks configured success rates accurately across the entire probability continuum.

---

## 4. Deep-Dive Audit 2: Mid-Run Outage Step-Function Dynamics

The primary test of the simulation harness is whether an outage trigger changes behavior on the **very next call**, rather than taking a transition window or exhibiting trailing decay.

### 4.1 Step-by-Step Sequence Trace

```
Step 1–50:   [AUTHORIZED, AUTHORIZED, ..., AUTHORIZED] (Healthy Phase: p=1.0)
==> TRIGGER OUTAGE: sim.set_outage(active=True)
Step 51:     [DECLINED] (decline_code="ACQUIRER_OUTAGE")  <-- IMMEDIATE SHIFT ON CALL N+1
Step 52–100: [DECLINED, DECLINED, ..., DECLINED] (Outage Phase: 50 consecutive declines)
==> CLEAR OUTAGE: sim.set_outage(active=False)
Step 101:    [AUTHORIZED] (status="AUTHORIZED")           <-- IMMEDIATE RECOVERY ON CALL M+1
```

### 4.2 Telemetry Verification
- At step 101: `total_requests = 101`
- `authorized_count = 51` (50 pre-outage + 1 post-outage)
- `declined_count = 50` (all 50 occurring strictly during the outage window)
- `outage_declines = 50` (100% attributed to outage failure mode)

**Conclusion**: Zero latency leakage, zero trailing persistence. The state transition is an instantaneous step function, providing the exact stress stimulus Loom's bandit + PID controller needs to demonstrate smooth damping.

---

## 5. Contract Ambiguities & Edge Cases Surfaced

During rigorous verification, QA surfaced five key areas where the interface contract was underspecified or open to interpretation:

### 1. Ambiguity in Identifier Precedence (`acquirer_id` in URL Path vs. JSON Body)
- **Finding**: When invoking `/acquirers/{acquirer_id}/authorize`, the caller can pass a JSON body containing `{"acquirer_id": "other_id", ...}`.
- **Current Resolution**: The URL path parameter takes precedence; the simulator instance tied to the URL path processes the payment, and the response echoes that instance's ID.
- **Risk for Phase 3**: If a client mistakenly passes mismatched IDs, the request succeeds against the path route without warning.
- **Recommendation**: In Phase 3's router client, always align path and body `acquirer_id` or omit `acquirer_id` from the body when using keyed endpoints.

### 2. Idempotency & Duplicate `transaction_id` Re-Use
- **Finding**: Real acquirers (Stripe/Adyen) reject duplicate authorization requests with the same idempotency key / `transaction_id` or return the cached prior authorization outcome.
- **Current Resolution**: The simulator treats each `POST /authorize` call as an independent Bernoulli trial, regardless of whether `transaction_id` was previously submitted.
- **Assessment**: Acceptable for Phase 2 simulation, as the transaction generator in Phase 3 emits unique UUIDs. However, Phase 3 tests should not rely on the simulator to deduplicate retried transactions.

### 3. Outage Decline Semantics (HTTP 200 vs HTTP 503)
- **Finding**: Under the default `RETURN_DECLINE` behavior, the simulator responds with `HTTP 200` and payload:
  ```json
  {"status": "DECLINED", "authorized": false, "decline_code": "ACQUIRER_OUTAGE"}
  ```
- **Assessment**: This allows automated tests and transaction generators to run without catching transport exceptions. However, the Router client in Phase 3 must inspect `response.json()["authorized"]` rather than relying solely on `response.status_code == 200` to determine payment success.

### 4. Telemetry Reset Scope
- **Finding**: Does `POST /admin/reset` with no query parameters reset all acquirers or only the default acquirer?
- **Current Resolution**: `POST /admin/reset` resets all registered acquirers; `POST /acquirers/{id}/admin/reset` or `POST /admin/reset?acquirer_id={id}` resets only the target acquirer.

### 5. Multi-Tenant Telemetry Querying
- **Finding**: `GET /admin/state` without query parameters returns the default single acquirer state. To view all acquirers, `GET /admin/states` was implemented, returning `MultiAdminStateResponse` mapping all registered acquirers.

---

## 6. Open Risks Added to Decisions Log

The following operational risk has been appended to [`docs/decisions-log.md`](file:///d:/loom/docs/decisions-log.md):

- **[Phase 2 QA] Identifier precedence in multi-tenant authorization routing**: When using `/acquirers/{acquirer_id}/authorize`, the URL path takes precedence over any `acquirer_id` passed in the JSON body. Phase 3 router implementation must ensure client requests do not supply conflicting route identifiers.
- **[Phase 2 QA] Absence of transaction idempotency deduplication in simulator**: The simulated acquirer service executes an independent probabilistic draw on every `POST /authorize` call, even if the same `transaction_id` is replayed. Router retries in Phase 3/4 will be evaluated as new random trials rather than returning cached idempotent responses.

---

## 7. QA Sign-Off

Phase 2 Simulated Acquirer Service satisfies all functional requirements, statistical convergence criteria, and step-function outage dynamics specified in the PRD.

The service is fully scriptable, deterministic at boundaries, robust under high-velocity flapping, and verified for Phase 3 integration.
