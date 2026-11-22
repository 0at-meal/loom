# Loom v2: from "loses to a breaker" to a router that is hard to beat

> **Implementation note (2026-11-16, Stage 1).** Stage 1 (causes 1, 2 and 5, and F-17) is implemented on branch `audit-fail-fast`. It adds the technical gate (`router_core/gate.py`), one cascade retry with `RoutingResult.attempts`, and per-acquirer floors with a rate floor in `project_to_bounded_simplex`. All three are opt-in (`python -m router_core.server --fail-fast`, suite policy `loom_failfast`), and the served default is unchanged. On the 200 held-out seeds Loom fail-fast meets the "lower 95% bound ≥ −0.15 pp against the competent breaker" target in 20 of 26 scenarios (Loom served: 10). It misses both approval gray failures, both 2-acquirer capacity scenarios, `capacity_collapse_3_acquirers` and `soak_day_gap_1pp`. Numbers: `docs/results/suite.md` and the README. The design differs from this plan in three places, all changed on train seeds and recorded in the decisions log: a 429 is not gate evidence; when every gate is open, the acquirers that processed a payment most recently stay in the allocation; and the probe backoff is capped at 2 s after a probe that failed safely. Stages 2–5 are not started.

> **Implementation note (2026-11-16).** Stage 0 is implemented on branch `audit-policy-harness`: the policy core (`router_core/policy.py`), the baselines as policies (`baseline_router/policies.py`), the simulator world (`acquirer_sim/world.py`) and the scenario suite (`scripts/suite/`). Its measured starting line is in `docs/results/suite.md`. Stages 1–5 are not started. The status line below and the spike numbers are as written on 2026-11-15; the spike numbers are still estimates from uncommitted code, not results.

> **Status: proposal, not implemented.** Written 2026-11-15 against branch `audit-value-dispatch-dashboard` (HEAD `6e39b95`). No code, decisions-log entry or benchmark in the repo reflects this plan yet. The spike numbers below come from a throwaway in-memory simulation that is not committed, so they are estimates, not reproducible results.

## Context

Loom today loses to a 3-failure circuit breaker on hard outages (−3.09 pp PSR, 100 seeds), wins only on gray failures (+1.97 pp), and sends 37–44% of healthy traffic to a 1-pp-worse acquirer. The audit (`docs/AUDIT.md`) also showed a throwaway "competent" router (decline-aware breaker + one retry) beating both Loom and the static baseline. The goal is a redesign of the model, the routing harness and the settings that makes Loom at least as good as a competent breaker everywhere and clearly better where learning matters, with evidence strong enough to be tested on real traffic.

**Kept intact** (existing decisions): Thompson Sampling over wall-clock, mean-reverting Beta beliefs; the technical × approval factorisation; the pure PID step + simplex projection + deficit/stochastic actuation; value-scaled protection; injectable clock; append-only ledger; simulated acquirers only; pinned stack (numpy/scipy, no ML libraries).

### Why Loom loses today (root causes, not symptoms)

| # | Cause | Effect |
| :-- | :-- | :-- |
| 1 | No retry: a technical failure is a lost payment | Every exploration and every detection step costs a full payment |
| 2 | Detection is a slow belief ramp, then a symmetric PID ramp | ~11 failures absorbed per outage vs 3–4 for a breaker |
| 3 | PID setpoint is a one-hot of one noisy draw; measurement is its own output; clock is per transaction | A low-pass filter on noise that costs 1.45 pp and whose benefit (herd protection) is unmeasured |
| 4 | Approval memory is one 60 s half-life | Cannot resolve 1–3 pp gaps; permanent steady-state regret |
| 5 | 3% floor is a share of volume | Outage loss scales with TPS × duration |
| 6 | No context (issuer/BIN, method) and no cost | The regime where learned routing has its largest real upside is not modelled |
| 7 | Benchmark is 150 tx (10 s), one scenario, weak baseline | Long-memory gains are invisible; wins and losses are not credible |

### Evidence gathered for this plan

**Feasibility spike** (in-memory re-implementation, 200 seeds, not repo code; its Loom-v1 and static numbers match the README within 0.5 pp):

| Scenario | Static M=3 | Competent + retry | Loom now | Loom v2 sketch (gate + retry + probes) |
| :-- | :-: | :-: | :-: | :-: |
| Hard outage, fast-fail, 50/50/50 | 91.89 | 94.55 | 88.75 | **94.19** |
| Technical brownout (40% 5xx) | 88.19 | 94.42 | 91.57 | **94.15** |
| Approval gray failure (0.60) | 87.89 | 77.43 | 91.60 | **92.44** |
| Timeout outage (no safe retry) | 91.89 | 92.01 | 88.75 | 92.07 |

Steady state, 30 min at 15 TPS, share of traffic to the worse acquirer: current 60 s belief 22.6% (1 pp gap) / 8.1% (3 pp); multi-timescale mixture 12.3% / 2.0%; with a ledger warm start 8.6% / 1.0%. A static priority costs 0 when right and 1–3 pp forever when wrong.

Reading: gate + retry removes the hard-outage loss (−3.1 → +2.3 pp vs M=3). What remains against a competent breaker is a ~0.3 pp steady-state residual (causes 4, 6), and timeouts need in-flight signals (cause 2). Loom's real edge is approval degradation, which technical breakers cannot see.

**Published systems.** Juspay (sliding-window SR score + hedging + elimination; reports +1.15% SR in production) and Dream11 (non-stationary bandits; +0.92%, +10% during gateway degradation) both ship bandit routing; Adyen uses contextual bandits with logged propensities; orchestrators treat cascade-on-soft/technical-failure as standard. Loom lacks every one of these ingredients. Adaptive concurrency (AIMD) and SPRT/CUSUM are the standard tools for capacity and fast detection.

## Target pipeline

```
request ─► eligibility + technical GATE (CUSUM, wall-clock probes)      [new]
        ─► beliefs: technical × issuer(segment) × lift(acquirer|segment) [extended]
        ─► Thompson ALLOCATION (many draws → win-probability vector)     [changed setpoint]
        ─► PID governor: wall-clock, instant down / governed up,
           measured on realised dispatch, bounded by capacity limits    [re-signalled]
        ─► actuation (deficit | stochastic), value protection            [kept]
        ─► dispatch ─► CASCADE once on a safe technical failure          [new]
        ─► feedback, ledger with propensities, telemetry                 [extended]
```

## Stages (one `audit-*` branch and PR each; decisions-log entry first)

### Stage 0 — Measure first: policy core, honest baselines, scenario suite
Nothing else is credible without this. Closes F-01, F-15, F-30.

- **Policy protocol** (`router_core/policy.py`, new): sync, I/O-free `decide(ctx, now, exclude) -> Decision` (acquirer, full propensity vector, reason) and `feedback(decision, outcome, now)`. Extract the decision/feedback logic of `BanditRouter._route` (`router_core/router.py:256`) into `LoomPolicy`; `BanditRouter` stays as the async shell. Golden test: seeds 42/777 give decision-identical runs before and after.
- **Baselines as policies** (`baseline_router/`): existing breaker unchanged; `CompetentBreakerPolicy` ported from the audit's `CompetentRouter` (`docs/AUDIT.md:3095`: decline-aware, wall-clock half-open, sane all-tripped, one retry); `SRWindowPolicy` and `SlidingWindowUCBPolicy` as labelled re-implementations of the published Juspay and Dream11 approaches.
- **Simulator realism** (`acquirer_sim/`, all off by default so current numbers still reproduce): fast-fail vs timeout vs 429 taxonomy, background technical error rate, capacity (reject or collapse), issuer segments with acquirer × issuer interaction, common-mode issuer incidents, flapping, gradual degrade, common random numbers per `(seed, transaction_id)` so every policy sees the same draws and retries are correctly correlated.
- **Harness** (`scripts/suite/`: `des.py`, `scenarios.py`, `metrics.py`, `run_suite.py`): discrete-event virtual time (latency, timeouts, concurrency, bursty and 0.5–500 TPS) driving sync policies; parity test against the HTTP path. Reuse `summarize_paired` (`scripts/compare_psr.py:380`).
- **Scenario suite, pre-registered before any tuning**: hard outage (fast-fail, timeout), technical brownout, approval gray failure, steady state at 0/0.5/1/3 pp with right and wrong priority, capacity-limited failover (429, collapse; 2 and 3 acquirers), flapping, weak secondary, common-mode issuer incident, segment heterogeneity, in-flight concurrency, low/high/bursty TPS, and a day-long soak scored with the audit's revenue model.
- **Metrics**: PSR regret vs oracle, failed payments on a dead route, seconds to divert/restore, added hops and latency, net value after fees, ambiguous retries (must be 0); paired 95% CIs, train seeds for tuning, held-out seeds for reporting.

### Stage 1 — Fail fast, recover safely (causes 1, 2, 5; F-17)
- **Technical gate** (`router_core/gate.py`, new): per-acquirer CUSUM on technical outcomes; CLOSED / OPEN / HALF_OPEN. Trips in 2–3 failures on a hard outage and in ~5 tx on a 50% brownout, which a consecutive-failure breaker misses. In-flight requests older than k × p95 latency add evidence before the timeout fires. All-open → least-recently-opened, never "the primary".
- **Cascade** (`router_core/router.py`): one retry on the best other eligible acquirer for fast technical failures and 429s only; never on a timeout or other ambiguous outcome. `RoutingResult.attempts` records each attempt; each updates beliefs; the existing idempotency store (`router_core/idempotency.py`) keeps one result per `transaction_id`.
- **Time-based probing**: the 3% share becomes a probe rate (probes per second, exponential backoff 1 s → 30 s while OPEN). Probes pick the lowest-value payment in the window, reusing `ValueScaledExplorationConfig.calculate_shrinkage` (`router_core/value_policy.py:44`). `project_to_bounded_simplex` (`router_core/pid.py:135`) gains per-acquirer floors and caps.

### Stage 2 — Steady-state brain (cause 4; F-03)
- **Multi-timescale approval belief** (`router_core/state.py`): a bank of the existing wall-clock Betas at half-lives 60 s / 30 min / 12 h, mixed by predictive likelihood with fixed-share weights; reuses `step_approval` and `decay_beliefs`. Large shifts move weight to the fast member within seconds; small gaps are resolved by the slow one.
- **Warm start**: priors from the ledger (new `data_layer.cli export-priors`) or an operator `prior_psr` per route, the fair counterpart of a static priority order.
- **Thompson allocation as setpoint**: vectorised draws → win-probability vector, projected onto the feasible simplex before the PID (also fixes F-25).

### Stage 3 — Make the PID a controller (cause 3; F-08, F-09, F-16)
- `dt` in seconds (parameter already exists in `calculate_pid_step`); gains per second, so ramps no longer depend on TPS.
- Asymmetric: decreases follow the target at once; only increases are governed (recovery ramp, load shift onto a backup).
- Measured variable = realised dispatch share (wall-clock EWMA of actual dispatches, including protected, probe and retry traffic).
- **Capacity limits** (`router_core/capacity.py`, new): per-acquirer AIMD concurrency limit from 429s and latency inflation, fed to the projection as a cap. A 429 is throttling, not a technical failure.
- **Keep-or-cut gate**: PID on must beat PID off in the collapse scenarios and lose nowhere by more than noise; otherwise it runs only during transitions and the README says so.

### Stage 4 — Context and value (cause 6)
- Optional `issuer_id` / `card_bin` / `card_network` on `AuthorizeRequest`; configurable segment key.
- Belief chain `technical_a × issuer_s × lift_{a|s}` with soft blame: a decline moves `lift` by π = q(1−m)/(1−q·m), so an issuer-wide incident does not reshuffle acquirers. Segment beliefs shrink toward the acquirer-level parent using the existing mean-reverting decay form.
- Per-payment propensity π(a|s) ∝ w_a · p_s(a)/t_a: contextual choice whose aggregate still tracks the PID weights.
- Objective = expected net value: amount × P(success) − acquirer fee; fees on `AcquirerRouteConfig`.

### Stage 5 — Bridge to real traffic
- Ledger gains context, propensity vector, policy/config hash, attempt index (via `migrate_schema_sync`, `data_layer/sqlite_logger.py:58`).
- `scripts/replay_ope.py`: IPS / SNIPS / doubly-robust evaluation of a candidate policy on logged decisions, validated in the simulator against ground truth.
- Shadow endpoints (decide without dispatch, feedback later), fail-open to static priority on any policy error, kill switch.
- One `LoomPolicyConfig` for server and benchmark, so the served and benchmarked configurations stop diverging; `scripts/tune_policy.py` tunes on train seeds only.

## Acceptance (held-out seeds; replaces the current headline)

1. Against competent breaker + retry with a correct priority: lower CI bound of (Loom − competent) ≥ −0.15 pp in every scenario.
2. Wins with CI excluding 0 in: approval gray failure, technical brownout, wrong or unknown priority, segment heterogeneity, capacity collapse, flapping.
3. Ties or beats the SR-window and sliding-window-UCB reference policies in every scenario class.
4. Lowest annual lost GMV of all policies in the day-long soak at δ = 0 and δ = 1 pp.
5. Zero ambiguous retries; decision overhead p99 < 1 ms.

Any stage that misses its gate is reported as measured and cut or reworked, not tuned on the test seeds.

## Records this reverses (each needs a decisions-log entry before code)

Single-shot routing → one cascade; volume floor → probe rate; transaction-clocked PID with self-measurement → wall-clock PID on realised share; the 150-tx benchmark stops being the headline. Old README rows stay reproducible through `scripts/compare_psr.py`.

## Verification

- Per stage: `ruff check .`, `ruff format --check .`, `mypy .`, `pytest`, `npm --prefix dashboard test`, `npm --prefix dashboard run build`.
- Regression: golden decision-parity test for the Stage 0 refactor; `python scripts/compare_psr.py --n-seeds 100` still reproduces the current table when new features are off.
- Results: `python scripts/suite/run_suite.py --policies all --seeds 200 --split test --out-json docs/results/suite.json`; README tables generated from that JSON.
- Live check: `python scripts/run_demo.py --tps 15` + dashboard, trigger `HTTP_503` and `LATENCY_SPIKE` outages, confirm gate state, retries and probe cadence in `/health` and the event tape.
- New unit tests fail against the old code (gate trip counts, no retry on timeout, probe rate independent of TPS, common-mode incident leaves shares unchanged, OPE estimate within CI of simulator truth).

## Execution order and limits

Start with Stage 0, then Stage 1, on a new branch cut from `audit-value-dispatch-dashboard` (PR #7's dispatch code is a prerequisite); the uncommitted dashboard files in the working tree stay untouched. Stop after each stage with its numbers; no push or PR without a go-ahead.

Limit to state plainly: every number above and every gate is simulator evidence. "Could replace an existing router" becomes testable only at Stage 5, by replaying real logs and running in shadow.

## Sources

- [Juspay: A Control-Theoretic Approach to Dynamic Payment Routing (arXiv 2510.16735)](https://arxiv.org/pdf/2510.16735) · [Juspay routing engine blog](https://juspay.io/blog/juspay-orchestrator-and-merchant-controlled-routing-engine) · [juspay/decision-engine](https://deepwiki.com/juspay/decision-engine) · [Hyperswitch auth-rate routing](https://docs.hyperswitch.io/integration-guide/workflows/intelligent-routing/auth-rate-based-routing)
- [Dream11: Maximizing Success Rate of Payment Routing using Non-stationary Bandits (arXiv 2308.01028)](https://arxiv.org/abs/2308.01028)
- [Adyen: contextual multi-armed bandits for conversion](https://www.adyen.com/knowledge-hub/optimizing-payment-conversion-rates-with-contextual-multi-armed-bandits) · [Adyen: non-uniform exploration (arXiv 2412.00569)](https://arxiv.org/html/2412.00569v2)
- [Besson et al.: change-point detection for piecewise-stationary bandits (JMLR 2022)](https://jmlr.org/beta/papers/v23/20-1384.html)
- [Solidgate: cascading payments](https://solidgate.com/blog/cascading-payments/) · [Stripe authorization optimisation](https://stripe.com/en-pt/payments/authorization)
- [Envoy outlier detection](https://www.envoyproxy.io/docs/envoy/latest/api-v3/config/cluster/v3/outlier_detection.proto) · [Adaptive concurrency limits (GitLab design doc)](https://handbook.gitlab.com/handbook/engineering/architecture/design-documents/gitaly_adaptive_concurrency_limit)
