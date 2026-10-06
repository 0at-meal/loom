# Loom: Independent Adversarial Audit

> **Status note (added when this file was committed to `docs/`).** This audit describes the code at commit `8001acb`; its file paths, line numbers and results refer to that commit. Later pull requests address its findings: PR #1 corrected the README against §3 and §4 and fixed CI (part of F-14), and PR #2 adds the multi-seed benchmark harness and a data-driven dashboard card (F-15, F-06, F-11, F-29 in part). PR #3 replaces per-observation belief decay with wall-clock decay (F-23; F-34 was already fixed by PR #1). The text below is the audit as written, apart from trailing whitespace removed by the pre-commit hook.

**Audited commit:** `8001acb` (main, clean tree).
**Audit date:** 2026-10-01/02.
**Auditor:** Claude (lead), plus four parallel lens sub-agents (A Credibility, B Engineering, C Claim verification, D Feasibility).

Every Critical and Major finding below was re-run or re-read by the lead before inclusion. Any figure a lens reported that the lead could not reproduce has been replaced with the lead's own number.

**Conventions**
- **RAN** means the auditor executed it. **READ** means code or doc inspection only.
- **[DOMAIN KNOWLEDGE, confidence]** marks claims about real payment systems. These come from general industry knowledge, not from this repo, and were not verified here.
- "pp" means percentage points of payment success rate (PSR).
- All execution happened in scratch copies under `%TEMP%\loom-audit*`. Nothing in the repo was modified except the creation of this file (see the Appendix for `git status`).

**Project's stated goal, one sentence:** a payment-routing layer that "treats acquirer selection as a live control problem", meaning a Thompson-sampling bandit that learns acquirer health plus a PID controller that smooths reallocation. Its success metric is PSR lift over a static rule-based router during simulated outages (docs/prd.md:61-64), and its target audience is a demo/portfolio reviewer (docs/phase8-tech-lead-review.md:19, "portfolio piece").

---

## 1. Verdicts

### (a) Engineering and honesty verdict: oversold, with real defects underneath competent-looking code

The core math is implemented carefully and matches its specs (RAN):
- The PID step follows the spec.
- The bounded-simplex projection never violated its floor in 200k fuzz cases.
- The deficit scheduler is exact.

The headline evidence does not support the headline claim:
- **The baseline it beats is a strawman.** The only baseline Loom beats on PSR is a breaker that trips on a single issuer decline and then routes everything to the known-dead primary. Against the repo's own standard M=3 breaker, Loom loses on PSR in about 88% of seeds (−3.0 pp, 95% CI [−3.4, −2.6], n=200; F-01).
- **The "smoothing" claim is measured, but its benefit is not.** It is quantified on a metric that compares a continuous weight against a 0/1 indicator. The benefit (herd protection) is never measured, because the simulator has infinite capacity (F-08).
- **Several published numbers do not reproduce from their own scripts, and some appear in no producer at all.** These include the Phase 7 results row, the dashboard's "+1333 bps" gray-failure card (actual: +133 bps), and the Phase 4 recovery posterior values (F-06).
- **The shipped server does not log, publish to Redis, or persist state.** It writes zero rows to SQLite, so "every decision is logged, permanently" is false for the product as run (F-04).
- **The "four-role engineering loop" is one author's single-day role-play.** QA reports were committed 7 minutes after the code they certify, and the "tech-lead approval for production" came 2 minutes after that (F-19).
- **Fixing either strawman choice flips the headline.** Under the issuer-decline fix, the breaker reaches 93.33% vs Loom's 86.00%. Under the fallback fix, it reaches up to 94.00%. Both were RAN.

The honest summary: a carefully built simulator-and-visualisation demo of a routing policy that the evidence shows is worse than a simple, correctly configured circuit breaker in hard outages. Its one genuine (and under-reported) win is gray failures: +2.4 to +2.7 pp over the naive breaker.

### (b) Deployability verdict: **NOT DEPLOYABLE**

Loom cannot be put on a PSP's authorization path in any mode today. These problems are structural, not polish:
- **Wrong failure signal.** It learns from the wrong signal: every issuer or customer decline counts as an acquirer failure (F-02).
- **Permanent regret.** Its per-observation forgetting caps memory at 20–50 observations. In steady state it therefore sends 19–47% of traffic (depending on configuration and gap) to an acquirer that is 1–5 pp worse, and this does not improve with volume (F-03).
- **Outage handling and recovery.** It loses to a standard breaker in hard outages. Its fixed 3% exploration floor bleeds failures in proportion to TPS × outage duration (F-01, F-17).
- **Unsafe hot path.**
  - One stalled dashboard WebSocket freezes all routing (F-05).
  - An acquirer authorization can surface to the merchant as an error, with no idempotency layer (F-07).
  - Persisted Redis state makes a restarted worker unable to route (F-13).
- **Missing controls.** It has no eligibility rules, kill switch, fail-open path, shadow mode, cost model, auth, or segmentation below the acquirer level (F-22, Gap table).

**Strongest reason FOR adoption:** a deterministic, scriptable multi-acquirer simulator plus benchmark harness, plus a bandit that does beat a consecutive-failure breaker under gray-failure brownouts. Lead RAN: +2.42 pp over 30 seeds, Loom winning 20/30. Lens C: +2.71 pp, CI [2.08, 3.35], over 100 seeds. These are useful as a policy test bench and as a teaching artifact.

**Strongest reason AGAINST:** on the repo's own scenarios, a throwaway "competent" router (tech-decline-only breaker, wall-clock probes, one retry) beats both Loom and the static router in every hard-outage scenario. It wins by +1.9 to +2.9 pp vs static and roughly +5 pp vs Loom (RAN, 40 seeds), and Loom's steady-state regret dominates any outage savings in the revenue model (§6).

The idea of adaptive acquirer routing is mainstream [DOMAIN KNOWLEDGE, high], so the gap is not the idea. The gap is that Loom's specific formulation (auth-rate-as-health, per-selection decay, transaction-clocked PID, volume-based exploration floor) is the wrong one. Closing it is a redesign of the estimator and failure taxonomy, not tuning.

---

## 2. Top 5 issues (ranked)

1. **[F-01] The headline "+1000 bps" lift exists only against a strawman baseline.** Against the standard M=3 breaker Loom loses on PSR (−2.6 to −3.3 pp, CIs exclude 0, about 88% of seeds). The strawman's collapse is produced by two baseline design choices: tripping on issuer declines, and exhaustion fallback to the dead primary. The pre-registered PRD metric (outage-window PSR vs the default baseline) is −1000 bps (F-20).
2. **[F-02 / F-03] The estimator learns the wrong thing, and forgets too fast to learn the right thing.** Issuer declines are scored as acquirer failures. The γ=0.95/0.98 per-selection decay gives permanent regret of 19–47% of traffic to a worse acquirer. In the revenue model this always-on loss outweighs every outage saving (§6).
3. **[F-04] The shipped server has no data layer.** There is no SQLite ledger, no Redis state, and no Pub/Sub publisher. 968 live transactions produced 0 rows. "Every decision is logged, permanently" and the Phase 5 architecture are absent from the product as run, and off-policy evaluation or audit of live decisions is impossible.
4. **[F-05] Payment routing is coupled to dashboard telemetry.** `/route` awaits `ws.send_text` for every connected client, with no timeout. One stalled client froze routing after 226 requests (5 s timeout).
5. **[F-06] Published numbers that do not reproduce.**
   - The Phase 7 README row (90/78/90, 129/150) comes from no producer; its own script gives 90/72/94, 128/150.
   - The dashboard shows a gray-failure lift of +1333 bps (baseline 68.67%) that appears nowhere else. The reproducible values are +133 bps and baseline 88.67%.
   - The Phase 4 "α 6.94→8.89, E[θ] 0.617→0.720, routing restored" values are wrong. Actual: 0.496→0.544, and Alpha stays at the 3% floor.

---

## 3. Findings

Lens sub-IDs (A-xx, B-xx, C-xx, D-xx) are cross-references to the lens reports that originated or corroborated each item. "Lead-verified" means the lead auditor independently re-ran or re-read the evidence.

### Critical

| ID | Severity | Lens | Type | Location | Evidence | RAN/READ | Why it matters | Suggested fix |
|---|---|---|---|---|---|---|---|---|
| F-01 | Critical | A, C | Incompetent / Incorrect | README.md:15-33; scripts/compare_psr.py:36,97; baseline_router/router.py:228-235 (exhaustion fallback to `priority_order[0]`), :282 (`success = payload.authorized`) | **Seed 42 (lead RAN):**<br>• M=3: 92.00 vs 86.00 (−600 bps)<br>• M=1: 76.00 vs 86.00<br>**Lead RAN, 100 seeds:**<br>• Loom − M=3 = −2.58 pp, CI [−3.17, −1.99], Loom wins 8/100<br>• Loom − M=5 = −1.49 pp<br>• Loom − M=1 = +14.94 pp<br>**C RAN, 200 seeds:** −2.99 pp [−3.36, −2.62], wins/ties/losses 14/11/175.<br>**Strawman mechanics (lead RAN, seed 42, M=1):**<br>• Breaker ignoring issuer `DO_NOT_HONOR` → baseline 93.33%<br>• All-tripped fallback to most-recently-tripped route → 94.00% (82.00% for a cooldown-respecting variant)<br>• Loom → 86.00%<br>**M=1 trace:** a single DO_NOT_HONOR on Beta at Tx57 sends Tx58–86 to dead Alpha. | RAN | The only PSR win is manufactured by baseline configuration, and the realistic comparison is a consistent loss. [DOMAIN KNOWLEDGE, high] Acquirer-health breakers key on technical / acquirer-side errors, not issuer declines. | Make the default comparison a decline-code-aware breaker with a sane all-tripped policy. Report multi-seed paired differences with CIs. Lead with whichever result comes out. |
| F-02 | Critical | B, D | Incorrect | router_core/router.py:286-296; baseline_router/router.py:278-283; acquirer_sim/simulator.py:187-201 | **Code:** `success = payload.authorized`, so DO_NOT_HONOR (issuer decline) is a failure (B RAN: 20/20 recorded as failure). This is the same mistake README.md:29 blames on the static router.<br>**D RAN, 30 seeds, two technically identical acquirers, correlated issuer declines:**<br>• Loom flips its majority acquirer 159–184 times per 2000 tx<br>• 2–4 episodes where a healthy acquirer holds ≤10% of traffic for ≥20 tx<br>• A low-quality-traffic burst swings Alpha's weight 0.15↔0.88<br>• With Beta 2.5 pp technically worse, Loom reaches 86.8% PSR at ~60% Alpha; a decline-code-aware router reaches 87.6% (oracle 87.56%) at 98% Alpha | RAN | [DOMAIN KNOWLEDGE, medium] Most declines are issuer, customer or fraud driven and acquirer-independent. Routing on them chases traffic-mix noise and hides real technical differences. | Classify responses (technical / soft-retryable / issuer / fraud). Feed only acquirer-attributable outcomes into health, and model approval rate per segment separately. |
| F-03 | Critical | D (lead-verified) | Incompetent | router_core/state.py:104-111 (decay only on the selected arm, per observation); server default γ=0.98, benchmark γ=0.95 | Lead RAN: 10 seeds, 2000 tx, no outage, Alpha .95. Share of tx 500–2000 sent to the worse Beta:<br>**Bench (γ=0.95, deficit):**<br>• Beta .94: 46.3% (sd 5.3)<br>• Beta .92: 38.6%<br>• Beta .90: 30.5%<br>**Live (γ=0.98, stochastic):** 46.8% / 29.2% / 19.2%<br>**Same pipeline with γ=0.9999:** 38.9% (sd 31.6, i.e. unresolved at 1 pp in 2000 tx) / 11.3% / 4.3%<br>D RAN at 50k tx: Loom still sends 42% to a 1 pp worse acquirer. | RAN | Effective memory 1/(1−γ) = 20–50 observations cannot resolve 1–5 pp gaps, so regret is permanent and proportional to GMV. It is the dominant term in the revenue model (§6). The README's warm-up rows already show it: Loom 90% vs baseline 94% on healthy acquirers. | Separate the two jobs: a fast breaker for outages, and a long-memory or wall-clock-windowed estimator (thousands of observations) with hysteresis for quality. Use wall-clock decay applied to all arms. |
| F-04 | Critical | C, B, D (lead-verified) | Not demonstrated / Incorrect | router_core/app.py:33-60; router_core/server.py:117-150; scripts/run_demo.py; README.md:99-101,173,495 | **Lead RAN:** `create_router_app(build_router_config(parse_args([])))` gives `metrics_logger=None`, `event_publisher=None`, `registry=BanditStateRegistry` (in-memory).<br>**C RAN:** 556 tx via Pathway B plus 412 via run_demo, then `data_layer.cli status` gives `Logged Transactions: 0`.<br>`grep` finds `metrics_logger=`, `event_publisher=` and `RedisBanditStateRegistry(` only in scripts/ and tests/. | RAN | The audit trail, Redis persistence, multi-worker consistency and Pub/Sub telemetry exist only in test harnesses. A restart loses all beliefs. Live decisions cannot be audited or replayed. | Wire MetricsLogger, publisher and an optional Redis registry into the app lifespan, plus an end-to-end test asserting row counts after `/route`. |
| F-05 | Critical | B (lead-verified) | Incorrect | router_core/app.py:67-78 (`broadcast_payload`), :205-209 (`/route` awaits broadcast) | **Lead RAN** (`ws_block.py`): uvicorn server plus one raw WebSocket client that completes the handshake and never reads, with a small receive buffer. Output: `POST /route #226 TIMED OUT after 5s (blocked by stalled websocket)` (median latency before the stall 2.85 ms). | RAN | A dashboard tab on a sleeping laptop halts all payments. The acquirer may already have authorized, and the client times out and retries (see F-07). | Broadcast off the request path: per-client bounded queues with drop-oldest, or `asyncio.wait_for` plus eviction. Never await telemetry inside `/route`. |
| F-06 | Critical | A, C (lead-verified) | Incorrect | README.md:24,31,35; docs/phase7-qa-report.md:57-60; dashboard/src/components/BaselineComparisonCard.jsx:40-48; docs/phase4-qa-report.md:28,78 | **Phase 7 row:** README says 86.00%, 90/78/90, 129/150, 11 failures, 13 flips. `scripts/qa_phase7_live_verification.py` (C RAN ×4, also at 9742b96) and the lead's in-process replica give 85.33%, 90/72/94, 128/150, 7 failures, 22 flips. It uses 3 acquirers (0.95/0.90/0.85) and stochastic actuation, not the "identical schedule".<br>**Dashboard gray-failure card:** Loom 82.00%, baseline 68.67%, "+1333 bps". `git grep` finds these values nowhere else. README.md:21 says the baseline is 88.67%. Lead RAN at seed 42: baseline 88.67, Loom 90.00, i.e. **+133 bps**.<br>**Phase 4 recovery:** "α 6.94→8.89, E[θ] 0.617→0.720… restored routing". Lead RAN trace: 0.496→0.521→0.544, and Alpha's weight stays at the 0.03 floor (C: in 30/30 seeds). | RAN | Headline surfaces (README results table, live dashboard) show numbers no code produces. A reviewer who finds one will distrust every number. | Generate every published number from a committed script writing JSON. Have the dashboard read that JSON. Delete numbers without a producer. |

### Major

| ID | Severity | Lens | Type | Location | Evidence | RAN/READ | Why it matters | Suggested fix |
|---|---|---|---|---|---|---|---|---|
| F-07 | Major | D, B (lead-verified) | Incorrect | router_core/router.py:284-331 (transport `except` covers only `TimeoutException, NetworkError`; `record_outcome` unguarded after dispatch); no idempotency anywhere (`git grep -i idempoten` → only a docstring and a QA note at docs/decisions-log.md:1316) | **Lead RAN (B exp5):**<br>• Acquirer returns 422: 50/50 exceptions, 0 outcomes recorded.<br>• Acquirer returns 200 with a non-JSON body: `JSONDecodeError`, 0 outcomes.<br>• `RemoteProtocolError` propagates; 0 outcomes recorded.<br>**D RAN:** a registry exception after the acquirer authorized gave HTTP 500 to the client with `authorized_count={'acquirer_alpha': 1}`. | RAN | The merchant sees an error for a payment the acquirer approved, and a retry risks a duplicate authorization. A faulty acquirer is never penalized. | Treat any unexpected acquirer response as an ambiguous technical failure. Make post-dispatch state updates best-effort. Add a router idempotency store keyed by `transaction_id`. |
| F-08 | Major | B, A, C, D | Incorrect (claim) / Not demonstrated | router_core/router.py:200-217; pid.py:248-303; README.md:3,30-33,471; scripts/compare_psr.py:115-119 | **What the PID controls:**<br>• Controlled variable: the software allocation vector w.<br>• Setpoint: a one-hot of this transaction's single Thompson draw.<br>• Measurement: w itself.<br>• The realised dispatch and acquirer outcomes enter only via the bandit posterior.<br>B RAN: fed i.i.d. one-hot targets, the output mean equals the win probabilities, i.e. a low-pass filter on Thompson win indicators.<br>**Δw "8.5×":** compares Loom's continuous weight to the baseline's 0/1 dispatch indicator. On the same indicator Loom also jumps 100% (C RAN, 200/200 seeds).<br>**Ramp is transaction-clocked (D RAN):** ≈34–45 s at 0.5 TPS (two runs), 1.3–1.4 s at 15 TPS, 0.06–0.07 s at 1000 TPS.<br>**Herd protection never measured** (infinite capacity). D's stylised capacity model at 100 TPS: PID ≈ raw bandit (2 acquirers, 429s), PID worse by 2.4 pp (3 acquirers, 429s), better by 6.5 pp (3 acquirers, collapse). A capacity-aware AIMD router beats PID by 3–7 pp.<br>**PID costs PSR vs the raw bandit:** −1.71 pp, CI [−1.99, −1.44], 100 seeds (C RAN). | RAN + READ | "Treats acquirer selection as a live control problem" is a metaphor for EMA smoothing. The novel component's benefit is asserted and its cost is measured. | Call it smoothing. If herd protection is the goal, model capacity (429s, concurrency) and use explicit admission control on the secondary, measured in wall-clock terms. |
| F-10 | Major | B, lead | Not demonstrated / Incorrect | router_core/router.py:181-234; router_core/value_policy.py; docs/phase8-tech-lead-review.md:5,61-69; tests/router_core/test_qa_phase8_value_scaled.py, test_value_scaled_exploration.py, scripts/qa_phase8_controlled_test.py | **Lead RAN, 30 seeds, high-value tx during an outage on Alpha.** High-value tx sent to the dead acquirer, with scaling off → on:<br>• Deficit actuation: 1.23 → 1.53 of 10<br>• Stochastic actuation: 1.40 → 1.10 of 10<br>**B RAN, PID on:** P(α\|high)=0.888 vs P(α\|low)=0.876.<br>**Not tested with PID:** none of the Phase 8 test/QA files set `pid_config`.<br>**Review's mechanism doesn't exist:** the review explains the result via "μ̂ = μ_Bayes × (0.5 + 0.5·H)". The code uses `alpha/(alpha+beta)` with no health term. It is labelled "APPROVED FOR PRODUCTION". | RAN + READ | With the shipped default (PID on), value-scaling does not change where a high-value payment goes. The approval rests on a mechanism the code does not contain. | Apply the policy at dispatch (e.g. high-value tx go to the argmax posterior mean, bypassing the categorical/deficit draw). Test with PID on. Retract the review's explanation. |
| F-11 | Major | A, B (lead-verified) | Incorrect (overclaim) | README.md:101,311,316,495; data_layer/schema.sql:80-102; data_layer/cli.py:513-525; scripts/compare_psr.py:41-45; data_layer/config.py:67 | **Lead RAN:**<br>• Blocked UPDATE raises an ordinary `sqlite3.IntegrityError`, which can be caught.<br>• After `DROP TRIGGER prevent_transactions_update` the UPDATE succeeds.<br>• After `DROP TRIGGER … delete`, DELETE leaves 0 rows.<br>**B RAN:**<br>• `INSERT OR REPLACE` rewrote a row to `('FORGED','DECLINED')` with no trigger firing.<br>• `DROP TABLE` works.<br>• File replacement works.<br>• `reset-demo --force` leaves 0 rows.<br>**compare_psr deletes the ledger:** it `os.remove()`s `loom_metrics.db`, the default ledger path, against CONSTITUTION.md "Never delete or overwrite the SQLite metrics log". | RAN | "Cannot be tampered with" and "uncatchable ABORT" are false. The triggers prevent accidents, not tampering, and the repo's own scripts bypass them. | Reword the claims. Use a distinct benchmark DB name. For tamper-evidence use a hash chain or WORM/off-host storage. |
| F-12 | Major | B, C (lead-verified) | Incorrect | data_layer/sqlite_logger.py:309-329, 339-355, 369-385, 401-415 | **Lead RAN (B exp10/10b):**<br>• `close()` cancels the drain task while it holds an in-hand batch: logged 30, persisted 0. With default config, 140 of 200 records lost across 20 trials.<br>• One duplicate `transaction_id` rolls back the whole `executemany` batch: persisted 1 of 20.<br>• A full queue drops silently: queue size 5, 100 logged, 5 persisted.<br>• Logging before `start()` drops with only a warning. | RAN | The logger loses data on normal shutdown and on any client retry, even if it were wired in (F-04). | Sentinel-based shutdown that awaits the drain. `INSERT OR IGNORE` or per-row fallback. Backpressure or a dead-letter queue with a drop counter metric. |
| F-13 | Major | B, D (lead-verified) | Incorrect | data_layer/redis_state.py:240-345, 495-501; router_core/router.py:45-50; router_core/app.py:129-152; README.md:99; docs/decisions-log.md:88 | **Lead RAN (B exp7, fakeredis):** a second or restarted `BanditRouter` on the same Redis has `locally registered arms=[]` and fails with `ValueError: max() arg is an empty sequence`, returned as HTTP 422.<br>**No Lua:** `grep -i lua` finds no code. It is WATCH/MULTI on a synchronous client with `time.sleep`, the alternative the decision log rejected. D RAN: 7 round-trips per tx, and 2967 and 3109 of 4800 updates (two runs) escaped as `WatchError` under 16-thread contention (fakeredis, indicative only).<br>**Forwarder dies silently:** `redis.exceptions.ConnectionError` is not a builtin `ConnectionError`, so the forwarder dies after about 49 s and shutdown raises (B RAN). | RAN + READ | The scenario Redis exists for (restart, multiple workers) cannot route. The event loop blocks on Redis I/O. Docs misdescribe the implementation. | Hydrate all configured arms. Use `redis.asyncio` and an atomic Lua or `HINCRBYFLOAT` update. Catch `redis.RedisError` with backoff. Add a `route()`-after-restart test. |
| F-14 | Major | B, C (lead-verified) | Incorrect | pyproject.toml:13-35 (unpinned; httpx only in `[dev]`), :116-123 (`filterwarnings=["error", …]`); .github/workflows/ci.yml | **Lead RAN, fresh venv:** `pytest` gives 2 collection errors (`StarletteDeprecationWarning`, a UserWarning subclass, not covered by `ignore::DeprecationWarning`). The same happens with the author's own `.venv` interpreter.<br>With `-W ignore::starlette…` it gives 252 passed.<br>**Lead RAN on a clean `git archive`:**<br>• `ruff check`: clean<br>• `ruff format --check`: 9 files would be reformatted<br>• `mypy .`: 18 errors in 3 files<br>**C RAN:** fresh `pip install -e .` (README step) fails with `ModuleNotFoundError: httpx` for the router and compare_psr. | RAN | CI would be red today on three steps. The README quickstart ("every command… tested and verified") fails on a clean machine. "N/N tests green" is not reproducible. | Lock dependencies, move httpx to runtime deps, fix the warning filter, and drop the `Redis[...]` generics. |
| F-15 | Major | A, C, B | Not demonstrated | scripts/compare_psr.py (seeds hard-coded, no `--seed`); scripts/tune_pid_gains.py:54,74; acquirer_sim/simulator.py:265-270; router_core/pid.py:66 vs docs/decisions-log.md:67 | **Statistics:**<br>• Single seed, n=150, no CI anywhere. A-04: SE(86 vs 92) ≈ 3.6 pp; outage window SE(72 vs 82) ≈ 8.4 pp, about 1.2 SE.<br>• PID gains were tuned on the benchmark's own seeds 42/777 (in-sample).<br>**Cooldown sensitivity (A RAN, seed 42):** cooldown N ∈ {5…40} makes the M=1 "lift" range from +200 to +1867 bps.<br>**Correlated seeds:** seed s+1 Alpha = seed s Beta (B RAN).<br>**Benchmarked config ≠ shipped config:** γ .95, deficit vs γ .98, stochastic. The shipped config loses to M=3 in 83/100 seeds (C RAN). | RAN + READ | The published numbers are one draw, from a non-shipped configuration, with in-sample tuning. | `--seed` plus multi-seed paired CIs with common random numbers (`SeedSequence.spawn`). Benchmark the shipped config. Publish M × N sweeps. |
| F-16 | Major | B, D (lead-verified) | Not demonstrated | router_core/router.py:176-234 vs 327; router.py:118-124 (one shared `httpx.AsyncClient`, max 100) | **In-flight lag (lead RAN, B exp5):** 200 concurrent requests during a 503 outage send 98 (stochastic) / 114 (deficit) to the dead acquirer, vs 15 / 12 sequentially. All decisions are made before any outcome.<br>**Pool coupling (load-dependent):** `httpx.PoolTimeout` subclasses `TimeoutException` (RAN), so the router's transport `except` books pool exhaustion as an acquirer failure. With Alpha latency-spiking (1.8 s): D's original run at concurrency 200 charged healthy Beta and Gamma 146 `PoolTimeout` + 32 `ConnectTimeout`. Lead re-runs at concurrency 200 produced **0** such errors (twice). At concurrency 400 the lead got 873 `PoolTimeout` + 113 `ConnectTimeout` on healthy Beta and Gamma. | RAN | Adaptation claims were measured only with serial traffic. One slow acquirer poisons healthy acquirers' health. | Virtual-loss / in-flight accounting. Per-acquirer pools (bulkheads). Never attribute client-side pool errors to an acquirer. |
| F-17 | Major | D | Incompetent | router_core/pid.py `min_allocation=0.03`; state.py (unselected arms never update) | **Failed tx on the dead acquirer per outage (D RAN, discrete-event re-implementation verified decision-identical to `BanditRouter` in 10/10 seeds), Loom vs k=3 breaker with a 30 s wall-clock probe:**<br>• 15 TPS, 60 s: 34.8 vs 6.3<br>• 15 TPS, 600 s: 278 vs 24<br>• 1000 TPS, 600 s: 18,076–18,088 vs 75–83 (two runs)<br>**Slow failback when the secondary is worse (.88):** Loom PSR 85.7 vs static 91.7, with 8–14% of traffic back on Alpha at the end. At 0.5 TPS, Alpha does not reach 50% within 10 min.<br>**In Loom's favour:** at 0.5 TPS the breaker's 30 s probes leak more than Loom's floor (20.9 vs 16.5). | RAN | Exploration cost scales with volume × duration instead of time. Failback to a better primary is slow. | Time-based half-open probing while a breaker is open. Promote failback on probe success with a time-bounded ramp. |
| F-19 | Major | A (lead-verified) | Not demonstrated / Incorrect | git log; docs/persona/*.md; README.md:371-402; docs/decisions-log.md:84,135 | **Lead RAN, `git log`:**<br>• All 16 commits by one author, no merges or PRs.<br>• Phase 8: architect 23:31:51 → backend 23:46:23 → QA 23:53:47 → tech-lead "KEEP (APPROVED FOR PRODUCTION)" 23:55:37.<br>• Phases 1–7 each bundle spec, code, tests, QA report and justification in one commit.<br>**PID-wiring defect:** `server.py` has zero PID references at cbe9a59, bd08c24 and ecc7a02, which span three gate "certifications".<br>**Unsupported presets:** the README's "eliminated untuned presets Kp=0.40, Ki=0.05, Kd=0.10" appear in no commit's code (history grep of all *.py; only a unit test uses kp=0.4, ki=0.0, kd=0.40). Pre-fix defaults were 0.20/0.01/0.10 (`git show cbe9a59:router_core/pid.py`).<br>**AI role prompts, undisclosed:** personas are written as AI-assistant role prompts; the README never says so. | RAN + READ | The "disciplined four-role loop" and "binding Gate Certifications" provide no independent verification, and the README's showcase story is partly inaccurate. | Describe the process honestly (single author, AI-assisted role prompts). Drop certification language. Add entry-point integration tests to gates. |
| F-20 | Major | A | Incorrect (retrofitted headline) | docs/prd.md:61-64,83; docs/decisions-log.md:133,1306,1339-1345 | The PRD warns to define "good enough" lift first so the target "isn't retrofitted". After the M=3 loss, the decisions log says the 86 vs 92 result "cannot be used as the naive headline" and features M=1 instead.<br>**PRD's own metric:** outage-window PSR vs default M=3 is 72% vs 82% = −1000 bps (RAN). | RAN + READ | The headline scenario was selected after the default comparison was lost. | Restore the pre-registered metric as the headline, with all M values side by side at equal prominence. |
| F-21 | Major | C, A, B | Incorrect (feature claims) | README.md:36-40, 66, 90, 105-106; router_core/app.py:256-277; dashboard/src/hooks/useLoomTelemetry.js; scripts/qa_phase7_live_verification.py | **"6.73 ms frame delivery":** actually times `POST /route` through WS receipt. True push measured from the server timestamp: mean 0.5–0.7 ms, max 1.2–2.0 ms (C RAN; lead re-run). n=150, serial, no warmup.<br>**Trigger RTT and reconnect are single samples.** "Reconnect 3.59 ms" is a Python client; the dashboard's backoff starts at ≥500 ms.<br>**"SQLite historical bootstrap":** BOOTSTRAP sends only in-memory `get_all_states()`, and nothing reads SQLite.<br>**"Ring buffer N=200, rAF 60 FPS":** no `requestAnimationFrame` anywhere; the history is a 120-element array copy.<br>**"<2 µs enqueue":** never measured in repo. C measured about 0.7 µs mean. | RAN + READ | Architecture and performance claims describe features that do not exist, or mislabel what was measured. | Rename metrics, increase n, add warmup. Implement or delete the bootstrap / rAF claims. |
| F-22 | Major | D | Not demonstrated | README.md:462-466 (non-goals); router_core/app.py:164-167 (CORS `*` with credentials), :297-405 (admin proxies on the routing service); `git grep -i "kill.switch\|shadow\|eligib"` on *.py → no hits | No eligibility rules, merchant pinning, kill switch, fail-open default acquirer (a `route()` exception becomes a 500), shadow mode, cost model, volume commitments, authN/Z, or admin-plane separation. | READ | [DOMAIN KNOWLEDGE, high] These are table stakes before any PSP pilot. Several are explicitly out of scope per the README, which is honest but confirms non-deployability. | See §5 and §7. |

### Minor

| ID | Severity | Lens | Type | Location | Evidence | RAN/READ | Why it matters | Suggested fix |
|---|---|---|---|---|---|---|---|---|
| F-18 | Minor | B | Incorrect | router_core/app.py:310-339,355-367,387-403; dashboard/src/hooks/useSimulatorControls.js:47-80; tests/router_core/test_phase7_dashboard_integration.py:58-103 | If the simulator is unreachable, the proxies return HTTP 200 with fabricated `outage_active` and still broadcast "Outage injected". The tests assert the fake-success path (each takes 2.06 s on an unreachable URL). | RAN + READ | Operators see outages that never happened. | Return 502/503 and broadcast only on upstream success. Test against an in-process sim. |
| F-09 | Minor (downgraded from Major) | C, A | Incorrect | docs/phase4-qa-report.md:22-28,41,74; README.md:31,399-402; docs/phase4-pid-spec.md:425 | **Flip gate is met on its own definition (lead RAN, 50 seeds):** the spec's gate is "≤2 transitions in continuous allocation" (pid-spec:425). w_A crosses 0.5 a mean of 0.94 times during the outage (max 3; 1 at seed 42). Dispatch-level route flips do rise (13.1 PID vs 7.2 raw bandit), which matters only if dispatch flips are the concern. The named test `test_pid_eliminates_crossover_flapping` does not exist (`git grep` → 0).<br>**Not monotonic:** TC-QA-401 says the curve "eases monotonically". w_A rises 3.1 times on average after the outage starts (peak +9.6 pp at seed 42, max +30.8 pp) toward the dead acquirer.<br>**"Clamping strictly necessary" not demonstrated closed-loop (lead RAN `qa_pid_comparison_and_windup_stress.py`):** bounded and unbounded integrators give identical results (`Recovery Delay: 100 tx`, `Alloc at Recovery End: 0.0300`, 3 probe tx). The benefit appears only in the open-loop step script. | RAN | QA claims overstate the smoothing quality and the windup benefit. The flip-gate criticism in the first version of this audit was wrong (see §13). | Measure monotonicity in code over many seeds. Report closed-loop windup results. |
| F-23 | Minor | B, C | Incorrect | README.md:494; router_core/state.py | "Weighted toward the last minute": memory is about 50 observations of that arm (γ=0.98). At 15 TPS that is ~3.5 s for the leader and ~111 s for a 3% floor arm; idle arms never decay. `DECAY_HALF_LIFE_SEC` is never read; `calculate_gamma_from_half_life` is never called. | RAN (calc) + READ | The claim is wrong in both directions and depends on traffic. | Use wall-clock decay, or describe the per-observation semantics. |
| F-24 | Minor | A, B, C | Not demonstrated / Incorrect | docs/phase5-qa-report.md:20-24; decisions-log.md:99; data_layer/redis_pubsub.py:533-545; README.md:496 | "Zero drops": measured with in-process fakeredis, 150 serial events, subscriber already attached. Pub/Sub is at-most-once by design. `AsyncEventSubscriber` validates every message as a `RoutingEvent`, so health alerts are dropped (B RAN). There is no publisher in the live path. | RAN + READ | "Reacts… instantly (Real-time Redis Pub/Sub)" is not how the shipped system works. | Qualify the claim. Validate per channel. Test with real Redis and a slow subscriber. |
| F-25 | Minor | B | Incorrect | router_core/pid.py:255-267; router.py:202-203 | Mean-centring after clamping lets \|I\| reach 1.31 > I_max=1 for K≥3 (B RAN; the tests check only K=2). A one-hot setpoint is unreachable under a 3% floor, so the integrator sits saturated whenever the bandit is confident (I_final = 1.03). | RAN | The spec's strict clamp is not met. Anti-windup is permanently engaged, not exceptional. Impact is small at ki=0.005. | Project the target onto the floored simplex first. Clamp after centring. |
| F-26 | Minor | B, D | Incorrect | acquirer_sim/app.py:100-103 (auto-register via `get_or_create`), :226; simulator.py:131-201, 265-270; acquirer_sim/models.py:31-41 | **Silent typo:** an outage toggle on `acquirer_alpah` returns 200 and auto-creates that acquirer.<br>**Outage checked late:** the outage flag is checked after the latency sleep.<br>**LATENCY_SPIKE never times out:** 520 ms < 2 s timeout, so it behaves identically to RETURN_DECLINE and HTTP_503 for every router (D RAN: byte-identical results).<br>**Unrealistic models:** declines independent per acquirer, infinite capacity, binary outages. | RAN | The scenario matrix is thinner than presented, and operator typos are silent. | 404 on unknown admin IDs. Spike above the timeout. Model correlated declines, 429s, and partial failures. |
| F-27 | Minor | B, C | Incorrect | dashboard/src/hooks/useLoomTelemetry.js:18-49,106-115,181-183,252-281; AllocationChart.jsx:98-115 | **Fabricated fallbacks:** hardcoded allocations 0.82/0.15/0.03 and latencies 0.042/22.5 ms are shown when data is missing.<br>**Sequence handling:** `sequence_number` is never used for gap detection.<br>**Lifecycle bugs:** markers pile up at index 0; reconnect is scheduled after unmount; `HEALTH_ALERT` reads a stale closure. | READ | The UI can display fabricated values and silently gap. | Drop placeholders. Use seq-based gap detection. Clean up handlers on unmount. |
| F-28 | Minor | B | Incompetent | tests/data_layer/test_sqlite_logger.py:388-405; test_phase5_e2e_pipeline.py:340-365; tests/dashboard/test_phase7_qa_scenarios.py:231-302 | **Tests miss the bugs they target:**<br>• The shutdown-flush test never yields, so it misses F-12.<br>• The restart test never calls `route()`, so it misses F-13.<br>**Mislabelled or weak assertions:**<br>• `tests/dashboard/` contains only Python backend tests; none touch the dashboard.<br>• A near-tautological `ws.close_code is not None or not open`.<br>• Wall-clock latency assertions (<15 ms) that can flake. | READ | Green tests mask real defects. | Exercise the realistic paths. |
| F-29 | Minor | A, C | Incorrect | README.md:172-203,212,244,341-366,373,406-455,465,483; .env.example; scripts/compare_psr.py:222 | **Counts and phases:**<br>• Test counts 239 (README) / 147 / 202 / 225 / 238 / 252 across docs; actual 252.<br>• "Seven phases", though Phase 8 exists and is missing from the doc map.<br>• Value-scaled exploration listed as both a "Non-Goal" and "What's Next #1", though implemented.<br>**Paths and links:** `PRD.md` vs the real `prd.md`; 51 `file:///d:/loom/...` links that work only on the author's machine.<br>**Dead config keys:** APP_ENV, LOG_LEVEL, DECAY_HALF_LIFE_SEC, PID_KP/KI/KD.<br>**Mislabelled output:** compare_psr prints "Threshold M=3, Cooldown N=30" regardless of flags.<br>**Mode B and rates:** Mode B `ping` exits 1 with `[UNHEALTHY]` despite "no action required". run_demo delivers ~9 TPS, not 15; the generator ~13.3 TPS. | RAN + READ | Documentation drift that a reviewer spots immediately. | Use relative links, generated counts, and correct labels. |
| F-30 | Minor | A, B | Incorrect | README.md:111 ("absolute pipeline parity… scientific validity"); scripts/compare_psr.py:59-110 | **Logging asymmetry:** the baseline logs via its own `metrics_logger`; Loom is logged by the script.<br>**Field reuse:** the baseline writes its allocation vector into `thompson_samples`.<br>**Different breaker unit:** baseline cooldown is counted in transactions, so probe cadence scales with TPS (D-15).<br>**Different realisations:** per-acquirer RNG streams are consumed only when called, so the two routers see different outcome realisations for the same tx index. | READ | The parity claim is overstated, although none of these alone changes the sign of results. | Log both through the same hook. Use per-tx common random numbers. |
| F-31 | Minor | D | Not demonstrated | Full stack | **In-process (D RAN):** 2.8–3.4 ms CPU per tx, ~300–350 TPS per process; routing decision p50 ≈ 0.15 ms.<br>**Loopback (D RAN):** ~103 TPS at concurrency 8. The Windows sim, not the router, is the ceiling. | RAN | Adequate for a demo. Production needs horizontal scale, which conflicts with in-process PID and bandit state. | Externalise state atomically, then profile. |

### Nits

| ID | Severity | Lens | Type | Location | Evidence | RAN/READ | Why it matters | Suggested fix |
|---|---|---|---|---|---|---|---|---|
| F-32 | Nit | B, lead | Incorrect | router_core/pid.py:181-183 | **Lead RAN, 200k fuzz cases:** 4 sum deviations, max 1.5e-9, inputs about 1e6.<br>**Non-finite inputs:** NaN maps silently to the floor; `inf` returns `{0.03, 0.03}` (sum 0.06). | RAN | Non-finite inputs are masked. | Assert finiteness. |
| F-33 | Nit | B | Useless | router_core/server.py, acquirer_sim/server.py (`--reload`); scripts/simulate_outage.py:31-72; app.py:80-86; data_layer/sqlite_logger.py:31-128 vs schema.sql | **Dead or unsafe CLI options:** `--reload` is parsed but never used. `simulate_outage --action` has no `choices` and exits 0 on HTTP error.<br>**Sequence numbers:** assigned in completion order and reset on restart.<br>**Duplicated schema:** two schema copies differ in trigger messages. | READ | Misleading CLI behaviour and drift. | Remove, add choices, keep a single schema source. |
| F-34 | Nit | A | Incorrect | README.md:33; CONSTITUTION.md:43 | "absorbed 11 failures (+4 over the raw cliff)": the static router absorbed 4, so the difference is +7. The `[Phase N]` commit prefix is used in 5 of 16 commits. | RAN + READ | Small inaccuracies. | Fix. |

---

## 4. Claim verification (Lens C, lead spot-checked)

| # | Claim | Source | Command | Result | RAN/READ | Verdict |
|---|---|---|---|---|---|---|
| 1 | Test suite passes; "239 tests" | README.md:366 | `pytest` (fresh venv and author's venv); then with `-W ignore::starlette.exceptions.StarletteDeprecationWarning` | Without the flag: 2 collection errors. With it: **252 passed** (router_core 120, data_layer 55, acquirer_sim 44, baseline_router 23, dashboard 5, scripts 5) | RAN | PARTIAL |
| 2 | M=3: 92.00 vs 86.00, windows 94/82/100 vs 90/72/96, failures 4/11, Δw 100/11.77, flips 3/13 | README.md:17,23 | `python scripts/compare_psr.py` | Exact match | RAN (lead + C) | REPRODUCED (seed 42/777 only) |
| 3 | M=1: 76.00, 92/38/98, 30 failures | README.md:18 | `compare_psr.py --threshold-m 1` | Exact match. Header still prints "M=3". | RAN (lead + C) | REPRODUCED |
| 4 | M=5, Snapback, Gray 60%, Phase 3 raw, Phase 4 PID rows | README.md:19-23 | `python scripts/run_qa_baseline_scenario.py` (sole producer) | All match. M=5 and Snapback are numerically identical. | RAN | REPRODUCED |
| 5 | Phase 7 row 86.00 / 90/78/90 / 129 / 11 / 11.94% / 13 | README.md:24 | `scripts/qa_phase7_live_verification.py` ×4 (HEAD and 9742b96); in-process replica | 85.33 / 90/72/94 / 128 / 7 / 11.94% / 22. 3 acquirers, stochastic actuation. | RAN (lead + C) | **NOT REPRODUCED** (F-06) |
| 6 | Determinism at seed 42 | n/a | compare_psr ×3, md5 of output | Identical | RAN | REPRODUCED |
| 7 | Multi-seed: does Loom ever beat M=3? | n/a | `_lead/ms.py` (100 seeds); C `multiseed.py` (200 paired) | Lead: −2.58 pp [−3.17, −1.99], wins 8/100. C: −2.99 [−3.36, −2.62], 14/11/175. Seed 42 sits in the bottom ~12% of Loom outcomes. | RAN | Loom rarely beats M=3 |
| 8 | Phase 4 (72%) vs Phase 7 (78%) outage PSR on the "identical" schedule | README.md:23-24 | as #5 | Different experiment. Its real outage PSR is 72%; the 78% has no producer. | RAN | Discrepancy explained: the README row is wrong |
| 9 | 6.73 ms mean WS frame delivery | README.md:37 | phase7 script ×4; `ws_listen.py` | 6.4–8.3 ms, but it measures HTTP route plus push. Pure push ≤1.2 ms. n=150, no warmup. | RAN | PARTIAL (mislabelled) |
| 10 | 30.75 ms trigger RTT | README.md:38 | phase7 script ×4 | 26.5–28.2 ms, n=1 per run, loopback | RAN | REPRODUCED (thin) |
| 11 | 3.59 ms reconnect "with instant SQLite historical bootstrap" | README.md:39 | phase7 script; read app.py | 3.0–3.4 ms for a Python client handshake. No SQLite read exists. Dashboard backoff is ≥500 ms. | RAN + READ | PARTIAL / feature claim false |
| 12 | < 2 µs SQLite enqueue | README.md:40 | grep; C `enqueue_bench.py` | No measurement in the repo. C measured 0.65–0.75 µs mean. | RAN | UNVERIFIABLE from repo (consistent when measured) |
| 13 | Raw-bandit flips 12 (README) vs 13 (Phase 3 QA) | README.md:22; phase3-qa-report.md:107 | both scripts | 12 and 13: the definitions differ (one counts the boundary transition) and this is undocumented | RAN | REPRODUCED, inconsistently defined |
| 14 | `init-db` expected output | README.md:181 | `python -m data_layer.cli init-db` | Matches | RAN | REPRODUCED |
| 15 | `ping` HEALTHY / Mode B "no action required" | README.md:173,189-199 | `cli ping`, `cli inspect-state` (no Redis) | `[UNHEALTHY]`, exit 1; `inspect-state` exit 1 | RAN | PARTIAL |
| 16 | Pathway B services, /health, /state, simulate_outage (trigger/clear/pulse/HTTP_503) | README.md:228-287 | all commands, ports 8000/8001 | All work. The router survives 503s. | RAN | REPRODUCED |
| 17 | Generator 15 TPS; run_demo 15 TPS | README.md:210,236 | `--tps 15` | 13.3 TPS; run_demo ~9 TPS (serial loop) | RAN | PARTIAL / NOT REPRODUCED |
| 18 | "Every decision is logged, permanently" | README.md:495 | 968 live tx, then `cli status` | 0 rows | RAN | **NOT REPRODUCED** |
| 19 | "Smooths every reroute so traffic never jumps" | README.md:493 | live-config replica, 30 seeds | Weight Δ ≤ 12.5% per tx, always. Dispatch per tx is still 100% one acquirer. | RAN | PARTIAL (true of the weight variable only) |
| 20 | "Weighted toward the last minute" | README.md:494 | analytic from state.py | ~3.5 s (leader) to ~111 s (floor arm) at 15 TPS; idle arms never decay | RAN (calc) | NOT REPRODUCED |
| 21 | "Reacts to every transaction instantly (Real-time Redis Pub/Sub)" | README.md:496 | live demo, WS listener | Reacts per tx: yes. Via Redis: no (no publisher). | RAN + READ | PARTIAL |
| 22 | Ring buffer N=200, rAF 60 FPS | README.md:66,105 | grep dashboard/src | No rAF; buffer is 120 | READ | NOT REPRODUCED |
| 23 | Phase 4 recovery posterior 6.94→8.89, 0.617→0.720, "restored routing" | README.md:35 | trace script (lead + C) | 0.496→0.544; Alpha stays at the floor | RAN | **NOT REPRODUCED** |
| 24 | "Eases monotonically" (TC-QA-401); "0.00% dynamic overshoot" | README.md:31; phase4-qa:41-42 | multi-seed w_A trace (lead re-run, 50 seeds) | Not monotonic: w_A rises 3.1 times on average after the outage starts, peak +9.6 pp at seed 42 (+6.1 pp mean, +30.8 pp max) toward the dead acquirer. This happens before the target flips, so it is not overshoot past a setpoint; the overshoot claim is not contradicted. | RAN | NOT REPRODUCED (monotonicity only) |
| 25 | Fresh `pip install -e .` then run | README.md:142 | fresh venv | `ModuleNotFoundError: httpx` | RAN | NOT REPRODUCED |
| 26 | Dashboard install and build | README.md:145 | `npm ci && npm run build` | Built in ~8 s, JS 174.79 kB | RAN | REPRODUCED |
| 27 | Doc map links exist | README.md:406-455 | path check | All 51 targets exist locally, but as absolute `file:///d:/loom` URLs. Phase 8 docs are missing from the map. | RAN | PARTIAL |
| 28 | `.env.example` keys are used | .env.example | grep each key | REDIS_*, KEY_PREFIX, channels and SQLITE_* are read. APP_ENV, LOG_LEVEL, DECAY_HALF_LIFE_SEC, PID_* are never read. | RAN | PARTIAL |
| 29 | Dashboard gray-failure card: +1333 bps (82.00 vs 68.67) | BaselineComparisonCard.jsx:40-48 | lead `gray.py`, seed 42 and 30 seeds | 90.00 vs 88.67 = +133 bps; 30-seed mean +2.42 pp, Loom wins 20/30 | RAN | **NOT REPRODUCED** |

---

## 5. Feasibility gap analysis

| Gap | Why a PSP would care | Evidence in repo | Severity | Effort | Revenue impact |
|---|---|---|---|---|---|
| Failure attribution (decline-code taxonomy) | Health must reflect the acquirer, not issuer or customer behaviour | F-02 | Blocking | M | **High.** Wrong signal on most traffic; noise-chasing diversions. |
| Estimator memory / steady-state regret | Permanent approval-rate loss on all volume | F-03 | Blocking | M | **High.** ~0.4 pp of GMV at a 1 pp acquirer gap (model §6). |
| Idempotency and duplicate-authorization safety | Double charges mean chargebacks and regulatory exposure | F-07; decisions-log.md:1316 acknowledges no idempotency | Blocking | M | Low direct, high risk |
| Fail-open / kill switch / static fallback | Router failure must not become a payments outage | `route()` exception → 500; no fallback (F-07, F-22) | Blocking | S | High during incidents |
| Hot-path isolation from telemetry | Telemetry must never block authorization | F-05 | Blocking | S | High during incidents |
| Live per-decision audit log with propensities, context, policy version | Disputes, regulators, debugging, off-policy evaluation | F-04. In deficit mode `allocation_weight` is not a propensity (dispatch is deterministic given history). In stochastic mode it is, but nothing is logged live. No amount, BIN, merchant or currency columns. | Blocking | S–M | Enables measurement |
| Deterministic eligibility (currency, MCC, region, BIN, method, contract) | [DOMAIN KNOWLEDGE, medium] Many transactions have only 1–2 legal routes | Absent | Blocking | M | High (correctness) |
| Shadow mode / holdout A/B / gradual rollout | A risk team will not pilot without them | Absent; `select_route()` skips the PID, so it is not the deployed policy | Blocking | M | Enables measurement |
| Card-on-file / recurring continuity (network transaction ID, mandates) | [DOMAIN KNOWLEDGE, medium] Merchant-initiated transactions reference the original authorization; switching acquirers can break them | No NTI, MIT/CIT or mandate fields (`AuthorizeRequest`) | Blocking for recurring | M | Medium |
| 3DS / authentication binding | [DOMAIN KNOWLEDGE, medium] Authenticated transactions may not be re-routable without re-authentication | No 3DS fields | Blocking for cascading | M | Medium |
| Retry / cascade on technical failure | [DOMAIN KNOWLEDGE, medium-high] Orchestrators commonly retry technical declines on an alternate acquirer | None (README.md:474 makes this a design choice). The competent router with retry gains +2.2 pp over static (D RAN). | Major | M (needs idempotency) | Medium, concentrated in outages |
| Contextual modelling (BIN/issuer, network, currency, amount band, 3DS, merchant, region, time) | [DOMAIN KNOWLEDGE, high] Approval differences are segment-level | Registry keyed by `acquirer_id` string; only `amount` is read (Phase 8) | Major | L. The Beta-state and registry code can be re-keyed by (acquirer, segment) with hierarchical priors, but the PID/deficit actuation and value policy need redesign. | High (where the real upside is) |
| Cost-aware routing (interchange++, scheme fees, acquirer pricing, volume tiers) | [DOMAIN KNOWLEDGE, high] Routing optimises approval net of cost | No cost fields | Major | M | Medium–High (bps of GMV) |
| Contract minimums vs a 3% exploration floor to a failing route | Commitments are volume- or time-based contracts, not exploration | `min_allocation` only | Major | S–M | Medium |
| Capacity / backpressure awareness | The stated purpose of PID smoothing | Infinite-capacity simulator; herd protection never measured (F-08) | Major | M | Medium (rare events) |
| Horizontal scale / atomic shared state / multi-region | HA requires replicas | In-process PID and RNG; sync Redis WATCH/MULTI; restart bug (F-13) | Major | M–L | — |
| AuthN/Z, admin-plane separation | Security baseline | CORS `*` with credentials; unauthenticated admin proxies on the routing service | Major | S | — |
| PCI scope | [DOMAIN KNOWLEDGE, high] Anything on the authorization path seeing PAN or tokens is in scope | Router passes opaque JSON; no PAN fields today | Major (deployment) | M | — |
| Multi-seed, correlated-failure, capacity-aware benchmark | Credibility of any lift claim | F-15 | Major | S | — |

---

## 6. Revenue model

Built by Lens D (`revenue_model.py`, RAN). The behaviour coefficients come from this audit's simulator runs. Every dollar input is an **assumption** and labelled as such. The lead reviewed the model and added the caveats below.

### Assumptions (base case)

| Parameter | Value | Basis |
|---|---|---|
| Annual GMV | $1B | assumption |
| Average ticket | $40 → 25M tx/yr ≈ **0.79 TPS average** | assumption |
| Primary approval rate | 90% | [DOMAIN KNOWLEDGE, medium] |
| Failed payments the shopper retries successfully | 50% | assumption, low confidence |
| Hard outages on primary | 4 h/yr in 30-min events | assumption, medium-low |
| Gray failure | 20 h/yr at −10 pp | assumption, low |
| Steady technical advantage of primary over secondary, δ | 1 pp | **assumption; the result hinges on it** |
| Share of outages that are timeouts | 50% | assumption |

**Behaviour coefficients (from RAN experiments):**

| Policy | Hard outage | Steady state | Gray failure |
|---|---|---|---|
| **Loom** | ~10-tx ramp + 3% floor | 41% of traffic to the worse acquirer at δ=1 pp | 15% left on the degraded acquirer |
| **static_repo** | ~3 failures + 1/30 of outage volume | 0% to the worse acquirer (assumes priority is correct) | 80% left on degraded |
| **Competent** | ~0 visible failures on fast-fail outages; ~2 s of volume on timeouts | 6.5% to the worse acquirer (long-memory learner) | 10% left on degraded |

### Base-case annual lost GMV vs an oracle router (D RAN)

| Policy | Outage | Gray | Steady | **Total** |
|---|---|---|---|---|
| No failover | $210k | $100k | $0 | $310k |
| Static (repo) | $7.4k | $82k | $0 | $90k |
| **Loom** | $7.7k | $15k | **$2.04M** | **$2.07M** |
| Competent (tech-only breaker + retry + long-memory estimator) | $0.1k | $10k | $0.32M | $0.33M |

### Sensitivity (D RAN): Loom minus static, annual lost GMV (positive = Loom worse)

| GMV | Outage h/yr | δ = 0, gray 0 h | δ = 0, gray 20 h | δ = 1 pp, gray 0 h | δ = 1 pp, gray 20 h |
|---|---|---|---|---|---|
| $100M | 1 | +$0.2k | −$7.2k | +$0.21M | +$0.20M |
| $100M | 24 | +$5.6k | −$1.8k | +$0.21M | +$0.20M |
| $1B | 1 | +$0.1k | −$74k | +$2.05M | +$1.98M |
| $1B | 4 | +$0.3k | −$74k | +$2.05M | +$1.98M |
| $1B | 24 | +$1.9k | −$72k | +$2.05M | +$1.97M |
| $10B | 4 | −$5.8k | −$0.75M | +$20.5M | +$19.8M |

The full 36-row table is reproduced in the Appendix.

### Reading

- **Hard-outage recovery is small for any policy that fails over at all.** The repo's static breaker already loses only about $7k/yr at $1B. Outage hours barely move the totals. Upside from outages is concentrated in rare events and is small.
- **Loom's only positive regime:** acquirers are technically identical (δ≈0) **and** gray failures occur. There it saves tens of $k per $1B GMV versus a naive breaker, and a competent breaker saves slightly more.
- **Loom's always-on loss:** whenever acquirers genuinely differ by ≥0.5 pp, Loom's steady-state regret (F-03) is roughly 0.2% of GMV and swamps everything else.
- **Wrong static priority:** if the static priority is wrong by 1 pp, static loses about $5.0M/yr at $1B, Loom $2.05M, competent $0.33M (D, by hand from the same formula). Loom beats a misconfigured static router but loses to a competent learner.
- These are GMV figures. PSP revenue is roughly take rate × GMV ([DOMAIN KNOWLEDGE, medium] 0.2–2%).

### Lead caveats

- **The "competent" steady-state figure needs volume.** It uses 6.5% (±3.1) to the worse acquirer measured at tx 40k–50k (re-run reproduced exactly). In the lead's 2000-tx check (F-03) the same γ=0.9999 learner still averaged 38.9% (sd 31.6) at a 1 pp gap. At 25M tx/yr the convergence assumption holds; for low-volume segments it would not.
- **Static is assumed to have the correct priority.**
- **Gray-failure prevalence (20 h/yr) is unsupported.**

**Confidence:** low on absolute dollars; medium on the sign and ranking.

**Data that would settle it:** per-acquirer, per-segment approval rates and decline-code mixes over months; incident logs with start/end times and failure mode (timeout, 5xx, decline-coded); traffic TPS profiles; acquirer pricing. Then run counterfactual replay with logged propensities.

---

## 7. Prioritized roadmap

### Step 1: NOT DEPLOYABLE → PROTOTYPE WITH A SOUND IDEA

This requires showing, on honest multi-seed benchmarks, that the learned router beats a competent non-learning baseline in at least one realistic regime without losing elsewhere.

1. **Decline-code taxonomy in both routers.** Only technical or acquirer-attributable outcomes feed health. (F-02) *Closes a revenue gap.*
2. **Split the estimator.** A fast breaker with wall-clock half-open probes for outages, plus a long-memory, wall-clock-decayed approval estimator with hysteresis for quality. Retire the per-selection γ and the volume-based 3% floor. (F-03, F-17) *Closes a revenue gap.*
3. **Re-baseline.** Default comparator: decline-aware breaker plus sane all-tripped policy plus single retry. Multi-seed paired CIs with common random numbers, `--seed`, the shipped config, and gray-failure, blip, long, 3-acquirer and weak-secondary scenarios. Publish whatever wins. (F-01, F-15, F-20) *Closes a credibility gap.*
4. **Either measure herd protection or drop the PID claim.** Add capacity (429s, concurrency limits) to the simulator. Compare the PID against explicit admission control in wall-clock terms. (F-08, F-09) *Closes a credibility gap.*
5. **Regenerate every published number from committed scripts** (README table, dashboard card, Phase 4 and 7 claims) and delete unsourced numbers. (F-06, F-21) *Closes a credibility gap.*
6. **Fix reproducibility:** lockfile, httpx as a runtime dependency, CI green. (F-14) *Closes a credibility gap.*

### Step 2: PROTOTYPE → PILOT-READY IN SHADOW MODE

This requires that the router can run beside production without touching money, while producing evaluable logs.

7. **Wire the data layer into the served app.** Per-decision log with the full probability vector, policy and config version, context features and outcome class. Fix logger loss and duplicate handling. (F-04, F-12) *Closes a credibility gap (enables off-policy evaluation).*
8. **Hot-path safety:** telemetry off the request path, per-acquirer bulkheads, best-effort post-dispatch updates, unexpected-response handling, fail-open default. (F-05, F-07, F-16) *Closes a revenue gap (incident risk).*
9. **Shadow mode:** consume mirrored production events, compute decisions, log without dispatch. Evaluate via counterfactual replay on real logs. *Closes a credibility gap.*
10. **Deterministic eligibility layer, merchant pinning, kill switch, authN/Z, admin-plane separation.** (F-22) *Closes a revenue gap (correctness).*
11. **Correct Redis state** (atomic Lua or async, hydrate on restart) or per-replica state with merge. (F-13) *Closes a credibility gap.*

### Step 3: Beyond shadow (live pilot)

Requires idempotency, retry/cascade with NTI / 3DS constraints, cost-aware objective, contextual segmentation, A/B with holdout, and region-local state. *Each closes a revenue gap.*

---

## 8. Reusable assets (verified)

- **`acquirer_sim/`:** a scriptable multi-acquirer FastAPI simulator with an admin API (outage behaviours, success-rate control). It is deterministic per seed (RAN). Needs: correlated declines, timeouts above the router timeout, 429/capacity, partial failures, `SeedSequence` seeding (F-26).
- **The `compare_psr.py` harness pattern:** an in-process `httpx.ASGITransport` simulator with identical outage scripts, deterministic and fast (150 tx in well under a second; 200-seed sweeps in ~3 min, RAN). With multi-seed paired CIs it is a useful regression bench for any routing policy. Lens C's `multiseed.py` (Appendix) is a working start.
- **`baseline_router/`:** a clear, readable breaker state machine. A useful foil once the decline-code and all-tripped policies are fixed.
- **`router_core/pid.py` utilities:** a correct, pure, immutable-state PID step and a bounded-simplex projection that held its floor in 200k fuzz cases (RAN). Reusable as generic allocation smoothing or rate limiting.
- **`data_layer/schema.sql` pattern:** an append-only ledger with guard triggers (blocks accidental UPDATE/DELETE, RAN). Needs context, propensity and config-version columns, and honest wording.
- **Dashboard:** builds cleanly (RAN). A good demo and ops-visualisation shell once fed from real logs.
- **Process artefacts:**
  - The decisions log does record inconvenient results (M=3 loss at decisions-log.md:1339, idempotency gap at :1316), and is append-only in practice (A RAN: only whitespace and placeholder deletions).
  - The phase specs are detailed enough to audit against.

Honest positioning: the defensible value today is the **simulation and benchmark harness and the teaching demo**, not the router. "Learn the better acquirer when priority is unknown" is a niche only if rebuilt as a contextual, long-memory estimator with a separate breaker (D).

---

## 9. Dead or useless inventory

All entries RAN in a scratch copy unless marked.

| Item | Command | Output / evidence |
|---|---|---|
| `scipy` declared but never imported | `grep -rn "scipy" --include=*.py .` | No source hits. mypy also notes the unused `scipy.*` override section. (Lens C used scipy only in a scratch script.) |
| `calculate_gamma_from_half_life` | `grep -rn "calculate_gamma_from_half_life" --include=*.py . \| grep -v ^./tests` | Only the definition (bandit.py:12) and re-export (`__init__.py:3,39`) |
| `DECAY_HALF_LIFE_SEC`, `PID_KP/KI/KD`, `APP_ENV`, `LOG_LEVEL` in .env.example | `grep -rn "APP_ENV\|LOG_LEVEL\|DECAY_HALF_LIFE\|PID_KP" --include=*.py .` | No hits |
| `redis_max_connections` | `grep -rn redis_max_connections --include=*.py .` | Only config.py:43 |
| `BanditRouter.select_route` (duplicates `route()` logic, skips the PID) | `grep -rn "select_route(" --include=*.py .` | Non-test callers are only the baseline's own method |
| `integral_decay`, `derivative_filter_alpha`, `derivative_on_measurement=False` | `grep -rn "integral_decay=\|derivative_filter_alpha=" --include=*.py . \| grep -v tests` | No hits outside tests |
| `health_score` (computed, persisted, never used for routing) | `grep -n "health" router_core/router.py` | Log lines only (≈339, 348) |
| `MetricsLogger.log_routing_result_async`, `publish_raw`, `hydrate_all_from_redis` | `grep -rn "log_routing_result_async\|publish_raw\|hydrate_all_from_redis" --include=*.py . \| grep -v "def \|tests"` | No hits |
| Entire live data layer (`RedisBanditStateRegistry`, `MetricsLogger`, `EventPublisher`) in the served app | `grep -rn "metrics_logger=\|event_publisher=\|RedisBanditStateRegistry(" --include=*.py .` | Only scripts/ and tests/ (F-04) |
| `_redis_forwarder_loop` subscriber with no publisher | READ router_core/app.py:129-152 | Subscribes; nothing publishes |
| `dashboard/src/data/baselineReferenceRun.json` (duplicate of the `.js`) | Node deep-equal (B RAN): identical; `grep -rn baselineReferenceRun dashboard/src` | Dashboard imports only the `.js`; the `.json` is read only by a Python test |
| `@types/react`, `@types/react-dom` | `ls dashboard/src/**/*.ts*` | No TypeScript files |
| CI Redis service and `REDIS_HOST/PORT` env | `grep -rn "redis.Redis(" tests` | None; all tests use fakeredis |
| Duplicate `[[tool.mypy.overrides]] module="tests.*"` | READ pyproject.toml | Appears twice |
| `scripts/test_windup_recovery_step.py` | `testpaths=["tests"]` | Test-named script never collected |
| `--reload` flags | READ both server.py files | Parsed, never passed to uvicorn |
| `LATENCY_SPIKE` behaviour | D RAN | Byte-identical outcomes to RETURN_DECLINE and HTTP_503 for every router |
| Personas, CONSTITUTION, "gate certifications" as engineering controls | git history (F-19) | No independent effect detectable in history |

---

## 10. Unverified suspicions

| Suspicion | Confidence | What would confirm it |
|---|---|---|
| Some QA-report numbers were written, not generated. The dashboard 68.67/82.00/4.82 and the README Phase 7 78/90 have no producer anywhere in history. "+1333" may be "+133" mis-transcribed. | Medium | Re-run every QA script and diff every reported number. Only compare_psr, run_qa_baseline_scenario, phase7, tune and windup scripts were checked. |
| CI on GitHub has been red since the dependency drift | Medium | GitHub Actions run history (no access; GitHub connector unauthenticated) |
| Under real Redis with network RTT, WATCH/MULTI contention and Pub/Sub drops are worse than fakeredis shows | Medium | Real Redis 7 with multiple processes and a lagging subscriber (`client-output-buffer-limit pubsub`) |
| Concurrent `ws.send_text` on one socket from concurrent `/route` handlers can interleave or raise | Medium-low | N concurrent routes plus a frame-integrity checking client |
| CORS `*` + credentials + unauthenticated admin proxies let any visited web page toggle simulator outages | Low-medium | A cross-origin `fetch` from another origin against 127.0.0.1:8000 |
| PID gains are overfit to seed 42/777 | Medium | Re-tune across held-out seeds and compare Δw and PSR |
| Delayed feedback under concurrency amplifies decline-noise chasing (F-02 × F-16) | Medium | Combine B exp5 concurrency with D exp2 decline noise |
| Phase 8 value-scaling under the F-02/F-03 regimes | Low | D's harness with `value_scaled_config` enabled |

---

## 11. What is genuinely good (verified)

- **Benchmark determinism (RAN):** `compare_psr.py` and `run_qa_baseline_scenario.py` reproduce README rows 1–7 bit-for-bit at the published seeds (identical md5 across runs). Seed 42 is not cherry-picked: the direction of every published comparison holds across 50–200 seeds, and seed 42 actually understates Loom's average.
- **Gray failure is a real, under-reported win (RAN):** +2.42 pp over the M=3 breaker (lead, 30 seeds, 20/30 wins); +2.71 pp, CI [2.08, 3.35] (C, 100 seeds).
- **Loom robustly beats the M=1 configuration** (+13.8 to +14.9 pp; RAN), even if that configuration is a strawman.
- **PID implementation matches its spec (RAN/READ):**
  - Correct derivative-on-measurement sign, with no one-step lag bug.
  - Spectral radius 0.951 at the shipped gains, so it is stable.
  - A step to a reachable target reaches 90% in 19 tx with zero overshoot.
  - Per-tx weight change ≤ 12.5% in 400+ runs.
- **The Phase 4 flip gate, as specified, is met (RAN):** w_A crosses 0.5 at most 3 times in the outage window (mean 0.94 over 50 seeds), satisfying "≤2 transitions in continuous allocation" on average.
- **Projection and scheduler (RAN):** bounded-simplex projection had zero floor violations in 400k combined fuzz cases. The deficit scheduler's dispatch fractions match the mean allocation to 5 decimals over 1e5 steps, with no burst after a regime change.
- **Concurrency safety of the PID step (RAN):** asyncio interleaving cannot tear PID or deficit state (allocation sum exactly 1.0 after 200 concurrent calls).
- **The append-only triggers do block plain UPDATE/DELETE (RAN).**
- **Working infrastructure (RAN):**
  - `ruff check` is clean.
  - The dashboard builds.
  - With one warning filter, 252/252 tests pass.
  - Fault injection (trigger/clear/pulse/HTTP_503) works and the router survives 503s.
  - SQLite init with WAL and 4 triggers works.
  - Enqueue cost ~0.7 µs.
- **The PID-wiring regression tests are real** (tests/router_core/test_server_cli.py).
- **Some inconvenient facts are disclosed:** the M=3 loss (README.md:33, decisions-log.md:1339), the transaction-clocked Δt (README.md:472), the 3% floor tax (:473), no inline cascading (:474), in-flight lag (:475), no idempotency (decisions-log.md:1316). They are disclosed in the body, not the headline.

---

## 12. Not run / limitations

- **No Docker daemon or real Redis** (`docker info` failed: daemon not running). All Redis behaviour comes from fakeredis, so Mode A, real Lua/WATCH contention and real Pub/Sub drop behaviour are READ or fakeredis-only.
- **Dashboard UI was not exercised in a browser.** Build only, plus a Python WebSocket client against the backend.
- **Latency and load numbers are Windows loopback.** The Windows simulator server, not the router, was the throughput ceiling. Some latency runs overlapped with CPU-heavy seed sweeps.
- **Lens D's TPS sweep, steady-state and decline-noise results use a discrete-event re-implementation (`des.py`).** It was verified decision-identical to `BanditRouter` in 10/10 seeds for both configs, but uses virtual latency. The lead independently re-ran the steady-state claim against the real `BanditRouter` (F-03).
- **D's "competent" router was quickly tuned,** so its results are a lower bound on a competent design. Capacity and collapse models are stylised.
- **Not run:** `reset-demo` against any real ledger (only a scratch DB), `run_phase5_e2e_verification.py`, `qa_phase8_controlled_test.py`, `npm run dev`, `pre-commit run` (would need network for hook environments). GitHub CI history was not checked (connector unauthenticated).
- **The revenue model's dollar inputs are assumptions** (§6).
- **Domain claims [DOMAIN KNOWLEDGE] are not verified against any specific PSP or scheme documentation.**
- **India context** (from Lens D, [DOMAIN KNOWLEDGE], medium confidence unless stated):
  - **UPI (blocking for Loom's model):** UPI success is not binary at request time (pending and deemed states, reconciliation). Many failures sit at the remitter bank, independent of PSP-bank choice. Collect and intent flows cannot be silently cascaded.
  - **Card rules (extra work; affect retry design):**
    - RBI card-on-file tokenization constrains storage and retries.
    - AFA/3DS on domestic card-not-present transactions binds authentication context, which constrains cascading (medium-high confidence).
  - **Data localisation (deployment constraint; high confidence).**
  - **Payment-aggregator governance (organisational work).**
  - The auditor is not confident on current small-value AFA exemption thresholds and makes no claim there.
- **Scratch code location:** cited scratch scripts are reproduced in the Appendix. Their outputs and SQLite files live only in `%TEMP%\loom-audit*` and are not committed.

---

## 13. Corrections after re-verification (2026-10-02)

A second pass re-checked, against code or by re-running:
- every Critical and Major finding;
- every NOT REPRODUCED verdict;
- the deployability verdict;
- every cited file path and line number.

Everything not listed below was re-confirmed unchanged. Numbers marked "re-run" were reproduced exactly or within run-to-run noise.

### Findings changed

| Item | Before | After | Why |
|---|---|---|---|
| F-09 | Major. Said the Phase 4 "≤2 flips" acceptance gate was missed. | **Downgraded to Minor and rewritten.** | The spec's gate (docs/phase4-pid-spec.md:425) is "≤2 transitions **in continuous allocation**". Measured that way (crossings of w_A = 0.5 during the outage), it is **met**: mean 0.94, max 3 over 50 seeds; 1 at seed 42. The first version compared dispatch-level flips against an allocation-level criterion. Non-monotonicity and the closed-loop windup result were re-run and stand. |
| Claim row 24 | NOT REPRODUCED for both "0.00% overshoot" and monotonic easing. | NOT REPRODUCED for **monotonicity only**. | The rise toward the dead acquirer happens before the target flips. It is not overshoot past a setpoint, so the overshoot claim is not contradicted. |
| F-16 (pool coupling) | "At concurrency 200, healthy Beta/Gamma charged 146 PoolTimeout + 32 ConnectTimeout." | Restated as **load-dependent**. | Two lead re-runs at concurrency 200 produced 0 such errors. At concurrency 400: 873 PoolTimeout + 113 ConnectTimeout on healthy acquirers. The mechanism (`PoolTimeout` ⊂ `TimeoutException`, booked as an acquirer failure) is confirmed. The original count was likely inflated by CPU contention from parallel sweeps. |
| F-21 | "True push ≤1.2 ms". | "Mean 0.5–0.7 ms, max 1.2–2.0 ms". | Lead re-run: mean 0.70 ms, max 2.03 ms (90 frames). |
| F-03 / §1(b) / Top 5 | "30–46% of traffic to the worse acquirer". | "19–47%, depending on configuration and gap". | The 30–46% range covered only the benchmark config. The live config gives 19.2–46.8% (re-run). The 50k-tx horizon result (42.0% / 45.1%) re-ran bit-identically. |
| F-08 | Ramp "≈40 s at 0.5 TPS". | "≈34–45 s". | Re-run divert time at 0.5 TPS: 34.5 s (bench) and 35.9 s (live), n=10. The original was 40.3 / 44.5 s, n=30. |
| F-17 | "1000 TPS, 600 s: 18,076 vs 75". | "18,076–18,088 vs 75–83". | Re-run (n=1) gave 18,088 vs 83. The 15 TPS rows reproduced (34.2 vs 6.1; 277.6 vs 23.6). |
| F-13 | "2967 of 4800 WatchError". | "2967 and 3109 of 4800 (two runs)". | Thread-scheduling nondeterminism. The finding is unchanged. |
| F-19 | Presets "Kp=0.40, Ki=0.05, Kd=0.10 never existed". | "Appear in no commit's code". | A history-wide grep can only show absence from commits, not that they never existed in an uncommitted state. |
| §6 lead caveat | The "competent" steady-state figure was called optimistic. | It "needs volume". | The 50k-tx re-run reproduced 6.5% ± 3.1. At the model's 25M tx/yr, convergence holds. |

### Citation corrections (substance unchanged)

| Finding | Wrong citation | Correct citation |
|---|---|---|
| F-03 | router_core/state.py:103-110 | :104-111 |
| F-04 | README.md:178 (a code fence) | Removed; the Mode B claim is at :173 |
| F-10 | docs/phase8-tech-lead-review.md:61-67 | :61-69 (the "0.5 + 0.5·H" formula is on line 69) |
| F-20 | decisions-log.md:1305; :1337-1345 | :1306 ("good enough" risk); :1339-1345 (both quotes verified verbatim at 1339 and 1345) |
| §8, §11 | M=3 loss at decisions-log.md:1337 | :1339 (1337 is the gray-failure sensitivity entry) |
| F-22, Gap table | CORS at router_core/app.py:153-160 | :164-167 (admin proxies :297-405) |
| F-24 | data_layer/redis_pubsub.py:524-536 | :533-545 (`get_event` validates every message as `RoutingEvent`) |
| F-26 | acquirer_sim/app.py:404-423 (the file has 308 lines) | :100-103 (auto-register via `get_or_create`), used by the keyed outage toggle at :226 |

### Re-verified unchanged (re-run this pass)

**Benchmarks and baselines**
- Seed-42 benchmark: compare_psr 92.00 / 76.00 / 86.00, Δw 11.77, 13 flips.
- 100-seed comparisons: −2.58 pp vs M=3, CI [−3.17, −1.99]; −1.49 vs M=5; +14.94 vs M=1.
- Strawman variants (F-01): issuer-aware baseline 93.33; most-recently-tripped fallback 94.00.
- Lens D scenario matrix, 20 seeds (F-01, F-08, F-17): Loom vs static −3.47 pp; competent+retry +2.07 pp; gray +1.73 pp; all signs as reported.
- PID vs raw bandit (F-08): −1.71 pp, CI [−1.98, −1.44], 100 seeds.

**Published numbers and the live server**
- Gray failure (F-06): seed 42 gives 88.67 vs 90.00 (+133 bps); 30 seeds +2.42 pp.
- Phase 7 (F-06): the repo's own `qa_phase7_live_verification.py` gives 90 / 72 / 94 and 85.3%. RTT 27.6 ms; reconnect 3.5–4.0 ms; "delivery" mean 6.8–7.7 ms.
- Phase 4 posterior trace (F-06): 0.496 → 0.521 → 0.544.
- Live demo, fresh ledger (F-04): 184 tx in 20 s (9.2 TPS) and **0 rows logged**. Generator ≈13 TPS.
- `ledger.py` (F-04): live router has no logger, publisher or Redis.

**Engineering and data-layer defects**
- Stalled WebSocket (F-05): `/route` blocked at request #226.
- Value-scaling under PID (F-10): no protective effect, 30 seeds.
- Steady-state share to the worse acquirer (F-03).
- Unexpected acquirer responses (F-07): 422, garbage body and RemoteProtocolError all record 0 outcomes. HTTP 500 after authorization reproduced.
- Concurrency (F-16): 98 / 114 of 200 vs 15 / 12 sequential.
- Redis restart (F-13): restarted worker cannot route.
- Ledger bypasses (F-11): catchable `IntegrityError`; DROP TRIGGER; INSERT OR REPLACE; reset-demo.
- MetricsLogger losses (F-12): close loses 140 / 200; one duplicate leaves 1 of 20.

**Reproducibility (F-14)**
- Fresh runtime-only `pip install -e .`: `ModuleNotFoundError: httpx`.
- Clean export: ruff format 9 files; mypy 18 errors.

**Decline noise (F-02), 15 seeds**
- 187 majority flips between identical acquirers.
- T2c: Loom 86.6% vs decline-aware 87.4%.

**Hot path and windup**
- Redis round-trips per tx (F-13).
- Windup stress script (F-09): bounded = unbounded.

**Process (F-19)**
- 16/16 commits by one author; 0 merges; 0 AI mentions in README.

**Static checks**
- Absent `test_pid_eliminates_crossover_flapping` and `requestAnimationFrame` (0 hits).
- Buffer cap of 120 (useLoomTelemetry.js:18,190).

### Deployability verdict

**Unchanged: NOT DEPLOYABLE.** Each pillar of the verdict was re-run this pass: F-01, F-02, F-03, F-05, F-07, F-13, and the absence of eligibility, fail-open and kill switch in code (F-22, grep). The F-09 downgrade does not touch any of them.

An incidental observation from the re-run supports F-02 / F-03. A live router serving three identical 95% acquirers had allocated 12% to Alpha and 59% to Gamma after ~250 transactions.

## 14. Appendix

### A.1 How to reproduce the lead's key checks

The commands were run in a scratch copy with `PYTHONPATH=<scratch>`, from `<scratch>/_lead`.

```bash
python ../scripts/compare_psr.py
```

```bash
python ../scripts/compare_psr.py --threshold-m 1
```

```bash
python ms.py
```

```bash
python gray.py
```

```bash
python steady.py
```

```bash
python p8_pid_seeds.py
```

```bash
python ledger.py
```

```bash
python a01b.py issuer
```

```bash
python a01b.py fallback
```

```bash
python ws_block.py
```

```bash
python simplex_fuzz.py
```

`ws_block.py` is Lens B's `exp6_ws_block.py` with the port changed.

### A.2 Lead outputs (verbatim excerpts)

```
# ms.py (100 seeds)
M=1: base mean=74.00 sd=7.23 | loom mean=88.94 sd=2.95 | diff=+14.94 95%CI[+13.75,+16.13] loom wins 97/ties 0/100
M=3: base mean=91.52 sd=2.92 | loom mean=88.94 sd=2.95 | diff=-2.58 95%CI[-3.17,-1.99] loom wins 8/ties 7/100
M=5: base mean=90.43 sd=2.01 | loom mean=88.94 sd=2.95 | diff=-1.49 95%CI[-1.98,-1.00] loom wins 20/ties 16/100
# gray.py
seed42: baseline M=3 gray PSR=88.67  loom=90.00
30 seeds: baseline mean=88.36 sd=3.55 | loom mean=90.78 sd=3.03 | loom-base mean=2.42 sd=3.58, loom wins 20/30
# a01b.py (seed 42, baseline PSR)
none M=1 76.0 | fallback(most-recently-tripped) M=1 94.0 | issuer-decline-aware M=1 93.33 | all M=3 92.0
# steady.py (share of tx 500-2000 to the worse acquirer, n=10)
beta .94: bench 46.3% | live 46.8% | decay.9999 38.9% (sd 31.6)
beta .92: bench 38.6% | live 29.2% | decay.9999 11.3%
beta .90: bench 30.5% | live 19.2% | decay.9999  4.3%
# p8_pid_seeds.py (high-value tx, 10/run, sent to dead alpha, n=30)
deficit    scaling=False mean=1.23 | scaling=True mean=1.53
stochastic scaling=False mean=1.40 | scaling=True mean=1.10
# ledger.py
live server router: metrics_logger = None | event_publisher = None | registry = BanditStateRegistry
live pid actuation_mode = stochastic | acquirers = ['acquirer_alpha', 'acquirer_beta', 'acquirer_gamma']
UPDATE blocked, caught in Python as IntegrityError -> Transactions table is append-only: ...
after DROP TRIGGER, row now: [('AUTHORIZED', 1)]
after DROP TRIGGER + DELETE, rows: 0
# ws_block.py
handshake: b'HTTP/1.1 101 Switching Protocols'
POST /route #226 TIMED OUT after 5s (blocked by stalled websocket)
# B exp7 / exp10 / exp10b re-run by lead
[pid=True] restarted worker route FAILED: ValueError: max() arg is an empty sequence
close() during batch accumulation: logged 30, persisted 0
batch with 1 duplicate tx_id + 19 unique: persisted rows total = 1 (expected 20 = 1 + 19)
persisted out of 10 per trial: [3, 3, ... 3] | total lost: 140
# CI checks on clean git archive
ruff check: All checks passed! | ruff format --check: 9 files would be reformatted | mypy: Found 18 errors in 3 files
# pytest
fresh venv: 2 errors during collection (StarletteDeprecationWarning) ; with -W ignore::...: 252 passed in 17.59s
```

### A.3 Revenue model full sensitivity output (Lens D, RAN)

```
BASE CASE: {'gmv': 1000000000.0, 'ticket': 40.0, 'auth': 0.9, 'recovery': 0.5, 'outage_h': 4.0, 'outage_event_min': 30.0, 'gray_h': 20.0, 'gray_drop': 0.1, 'delta': 0.01, 'timeout_share': 0.5}
  no_failover  avgTPS=0.79  lost: outage=$0.21M gray=$0.10M steady=$0.0k  TOTAL=$0.31M
  static_repo  avgTPS=0.79  lost: outage=$7.4k gray=$82.2k steady=$0.0k  TOTAL=$89.6k
  loom         avgTPS=0.79  lost: outage=$7.7k gray=$15.4k steady=$2.04M  TOTAL=$2.07M
  competent    avgTPS=0.79  lost: outage=$0.1k gray=$10.3k steady=$0.32M  TOTAL=$0.33M

SENSITIVITY: annual lost GMV, Loom vs static_repo vs competent (delta = steady primary advantage)
     GMV  avgTPS outage h/yr  delta gray h |  static_repo         loom    competent |  loom - static
    100M    0.08           1   0.00      0 |        $0.3k        $0.5k        $0.0k |          $0.2k
    100M    0.08           1   0.00     20 |        $9.4k        $2.2k        $1.1k |         $-7.2k
    100M    0.08           1   0.01      0 |        $0.3k       $0.21M       $32.5k |         $0.21M
    100M    0.08           1   0.01     20 |        $8.5k       $0.21M       $33.5k |         $0.20M
    100M    0.08           4   0.00      0 |        $1.1k        $2.1k        $0.0k |          $0.9k
    100M    0.08           4   0.00     20 |       $10.3k        $3.8k        $1.2k |         $-6.5k
    100M    0.08           4   0.01      0 |        $1.1k       $0.21M       $32.5k |         $0.21M
    100M    0.08           4   0.01     20 |        $9.3k       $0.21M       $33.4k |         $0.20M
    100M    0.08          24   0.00      0 |        $6.8k       $12.4k        $0.1k |          $5.6k
    100M    0.08          24   0.00     20 |       $15.9k       $14.1k        $1.2k |         $-1.8k
    100M    0.08          24   0.01      0 |        $6.8k       $0.22M       $32.5k |         $0.21M
    100M    0.08          24   0.01     20 |       $15.0k       $0.22M       $33.4k |         $0.20M
   1000M    0.79           1   0.00      0 |        $1.8k        $1.9k        $0.0k |          $0.1k
   1000M    0.79           1   0.00     20 |       $93.2k       $19.1k       $11.4k |        $-74.1k
   1000M    0.79           1   0.01      0 |        $1.8k       $2.05M       $0.32M |         $2.05M
   1000M    0.79           1   0.01     20 |       $84.0k       $2.06M       $0.33M |         $1.98M
   1000M    0.79           4   0.00      0 |        $7.4k        $7.7k        $0.1k |          $0.3k
   1000M    0.79           4   0.00     20 |       $98.7k       $24.8k       $11.5k |        $-73.9k
   1000M    0.79           4   0.01      0 |        $7.4k       $2.06M       $0.32M |         $2.05M
   1000M    0.79           4   0.01     20 |       $89.6k       $2.07M       $0.33M |         $1.98M
   1000M    0.79          24   0.00      0 |       $44.4k       $46.3k        $0.7k |          $1.9k
   1000M    0.79          24   0.00     20 |       $0.14M       $63.4k       $12.1k |        $-72.3k
   1000M    0.79          24   0.01      0 |       $44.4k       $2.09M       $0.32M |         $2.05M
   1000M    0.79          24   0.01     20 |       $0.13M       $2.10M       $0.33M |         $1.97M
  10000M    7.93           1   0.00      0 |       $17.5k       $16.1k        $0.3k |         $-1.5k
  10000M    7.93           1   0.00     20 |       $0.93M       $0.19M       $0.11M |        $-0.74M
  10000M    7.93           1   0.01      0 |       $17.5k      $20.51M       $3.25M |        $20.50M
  10000M    7.93           1   0.01     20 |       $0.84M      $20.62M       $3.35M |        $19.78M
  10000M    7.93           4   0.00      0 |       $70.1k       $64.2k        $1.1k |         $-5.8k
  10000M    7.93           4   0.00     20 |       $0.98M       $0.24M       $0.12M |        $-0.75M
  10000M    7.93           4   0.01      0 |       $70.1k      $20.55M       $3.25M |        $20.48M
  10000M    7.93           4   0.01     20 |       $0.89M      $20.66M       $3.34M |        $19.77M
  10000M    7.93          24   0.00      0 |       $0.42M       $0.39M        $6.8k |        $-35.0k
  10000M    7.93          24   0.00     20 |       $1.33M       $0.56M       $0.12M |        $-0.78M
  10000M    7.93          24   0.01      0 |       $0.42M      $20.83M       $3.25M |        $20.41M
  10000M    7.93          24   0.01     20 |       $1.24M      $20.94M       $3.34M |        $19.69M
```

### A.4 Scratch experiment source code

Each script is reproduced as it was run. Lens sub-agent scripts expect `PYTHONPATH` set to their scratch copy root.


#### Lead auditor (`_lead/`)

<details><summary><code>a01.py</code></summary>

```python
import asyncio, logging, sys, types
logging.disable(logging.CRITICAL)
sys.argv=["x"]
src=open("../scripts/compare_psr.py").read()
import baseline_router.router as BR
from baseline_router.models import RouteHealthStatus
orig=BR.StaticBaselineRouter.select_route
def most_recent(self):
    if all(s.status==RouteHealthStatus.TRIPPED for s in self._route_states.values()):
        aid=max(self._route_states, key=lambda a:self._route_states[a].tripped_at_tx or -1)
        cd=self._config.failover_policy.cooldown_transactions
        st=self._route_states[aid]
        if self._global_tx_counter-st.tripped_at_tx < cd:
            return aid,{k:1.0 if k==aid else 0.0 for k in self._priority_order}
    return orig(self)
ns={"__name__":"cmp"}; exec(compile(src,"cmp","exec"),ns)
def go(): return asyncio.run(ns["execute_scenario"]("baseline","b.db",threshold_m=1))["global_metrics"]["psr"]*100
print("M=1 original:", round(go(),2))
BR.StaticBaselineRouter.select_route=most_recent
print("M=1 fallback=most-recently-tripped:", round(go(),2))
BR.StaticBaselineRouter.select_route=orig
# issuer-decline-aware breaker: re-exec router module with success ignoring DO_NOT_HONOR
rsrc=open("../baseline_router/router.py").read().replace("success = payload.authorized\n","success = payload.authorized or payload.decline_code == 'DO_NOT_HONOR'\n")
assert "DO_NOT_HONOR" in rsrc
exec(compile(rsrc,"brmod","exec"),BR.__dict__)
print("M=1 breaker ignores issuer declines:", round(go(),2))
```

</details>

<details><summary><code>a01b.py</code></summary>

```python
import asyncio, logging, sys
logging.disable(logging.CRITICAL)
import baseline_router.router as BR
from baseline_router.models import RouteHealthStatus
rsrc=open("../baseline_router/router.py").read()
mode=sys.argv[1]
if mode=="issuer":
    rsrc=rsrc.replace("success = payload.authorized\n","success = payload.authorized or payload.decline_code == 'DO_NOT_HONOR'\n")
exec(compile(rsrc,"brmod","exec"),BR.__dict__)
if mode=="fallback":
    orig=BR.StaticBaselineRouter.select_route
    def f(self):
        if all(s.status==RouteHealthStatus.TRIPPED for s in self._route_states.values()):
            aid=max(self._route_states, key=lambda a:self._route_states[a].tripped_at_tx)
            return aid,{k:1.0 if k==aid else 0.0 for k in self._priority_order}
        return orig(self)
    BR.StaticBaselineRouter.select_route=f
ns={"__name__":"cmp"}; exec(compile(open("../scripts/compare_psr.py").read(),"cmp","exec"),ns)
for m in (1,3):
    print(mode, f"M={m} seed42 baseline PSR:", round(asyncio.run(ns["execute_scenario"]("baseline","b.db",threshold_m=m))["global_metrics"]["psr"]*100,2))
```

</details>

<details><summary><code>gray.py</code></summary>

```python
import asyncio, httpx, logging, statistics as st, sys
logging.disable(logging.CRITICAL)
from acquirer_sim.app import create_app
from acquirer_sim.models import AuthorizeRequest, LatencyConfig
from baseline_router.models import BaselineRouterConfig, FailoverPolicyConfig
from baseline_router.router import StaticBaselineRouter
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig
async def run(kind, seed, rseed=777):
    sim = create_app(default_acquirers=["acquirer_alpha","acquirer_beta"], default_base_rate=0.95,
                     default_latency=LatencyConfig(base_ms=0.0, jitter_ms=0.0), seed=seed)
    a=sim.state.registry.get("acquirer_alpha"); a.set_success_rate(0.95); sim.state.registry.get("acquirer_beta").set_success_rate(0.94)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=sim), base_url="http://t") as c:
        routes=[AcquirerRouteConfig(acquirer_id=x, base_url="http://t", state_config=AcquirerStateConfig(decay_factor=0.95)) for x in ("acquirer_alpha","acquirer_beta")]
        if kind=="base":
            r=StaticBaselineRouter(BaselineRouterConfig(routes=routes, priority_order=["acquirer_alpha","acquirer_beta"], failover_policy=FailoverPolicyConfig(consecutive_failure_threshold=3, cooldown_transactions=30, failback_mode="probe")), http_client=c)
        else:
            r=BanditRouter(RouterConfig(routes=routes, pid_config=PIDConfig(kp=0.12,ki=0.005,kd=0.25,integral_max=1.0,min_allocation=0.03,actuation_mode="deficit"), seed=rseed), http_client=c)
        ok=0
        for i in range(150):
            if i==50: a.set_success_rate(0.60)
            if i==100: a.set_success_rate(0.95)
            ok += (await r.route(AuthorizeRequest(transaction_id=f"t{i}", amount=50.0))).authorized
        return ok/150*100
print("seed42: baseline M=3 gray PSR=%.2f  loom=%.2f" % (asyncio.run(run("base",42)), asyncio.run(run("loom",42))))
b=[asyncio.run(run("base",s)) for s in range(30)]; l=[asyncio.run(run("loom",s,s+1000)) for s in range(30)]
d=[x-y for x,y in zip(l,b)]
print("30 seeds: baseline mean=%.2f sd=%.2f | loom mean=%.2f sd=%.2f | loom-base mean=%.2f sd=%.2f, loom wins %d/30" % (st.mean(b),st.stdev(b),st.mean(l),st.stdev(l),st.mean(d),st.stdev(d),sum(x>0 for x in d)))
```

</details>

<details><summary><code>ledger.py</code></summary>

```python
import sqlite3, os
from data_layer.sqlite_logger import load_schema_sql
from router_core.app import create_router_app
from router_core.server import parse_args, build_router_config
app = create_router_app(config=build_router_config(parse_args([])))
r = app.state.router
print("live server router: metrics_logger =", r.metrics_logger, "| event_publisher =", r.event_publisher, "| registry =", type(r.registry).__name__)
print("live pid actuation_mode =", r.config.pid_config.actuation_mode, "| acquirers =", r.list_acquirer_ids())
p="ledger_test.db"
if os.path.exists(p): os.remove(p)
c=sqlite3.connect(p); c.executescript(load_schema_sql())
c.execute("INSERT INTO transactions(transaction_id,timestamp,chosen_acquirer,allocation_weight,status,authorized,success,routing_latency_ms,acquirer_latency_ms,total_latency_ms,smoothed_allocation_json,thompson_samples_json) VALUES('t1',0,'a',1,'DECLINED',0,0,0,0,0,'{}','{}')"); c.commit()
try:
    c.execute("UPDATE transactions SET status='AUTHORIZED', authorized=1, success=1 WHERE transaction_id='t1'")
except sqlite3.Error as e:
    print("UPDATE blocked, caught in Python as", type(e).__name__, "->", e)
c.execute("DROP TRIGGER prevent_transactions_update")
c.execute("UPDATE transactions SET status='AUTHORIZED', authorized=1, success=1 WHERE transaction_id='t1'"); c.commit()
print("after DROP TRIGGER, row now:", c.execute("select status,authorized from transactions").fetchall())
c.execute("DROP TRIGGER prevent_transactions_delete"); c.execute("DELETE FROM transactions"); c.commit()
print("after DROP TRIGGER + DELETE, rows:", c.execute("select count(*) from transactions").fetchone()[0])
```

</details>

<details><summary><code>ms.py</code></summary>

```python
import asyncio, httpx, logging, statistics as st, math
logging.disable(logging.CRITICAL)
from acquirer_sim.app import create_app
from acquirer_sim.models import AuthorizeRequest, LatencyConfig, OutageToggleRequest
from baseline_router.models import BaselineRouterConfig, FailoverPolicyConfig
from baseline_router.router import StaticBaselineRouter
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig
async def run(kind, seed, m=3):
    sim = create_app(default_acquirers=["acquirer_alpha","acquirer_beta"], default_base_rate=0.95,
                     default_latency=LatencyConfig(base_ms=0.0, jitter_ms=0.0), seed=seed)
    sim.state.registry.get("acquirer_beta").set_success_rate(0.94)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=sim), base_url="http://t") as c:
        routes=[AcquirerRouteConfig(acquirer_id=x, base_url="http://t", state_config=AcquirerStateConfig(decay_factor=0.95)) for x in ("acquirer_alpha","acquirer_beta")]
        if kind=="base":
            r=StaticBaselineRouter(BaselineRouterConfig(routes=routes, priority_order=["acquirer_alpha","acquirer_beta"], failover_policy=FailoverPolicyConfig(consecutive_failure_threshold=m, cooldown_transactions=30)), http_client=c)
        else:
            r=BanditRouter(RouterConfig(routes=routes, pid_config=PIDConfig(actuation_mode="deficit"), seed=seed+1000), http_client=c)
        ok=0
        for i in range(150):
            if i in (50,100): await c.post("/acquirers/acquirer_alpha/admin/outage", json=OutageToggleRequest(active=(i==50)).model_dump())
            ok += (await r.route(AuthorizeRequest(transaction_id=f"t{i}", amount=50.0))).authorized
        return ok/1.5
N=100
L=[asyncio.run(run("loom",s)) for s in range(N)]
for m in (1,3,5):
    B=[asyncio.run(run("base",s,m)) for s in range(N)]
    d=[x-y for x,y in zip(L,B)]; se=st.stdev(d)/math.sqrt(N)
    print(f"M={m}: base mean={st.mean(B):.2f} sd={st.stdev(B):.2f} | loom mean={st.mean(L):.2f} sd={st.stdev(L):.2f} | diff={st.mean(d):+.2f} 95%CI[{st.mean(d)-1.96*se:+.2f},{st.mean(d)+1.96*se:+.2f}] loom wins {sum(x>0 for x in d)}/ties {sum(x==0 for x in d)}/{N}")
```

</details>

<details><summary><code>p8_pid.py</code></summary>

```python
"""Does value-scaled exploration control where a high-value tx goes when PID is on?"""
import asyncio, httpx
from acquirer_sim.app import create_app
from acquirer_sim.models import AuthorizeRequest, LatencyConfig, OutageToggleRequest
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig
from router_core.value_policy import ValueScaledExplorationConfig

async def run(vs_enabled, amount, actuation):
    sim = create_app(default_acquirers=["acquirer_alpha","acquirer_beta"], default_base_rate=0.95,
                     default_latency=LatencyConfig(base_ms=0.0, jitter_ms=0.0), seed=42)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=sim), base_url="http://t") as c:
        routes=[AcquirerRouteConfig(acquirer_id=a, base_url="http://t", state_config=AcquirerStateConfig(decay_factor=0.95)) for a in ("acquirer_alpha","acquirer_beta")]
        r=BanditRouter(RouterConfig(routes=routes, pid_config=PIDConfig(actuation_mode=actuation), seed=777,
              value_scaled_config=ValueScaledExplorationConfig(enabled=vs_enabled, tau=100.0)), http_client=c)
        for i in range(50): await r.route(AuthorizeRequest(transaction_id=f"w{i}", amount=10.0))
        await c.post("/acquirers/acquirer_alpha/admin/outage", json=OutageToggleRequest(active=True).model_dump())
        hv_to_dead=0; hv=0
        for i in range(50):
            amt = amount if i % 5 == 0 else 10.0
            res = await r.route(AuthorizeRequest(transaction_id=f"o{i}", amount=amt))
            if amt==amount:
                hv+=1; hv_to_dead += res.selected_acquirer=="acquirer_alpha"
        return hv, hv_to_dead
for act in ("deficit","stochastic"):
    for vs in (False, True):
        print(act, "value_scaling" if vs else "no_scaling", "high-value(5000) tx during outage, sent to dead alpha:", asyncio.run(run(vs, 5000.0, act)))
```

</details>

<details><summary><code>p8_pid_seeds.py</code></summary>

```python
import asyncio, httpx, statistics as st
from acquirer_sim.app import create_app
from acquirer_sim.models import AuthorizeRequest, LatencyConfig, OutageToggleRequest
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig
from router_core.value_policy import ValueScaledExplorationConfig
import logging; logging.disable(logging.CRITICAL)
async def run(seed, vs, act):
    sim = create_app(default_acquirers=["acquirer_alpha","acquirer_beta"], default_base_rate=0.95,
                     default_latency=LatencyConfig(base_ms=0.0, jitter_ms=0.0), seed=seed)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=sim), base_url="http://t") as c:
        routes=[AcquirerRouteConfig(acquirer_id=a, base_url="http://t", state_config=AcquirerStateConfig(decay_factor=0.95)) for a in ("acquirer_alpha","acquirer_beta")]
        r=BanditRouter(RouterConfig(routes=routes, pid_config=PIDConfig(actuation_mode=act), seed=seed+1000,
              value_scaled_config=ValueScaledExplorationConfig(enabled=vs, tau=100.0)), http_client=c)
        for i in range(50): await r.route(AuthorizeRequest(transaction_id=f"w{i}", amount=10.0))
        await c.post("/acquirers/acquirer_alpha/admin/outage", json=OutageToggleRequest(active=True).model_dump())
        dead=0
        for i in range(50):
            amt = 5000.0 if i % 5 == 0 else 10.0
            res = await r.route(AuthorizeRequest(transaction_id=f"o{i}", amount=amt))
            dead += (amt==5000.0 and res.selected_acquirer=="acquirer_alpha")
        return dead
for act in ("deficit","stochastic"):
    for vs in (False, True):
        xs=[asyncio.run(run(s, vs, act)) for s in range(30)]
        print(f"{act:10s} scaling={vs!s:5s}  high-value tx (10/run) sent to dead alpha: mean={st.mean(xs):.2f} sd={st.stdev(xs):.2f} n=30")
```

</details>

<details><summary><code>phase7_inproc.py</code></summary>

```python
"""Phase 7 QA script's routing config, in-process (no TCP/WS), to compute the README Phase-7 row columns."""
import asyncio, logging, httpx
from acquirer_sim.app import create_app
from acquirer_sim.models import AuthorizeRequest, LatencyConfig, OutageBehavior
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig
logging.disable(logging.CRITICAL)
IDS = ["acquirer_alpha", "acquirer_beta", "acquirer_gamma"]
async def main():
    sim = create_app(default_acquirers=IDS, default_base_rate=0.95, default_latency=LatencyConfig(base_ms=0.0, jitter_ms=0.0), seed=42)
    for a, p in zip(IDS, (0.95, 0.90, 0.85)): sim.state.registry.get(a).set_success_rate(p)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=sim), base_url="http://mock-sim:8001") as c:
        r = BanditRouter(config=RouterConfig(routes=[AcquirerRouteConfig(acquirer_id=a, base_url="http://127.0.0.1:8001", state_config=AcquirerStateConfig(alpha_prior=1.0, beta_prior=1.0, decay_factor=0.95)) for a in IDS], seed=777, pid_config=PIDConfig(kp=0.12, ki=0.005, kd=0.25, integral_max=1.0, min_allocation=0.03)), http_client=c)
        res = []
        for seq in range(1, 151):
            if seq == 51: sim.state.registry.get("acquirer_alpha").set_outage(active=True, behavior=OutageBehavior.RETURN_DECLINE)
            if seq == 101: sim.state.registry.get("acquirer_alpha").set_outage(active=False, behavior=OutageBehavior.RETURN_DECLINE)
            res.append(await r.route(AuthorizeRequest(transaction_id=f"tx_gauntlet_{seq:03d}", amount=50.0)))
    au = [x.authorized for x in res]; sel = [x.selected_acquirer for x in res]; W = [x.smoothed_allocation for x in res]
    o = sel[50:100]
    print("warm/out/rec PSR:", sum(au[:50])*2, sum(au[50:100])*2, sum(au[100:])*2, " auth:", sum(au), "/150 =", round(sum(au)/1.5, 2), "%")
    print("fail_alpha_outage:", sum(1 for k in range(50,100) if sel[k]=="acquirer_alpha" and not au[k]), " outage_flips:", sum(1 for k in range(1,50) if o[k]!=o[k-1]))
    print("dw alpha-only max: %.2f%%  dw any-arm max: %.2f%%" % (100*max(abs(W[k]["acquirer_alpha"]-W[k-1]["acquirer_alpha"]) for k in range(1,150)), 100*max(max(abs(W[k][a]-W[k-1][a]) for a in IDS) for k in range(1,150))))
    print("recovery tx to alpha:", sum(1 for k in range(100,150) if sel[k]=="acquirer_alpha"))
asyncio.run(main())
```

</details>

<details><summary><code>simplex_fuzz.py</code></summary>

```python
import numpy as np, math
from router_core.pid import project_to_bounded_simplex as P
rng=np.random.default_rng(0); bad_sum=bad_floor=nan=0; worst=0; ex=None
for t in range(200000):
    k=int(rng.integers(2,11)); f=float(rng.uniform(0,0.999/k))
    w={str(i):float(x) for i,x in enumerate(rng.normal(0,rng.choice([0.1,1,10,1e6]),k))}
    out=P(w,f); s=sum(out.values())
    if any(math.isnan(v) for v in out.values()): nan+=1; continue
    if abs(s-1)>1e-9: bad_sum+=1; worst=max(worst,abs(s-1)); ex=ex or (w,f,out,s)
    if min(out.values())<f-1e-12: bad_floor+=1
print("cases=200000 sum!=1 (>1e-9):",bad_sum,"worst |sum-1|:",worst,"floor violations:",bad_floor,"nan:",nan)
if ex: print("example:", {k:round(v,4) for k,v in ex[0].items()}, "floor=",round(ex[1],4), "-> sum", ex[3])
print("NaN input:", P({"a":float("nan"),"b":0.5},0.03))
print("inf input:", (lambda: P({"a":float("inf"),"b":0.5},0.03))() if True else None)
```

</details>

<details><summary><code>steady.py</code></summary>

```python
import asyncio, httpx, logging, statistics as st
logging.disable(logging.CRITICAL)
from acquirer_sim.app import create_app
from acquirer_sim.models import AuthorizeRequest, LatencyConfig
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig
async def run(seed, pb, decay, act, n=2000):
    sim=create_app(default_acquirers=["acquirer_alpha","acquirer_beta"], default_base_rate=0.95, default_latency=LatencyConfig(base_ms=0, jitter_ms=0), seed=seed*10)
    sim.state.registry.get("acquirer_beta").set_success_rate(pb)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=sim), base_url="http://t") as c:
        routes=[AcquirerRouteConfig(acquirer_id=a, base_url="http://t", state_config=AcquirerStateConfig(decay_factor=decay)) for a in ("acquirer_alpha","acquirer_beta")]
        r=BanditRouter(RouterConfig(routes=routes, pid_config=PIDConfig(actuation_mode=act), seed=777+seed), http_client=c)
        sel=[(await r.route(AuthorizeRequest(transaction_id=f"t{i}", amount=50.0))).selected_acquirer for i in range(n)]
    return sum(s=="acquirer_beta" for s in sel[500:])/len(sel[500:])
for pb in (0.94,0.92,0.90):
    for decay,act,lab in ((0.95,"deficit","bench"),(0.98,"stochastic","live"),(0.9999,"stochastic","decay.9999")):
        xs=[asyncio.run(run(s,pb,decay,act)) for s in range(10)]
        print(f"alpha .95 / beta {pb}: {lab:11s} share to worse beta (tx 500-2000) mean={st.mean(xs)*100:.1f}% sd={st.stdev(xs)*100:.1f} n=10")
```

</details>

<details><summary><code>trace_checks.py</code></summary>

```python
"""Check README narrative details: M=1 cascade (Tx 57 trip, Tx 58-86 to dead Alpha) and Phase-4 post-recovery alpha posterior."""
import asyncio, logging, httpx
from acquirer_sim.app import create_app
from acquirer_sim.models import AuthorizeRequest, LatencyConfig, OutageToggleRequest
from baseline_router.models import BaselineRouterConfig, FailoverPolicyConfig
from baseline_router.router import StaticBaselineRouter
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig
logging.disable(logging.CRITICAL)
A, B = "acquirer_alpha", "acquirer_beta"
async def go(kind):
    sim = create_app(default_acquirers=[A, B], default_base_rate=0.95, default_latency=LatencyConfig(base_ms=0.0, jitter_ms=0.0), seed=42)
    sim.state.registry.get(A).set_success_rate(0.95); sim.state.registry.get(B).set_success_rate(0.94)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=sim), base_url="http://testserver") as c:
        routes = [AcquirerRouteConfig(acquirer_id=a, base_url="http://testserver", state_config=AcquirerStateConfig(decay_factor=0.95)) for a in (A, B)]
        if kind == "m1":
            r = StaticBaselineRouter(config=BaselineRouterConfig(routes=routes, priority_order=[A, B], failover_policy=FailoverPolicyConfig(consecutive_failure_threshold=1, cooldown_transactions=30, failback_mode="probe")), http_client=c)
        else:
            r = BanditRouter(config=RouterConfig(routes=routes, pid_config=PIDConfig(kp=0.12, ki=0.005, kd=0.25, integral_max=1.0, min_allocation=0.03, actuation_mode="deficit"), seed=777), http_client=c)
        out = []
        for tx in range(1, 151):
            if tx == 51: await c.post(f"/acquirers/{A}/admin/outage", json=OutageToggleRequest(active=True).model_dump())
            if tx == 101: await c.post(f"/acquirers/{A}/admin/outage", json=OutageToggleRequest(active=False).model_dump())
            res = await r.route(AuthorizeRequest(transaction_id=f"t{tx}", amount=50.0))
            sa = r.get_state(A) if kind == "p4" else None
            out.append((tx, res.selected_acquirer[9:], res.authorized, sa))
        return out
async def main():
    m1 = await go("m1")
    print("M=1 outage trace (tx:route/auth):", " ".join(f"{t}:{s[0]}{'+' if a else '-'}" for t, s, a, _ in m1[50:100]))
    run = [];
    for t, s, a, _ in m1[50:100]:
        run.append(t) if s == "alpha" else None
    print("M=1 Tx routed to alpha during outage:", run)
    p4 = await go("p4")
    for t, s, a, st in p4[95:150]:
        if s == "alpha" or t in (100,):
            print(f"P4 tx{t} -> {s} auth={a} alpha_state a={st.alpha:.2f} b={st.beta:.2f} mean={st.expected_success_rate:.3f}")
asyncio.run(main())
```

</details>

<details><summary><code>ws_block.py</code></summary>

```python
"""Does a stalled WebSocket client (connected, never reads) add latency to / block POST /route?
Real uvicorn on 127.0.0.1:48011; acquirer mocked in-process (instant AUTHORIZED)."""
import asyncio, base64, os, socket, threading, time, uuid
import httpx, uvicorn
from acquirer_sim.models import AuthorizeResponse
from router_core.app import create_router_app
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig
from router_core.router import BanditRouter

def handler(request):
    import json
    body = json.loads(request.content)
    return httpx.Response(200, json=AuthorizeResponse(
        transaction_id=body["transaction_id"], acquirer_id="a", status="AUTHORIZED", authorized=True,
        authorization_code="AUTH_X", simulated_latency_ms=0.0, timestamp=time.time()).model_dump())

cfg = RouterConfig(routes=[AcquirerRouteConfig(acquirer_id=a, base_url="http://x") for a in ["a", "b", "c"]],
                   pid_config=PIDConfig(), seed=1)
router = BanditRouter(config=cfg, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
app = create_router_app(router=router)
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=48011, log_level="error", ws=os.environ.get("WSIMPL", "auto")))
threading.Thread(target=server.run, daemon=True).start()
time.sleep(1.5)

# raw stalled WS client: handshake, then never read
s = socket.create_connection(("127.0.0.1", 48011))
s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
key = base64.b64encode(os.urandom(16)).decode()
s.sendall((f"GET /ws/telemetry HTTP/1.1\r\nHost: 127.0.0.1:48011\r\nUpgrade: websocket\r\n"
           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
time.sleep(0.3)
print("handshake:", s.recv(200).split(b"\r\n")[0])
# do not read anymore

lat = []
with httpx.Client(timeout=5.0) as c:
    for i in range(3000):
        t0 = time.perf_counter()
        try:
            r = c.post("http://127.0.0.1:48011/route", json={"transaction_id": f"tx_{uuid.uuid4().hex}", "amount": 10})
            lat.append((time.perf_counter() - t0) * 1000)
        except httpx.ReadTimeout:
            print(f"POST /route #{i} TIMED OUT after 5s (blocked by stalled websocket)")
            break
print(f"completed {len(lat)} routes; median {sorted(lat)[len(lat)//2]:.2f}ms; max {max(lat):.1f}ms")
server.should_exit = True
```

</details>


#### Lens A, credibility (`loom-audit-A/_scratch/`)

<details><summary><code>fallback.py</code></summary>

```python
import asyncio, logging, statistics
logging.disable(logging.CRITICAL)
import baseline_router.router as br
from baseline_router.models import RouteHealthStatus
orig_select = br.StaticBaselineRouter.select_route
def select_mrt(self):
    # identical, except exhaustion fallback goes to the most-recently-tripped route instead of dead primary
    aid, alloc = orig_select(self)
    if all(s.status == RouteHealthStatus.TRIPPED for s in self._route_states.values()):
        aid = max(self._route_states.values(), key=lambda s: s.tripped_at_tx or -1).acquirer_id
        alloc = {k: 1.0 if k == aid else 0.0 for k in self._priority_order}
    return aid, alloc
src=open("scripts/compare_psr.py").read()
ns={"__name__":"x"}; exec(compile(src,"c","exec"),ns)
async def run(label):
    r=[]
    for s in [42]+list(range(30)):
        b=await ns["execute_scenario"]("baseline","_scratch/f_b.db",seed=s,threshold_m=1)
        r.append(b["global_metrics"]["psr"])
    print(label,"seed42:",round(r[0]*100,2),"| 30-seed mean:",round(statistics.mean(r[1:])*100,2))
async def main():
    await run("M=1 as shipped (fallback->dead primary)")
    br.StaticBaselineRouter.select_route = select_mrt
    await run("M=1 fallback->most-recently-tripped")
asyncio.run(main())
```

</details>

<details><summary><code>gray.py</code></summary>

```python
import asyncio, logging, statistics
logging.disable(logging.CRITICAL)
src=open("scripts/compare_psr.py").read()
on='''        await client.post(
            "/acquirers/acquirer_alpha/admin/outage",
            json=OutageToggleRequest(active=True).model_dump(),
        )'''
off=on.replace("active=True","active=False")
assert on in src and off in src
src=src.replace(on,'        sim_app.state.registry.get("acquirer_alpha").set_success_rate(0.60)').replace(off,'        sim_app.state.registry.get("acquirer_alpha").set_success_rate(0.95)')
src=src.replace("seed=777,","seed=LOOM_SEED,")
ns={"__name__":"x"}; exec(compile(src,"g","exec"),ns)
async def main():
    ns["LOOM_SEED"]=777
    b=await ns["execute_scenario"]("baseline","_scratch/g_b.db",seed=42,threshold_m=3)
    l=await ns["execute_scenario"]("loom","_scratch/g_l.db",seed=42)
    print("seed42 gray: baseline PSR",round(b["global_metrics"]["psr"]*100,2),"outage",b["outage_psr"]," loom PSR",round(l["global_metrics"]["psr"]*100,2),"outage",l["outage_psr"],"loom dw",round(l["max_step_delta"]*100,2))
    d=[]
    for s in range(30):
        ns["LOOM_SEED"]=1000+s
        b=await ns["execute_scenario"]("baseline","_scratch/g_b.db",seed=s,threshold_m=3)
        l=await ns["execute_scenario"]("loom","_scratch/g_l.db",seed=s)
        d.append((b["global_metrics"]["psr"],l["global_metrics"]["psr"]))
    print("30 seeds gray: baseline mean",round(statistics.mean(x for x,_ in d)*100,2),"loom mean",round(statistics.mean(y for _,y in d)*100,2),"mean diff bps",round(statistics.mean(y-x for x,y in d)*1e4),"loom wins",sum(1 for x,y in d if y>x))
asyncio.run(main())
```

</details>

<details><summary><code>issuer.py</code></summary>

```python
import asyncio, logging, statistics, sys, types
logging.disable(logging.CRITICAL)
import baseline_router.router as br
src=open(br.__file__).read()
needle="                success = payload.authorized\n"
assert needle in src
src=src.replace(needle,"                success = payload.authorized or payload.decline_code == 'DO_NOT_HONOR'\n")
exec(compile(src, br.__file__, "exec"), br.__dict__)   # redefine StaticBaselineRouter in-place
cmp=open("scripts/compare_psr.py").read(); ns={"__name__":"x"}; exec(compile(cmp,"c","exec"),ns)
ns["StaticBaselineRouter"]=br.StaticBaselineRouter
async def main():
    for m in (1,3):
        r=[]
        for s in [42]+list(range(30)):
            b=await ns["execute_scenario"]("baseline","_scratch/i_b.db",seed=s,threshold_m=m)
            r.append(b["global_metrics"]["psr"])
        print(f"M={m} breaker ignores issuer DO_NOT_HONOR: seed42 {r[0]*100:.2f} | 30-seed mean {statistics.mean(r[1:])*100:.2f}")
asyncio.run(main())
```

</details>

<details><summary><code>issuer_loom.py</code></summary>

```python
import asyncio, logging, statistics
logging.disable(logging.CRITICAL)
import router_core.router as rr
src=open(rr.__file__).read()
needle="success = payload.authorized  # True if AUTHORIZED, False if DECLINED"
assert needle in src
src=src.replace(needle,"success = payload.authorized or payload.decline_code == 'DO_NOT_HONOR'")
exec(compile(src, rr.__file__, "exec"), rr.__dict__)
cmp=open("scripts/compare_psr.py").read().replace("seed=777,","seed=LOOM_SEED,"); ns={"__name__":"x"}; exec(compile(cmp,"c","exec"),ns)
ns["BanditRouter"]=rr.BanditRouter
async def main():
    r=[]
    for i,s in enumerate([42]+list(range(30))):
        ns["LOOM_SEED"]=777 if i==0 else 1000+s
        l=await ns["execute_scenario"]("loom","_scratch/il.db",seed=s)
        r.append(l["global_metrics"]["psr"])
    print(f"Loom ignoring issuer DO_NOT_HONOR: seed42 {r[0]*100:.2f} | 30-seed mean {statistics.mean(r[1:])*100:.2f}")
asyncio.run(main())
```

</details>

<details><summary><code>m1trace.py</code></summary>

```python
import sqlite3
c=sqlite3.connect("_scratch/b1.db")
rows=c.execute("select id, chosen_acquirer, authorized, decline_code from transactions order by id").fetchall()
for r in rows[48:90]:
    print(r[0], r[1][-5:], r[2], r[3], end=" | ")
print()
alpha=[r[0] for r in rows[50:100] if r[1]=="acquirer_alpha"]
print("alpha tx in outage:",alpha)
dec=[(r[0],r[1][-5:],r[3]) for r in rows[:50] if not r[2]]
print("warmup declines:",dec)
```

</details>

<details><summary><code>multiseed.py</code></summary>

```python
import asyncio, sys, statistics, logging, os, re
logging.disable(logging.CRITICAL)
src = open("scripts/compare_psr.py").read()
# parametrize loom seed, decay, actuation
src = src.replace("seed=777,", "seed=LOOM_SEED,").replace('actuation_mode="deficit"', "actuation_mode=ACT").replace("decay_factor=0.95", "decay_factor=DECAY")
ns = {"__name__": "x"}
exec(compile(src, "cmp", "exec"), ns)
async def run(n, m, decay, act, fixed_loom_seed=False):
    rows=[]
    for s in range(n):
        ns["LOOM_SEED"] = 777 if fixed_loom_seed else 1000+s
        ns["DECAY"]=decay; ns["ACT"]=act
        b = await ns["execute_scenario"]("baseline", f"_scratch/ms_b.db", seed=s, threshold_m=m)
        l = await ns["execute_scenario"]("loom", f"_scratch/ms_l.db", seed=s)
        rows.append((b["global_metrics"]["psr"], l["global_metrics"]["psr"], b["outage_flips"], l["outage_flips"], l["max_step_delta"]))
    return rows
def summ(label, rows):
    d=[l-b for b,l,*_ in rows]
    bm=statistics.mean(r[0] for r in rows); lm=statistics.mean(r[1] for r in rows)
    sd=statistics.stdev(d); n=len(d)
    wins=sum(1 for x in d if x>0); ties=sum(1 for x in d if x==0)
    print(f"{label}: n={n} baseline mean={bm:.4f} sd={statistics.stdev(r[0] for r in rows):.4f} | loom mean={lm:.4f} sd={statistics.stdev(r[1] for r in rows):.4f} | diff mean={statistics.mean(d)*1e4:+.0f}bps sd={sd*1e4:.0f}bps 95%CI=[{(statistics.mean(d)-1.96*sd/n**0.5)*1e4:+.0f},{(statistics.mean(d)+1.96*sd/n**0.5)*1e4:+.0f}] loom wins={wins} ties={ties} | diff min={min(d)*1e4:+.0f} max={max(d)*1e4:+.0f} | loom flips mean={statistics.mean(r[3] for r in rows):.1f} base flips mean={statistics.mean(r[2] for r in rows):.1f} | loom dw max={max(r[4] for r in rows):.4f}")
async def main():
    N=int(sys.argv[1])
    for m in (1,3,5):
        summ(f"M={m} bench-config(decay .95, deficit)", await run(N,m,0.95,"deficit"))
    summ("M=1 live-config(decay .98, stochastic)", await run(N,1,0.98,"stochastic"))
    summ("M=3 live-config(decay .98, stochastic)", await run(N,3,0.98,"stochastic"))
asyncio.run(main())
```

</details>

<details><summary><code>ramp.py</code></summary>

```python
import sqlite3, json
c=sqlite3.connect("_scratch/l.db")
rows=c.execute("select id, chosen_acquirer, smoothed_allocation_json from transactions order by id").fetchall()
w=[json.loads(r[2])["acquirer_alpha"] for r in rows]
print("alpha w at tx50..:", [round(x,3) for x in w[49:100:5]])
t=next(i+1 for i in range(50,150) if w[i]<0.05); print("first tx with alpha w<0.05:",t,"=> ramp length",t-50,"tx")
beta_share=sum(1 for r in rows[50:100] if r[1]=="acquirer_beta")/50; print("beta share during outage (loom):",beta_share)
print("alpha w warmup min/max:", round(min(w[:50]),3), round(max(w[:50]),3))
print("recovery alpha w at tx 101..150 (every 7):",[round(x,3) for x in w[100:150:7]])
print("recovery alpha share:",sum(1 for r in rows[100:150] if r[1]=="acquirer_alpha")/50)
```

</details>

<details><summary><code>recov.py</code></summary>

```python
import asyncio, logging, sqlite3, json
logging.disable(logging.CRITICAL)
src=open("scripts/compare_psr.py").read().replace("seed=777,","seed=LOOM_SEED,")
ns={"__name__":"x"}; exec(compile(src,"c","exec"),ns)
async def main():
    res=[]
    for s in range(20):
        ns["LOOM_SEED"]=1000+s
        await ns["execute_scenario"]("loom","_scratch/rc.db",seed=s)
        c=sqlite3.connect("_scratch/rc.db")
        w=[json.loads(r[0])["acquirer_alpha"] for r in c.execute("select smoothed_allocation_json from transactions order by id")]
        c.close()
        res.append(round(w[-1],3))
    print("alpha smoothed weight at Tx150 across 20 seeds:",res, "| seeds with w>0.10:",sum(1 for x in res if x>0.10))
asyncio.run(main())
```

</details>

<details><summary><code>refcmp.py</code></summary>

```python
import json, sqlite3
ref=json.load(open("dashboard/src/data/baselineReferenceRun.json"))["transactions"]
c=sqlite3.connect("_scratch/b.db")
rows=c.execute("select transaction_id, chosen_acquirer, authorized from transactions order by id").fetchall()
mism=[(r["id"],r["chosen_acquirer"],r["authorized"],db[1],db[2]) for r,db in zip(ref,rows) if (r["chosen_acquirer"],int(r["authorized"]))!=(db[1],db[2]) or r["tx_id"]!=db[0]]
print("ref n",len(ref),"db n",len(rows),"mismatches",len(mism), mism[:5])
print("ref auth",sum(r["authorized"] for r in ref))
```

</details>

<details><summary><code>sweep.py</code></summary>

```python
import asyncio, logging
logging.disable(logging.CRITICAL)
ns={"__name__":"x"}; exec(compile(open("scripts/compare_psr.py").read(),"c","exec"),ns)
async def main():
    l=await ns["execute_scenario"]("loom","_scratch/sw_l.db",seed=42)
    lp=l["global_metrics"]["psr"]*100
    for m in (1,3):
        out=[]
        for n in (5,10,20,30,40,50):
            b=await ns["execute_scenario"]("baseline","_scratch/sw_b.db",seed=42,threshold_m=m,cooldown_n=n)
            out.append(f"N={n}:{b['global_metrics']['psr']*100:.2f}({(lp-b['global_metrics']['psr']*100)*100:+.0f}bps)")
        print(f"seed42 Loom={lp:.2f} | baseline M={m}: "+"  ".join(out))
asyncio.run(main())
```

</details>

<details><summary><code>trig.py</code></summary>

```python
import sqlite3, math
c=sqlite3.connect(":memory:")
c.executescript(open("data_layer/schema.sql").read())
cols=[r[1] for r in c.execute("PRAGMA table_info(transactions)")]
print("cols",cols[:6])
try:
    c.execute("DELETE FROM transactions")
except sqlite3.IntegrityError as e:
    print("caught normally:", type(e).__name__, e)
c.execute("DROP TRIGGER prevent_transactions_delete"); c.execute("DELETE FROM transactions"); print("after DROP TRIGGER: delete ok")
for p in (0.86,0.92,0.76):
    for n in (150,50):
        print(f"p={p} n={n} SE={math.sqrt(p*(1-p)/n)*100:.2f}pp  95%half={1.96*math.sqrt(p*(1-p)/n)*100:.2f}pp")
def se_diff(p1,p2,n): return math.sqrt(p1*(1-p1)/n+p2*(1-p2)/n)*100
print("diff SE .86 vs .92 n150:", round(se_diff(.86,.92,150),2), "| .86 vs .76 n150:", round(se_diff(.86,.76,150),2), "| outage .72 vs .82 n50:", round(se_diff(.72,.82,50),2), "| .72 vs .38 n50:", round(se_diff(.72,.38,50),2))
```

</details>

<details><summary><code>trig2.py</code></summary>

```python
import sqlite3
c=sqlite3.connect(":memory:"); c.executescript(open("data_layer/schema.sql").read())
c.execute("INSERT INTO transactions(transaction_id,timestamp,chosen_acquirer,allocation_weight,status,authorized,success,routing_latency_ms,acquirer_latency_ms,total_latency_ms,smoothed_allocation_json,thompson_samples_json) VALUES('t1',0,'a',1,'AUTHORIZED',1,1,0,0,0,'{}','{}')")
try: c.execute("DELETE FROM transactions")
except sqlite3.IntegrityError as e: print("caught as ordinary", type(e).__name__, "->", str(e)[:50])
c.execute("DROP TRIGGER prevent_transactions_delete"); c.execute("DELETE FROM transactions")
print("rows after DROP TRIGGER + DELETE:", c.execute("select count(*) from transactions").fetchone()[0])
c.execute("PRAGMA writable_schema");
```

</details>


#### Lens B, engineering (`loom-audit-B/_audit/`)

<details><summary><code>exp10_sqlite.py</code></summary>

```python
"""SQLite ledger: immutability triggers & bypasses, error class, MetricsLogger queue/close/poison semantics.
All DBs under _audit/db/ (scratch)."""
import asyncio, os, shutil, sqlite3, subprocess, sys, time, uuid, logging
logging.basicConfig(level=logging.CRITICAL)
from data_layer.sqlite_logger import MetricsLogger, SQLiteMetricsStore
from router_core.models import RoutingResult
from router_core.state import AcquirerStateSnapshot

D = os.path.join(os.path.dirname(__file__), "db"); shutil.rmtree(D, ignore_errors=True); os.makedirs(D)
def rr(txid=None):
    return RoutingResult(transaction_id=txid or f"tx_{uuid.uuid4().hex}", selected_acquirer="a",
        thompson_samples={"a": .9}, status="AUTHORIZED", authorized=True, success=True,
        routing_latency_ms=0.1, acquirer_latency_ms=1, total_latency_ms=1.1,
        state_snapshot=AcquirerStateSnapshot("a", 2, 1, 1, 1, 0, 1, time.time()), timestamp=time.time())

# 1. triggers
p = os.path.join(D, "ledger.db"); s = SQLiteMetricsStore(db_path=p); s.log_routing_result(rr("tx_1")); s.close()
c = sqlite3.connect(p)
for sql in ["UPDATE transactions SET success=0 WHERE transaction_id='tx_1'", "DELETE FROM transactions"]:
    try:
        c.execute(sql); print("NOT BLOCKED:", sql)
    except sqlite3.IntegrityError as e:
        print(f"blocked + catchable as sqlite3.{type(e).__name__}: {e}")
# INSERT OR REPLACE bypass (REPLACE deletes conflicting row; delete triggers fire only with recursive_triggers)
c.execute("INSERT OR REPLACE INTO transactions (transaction_id,timestamp,chosen_acquirer,allocation_weight,status,"
          "authorized,success,routing_latency_ms,acquirer_latency_ms,total_latency_ms,smoothed_allocation_json,"
          "thompson_samples_json) VALUES ('tx_1',0,'FORGED',1,'DECLINED',0,0,0,0,0,'{}','{}')"); c.commit()
print("INSERT OR REPLACE rewrote tx_1 ->", c.execute("SELECT chosen_acquirer,status FROM transactions WHERE transaction_id='tx_1'").fetchone())
c.execute("DROP TRIGGER prevent_transactions_update")
c.execute("UPDATE transactions SET status='AUTHORIZED' WHERE transaction_id='tx_1'"); c.commit()
print("after DROP TRIGGER, UPDATE succeeded:", c.execute("SELECT status FROM transactions").fetchone())
c.execute("DROP TABLE acquirer_outcomes"); c.commit()
print("DROP TABLE acquirer_outcomes succeeded; tables now:", [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")])
c.close()
os.replace(p, p + ".bak"); open(p, "wb").close(); print("file replaced by empty file: size", os.path.getsize(p))

# 2. reset-demo --force via CLI on scratch DB (skip redis)
p2 = os.path.join(D, "demo.db"); s = SQLiteMetricsStore(db_path=p2); [s.log_routing_result(rr()) for _ in range(5)]; s.close()
out = subprocess.run([sys.executable, "-m", "data_layer.cli", "reset-demo", "--force", "--db-path", p2, "--skip-redis"],
                     capture_output=True, text=True, cwd=os.path.dirname(D) + "/..")
print("reset-demo rc", out.returncode, "| rows after:", sqlite3.connect(p2).execute("SELECT COUNT(*) FROM transactions").fetchone()[0])
if out.returncode: print(out.stdout[-400:], out.stderr[-400:])

async def logger_tests():
    # 3. before start
    m = MetricsLogger(db_path=os.path.join(D, "m0.db")); m.log_routing_result(rr()); print("log before start(): silently dropped (warning only)")
    # 4. tiny queue: drop vs backpressure
    m = MetricsLogger(db_path=os.path.join(D, "m1.db"), max_queue_size=5, batch_size=20, flush_interval_sec=0.05)
    await m.start()
    for _ in range(100): m.log_routing_result(rr())     # no await between -> drain can't run
    await m.close()
    print("queue=5, 100 logged synchronously: rows persisted =", m.total_written)
    # 5. close() race: items pulled into drain batch at time of close
    m = MetricsLogger(db_path=os.path.join(D, "m2.db"), batch_size=50, flush_interval_sec=0.5)
    await m.start()
    for _ in range(30): m.log_routing_result(rr())
    await asyncio.sleep(0.05)  # drain loop has pulled items into its local batch, waiting for deadline
    await m.close()
    n = sqlite3.connect(os.path.join(D, "m2.db")).execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    print("close() during batch accumulation: logged 30, persisted", n)
    # 6. poison: one duplicate tx_id inside a batch
    m = MetricsLogger(db_path=os.path.join(D, "m3.db"), batch_size=20, flush_interval_sec=0.05)
    await m.start()
    m.log_routing_result(rr("dup")); await asyncio.sleep(0.2)
    for i in range(19): m.log_routing_result(rr())
    m.log_routing_result(rr("dup"))   # client retry with same id
    await asyncio.sleep(0.3); await m.close()
    n = sqlite3.connect(os.path.join(D, "m3.db")).execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
    print("batch with 1 duplicate tx_id + 19 unique: persisted rows total =", n, "(expected 20 = 1 + 19)")
asyncio.run(logger_tests())
```

</details>

<details><summary><code>exp10b_close_defaults.py</code></summary>

```python
"""MetricsLogger close() data loss with DEFAULT config (batch 20, flush 0.05s), 20 trials."""
import asyncio, os, sqlite3, time, uuid, logging, tempfile
logging.basicConfig(level=logging.CRITICAL)
from data_layer.sqlite_logger import MetricsLogger
from router_core.models import RoutingResult
from router_core.state import AcquirerStateSnapshot
def rr():
    return RoutingResult(transaction_id=f"tx_{uuid.uuid4().hex}", selected_acquirer="a", thompson_samples={"a": .9},
        status="AUTHORIZED", authorized=True, success=True, routing_latency_ms=0.1, acquirer_latency_ms=1,
        total_latency_ms=1.1, state_snapshot=AcquirerStateSnapshot("a", 2, 1, 1, 1, 0, 1, time.time()), timestamp=time.time())
async def trial(i):
    p = os.path.join(os.path.dirname(__file__), "db", f"def{i}.db")
    m = MetricsLogger(db_path=p); await m.start()
    for _ in range(10):
        m.log_routing_result(rr()); await asyncio.sleep(0.001)
    await m.close()
    return sqlite3.connect(p).execute("SELECT COUNT(*) FROM transactions").fetchone()[0]
async def main():
    res = [await trial(i) for i in range(20)]
    print("persisted out of 10 per trial:", res, "| total lost:", 200 - sum(res))
asyncio.run(main())
```

</details>

<details><summary><code>exp11_seed_sweep.py</code></summary>

```python
"""compare_psr.execute_scenario across 30 simulator seeds (DBs in scratch). README reports seed 42 only."""
import asyncio, os, logging, statistics, sys
logging.disable(logging.CRITICAL)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
from compare_psr import execute_scenario
D = os.path.join(os.path.dirname(__file__), "db")
async def main():
    rows = {"loom": [], "base_m3": [], "base_m1": []}
    for s in range(1, 31):
        rows["loom"].append((await execute_scenario("loom", os.path.join(D, "l.db"), seed=s))["global_metrics"]["psr"])
        rows["base_m3"].append((await execute_scenario("baseline", os.path.join(D, "b3.db"), seed=s, threshold_m=3))["global_metrics"]["psr"])
        rows["base_m1"].append((await execute_scenario("baseline", os.path.join(D, "b1.db"), seed=s, threshold_m=1))["global_metrics"]["psr"])
    for k, v in rows.items():
        print(f"{k:8s} mean={statistics.mean(v)*100:.2f}% sd={statistics.stdev(v)*100:.2f} min={min(v)*100:.1f} max={max(v)*100:.1f}")
    d3 = [a - b for a, b in zip(rows["loom"], rows["base_m3"])]
    print(f"loom - base_m3: mean {statistics.mean(d3)*1e4:.0f} bps, loom worse in {sum(x < 0 for x in d3)}/30 seeds")
    d1 = [a - b for a, b in zip(rows["loom"], rows["base_m1"])]
    print(f"loom - base_m1: mean {statistics.mean(d1)*1e4:.0f} bps, loom worse in {sum(x < 0 for x in d1)}/30 seeds")
    r42 = [await execute_scenario(n, os.path.join(D, "x.db"), seed=42, threshold_m=m) for n, m in [("loom", 3), ("baseline", 3), ("baseline", 1)]]
    print("seed 42 reproduce:", [round(r["global_metrics"]["psr"] * 100, 2) for r in r42])
asyncio.run(main())
```

</details>

<details><summary><code>exp12_sim.py</code></summary>

```python
"""Simulator: typo'd acquirer ids auto-register (admin + authorize succeed); seed stream overlap across seeds;
outage state evaluated after the latency sleep (request in flight when outage toggles is affected)."""
import asyncio, httpx, numpy as np
from acquirer_sim.app import create_app
from acquirer_sim.models import LatencyConfig
from acquirer_sim.simulator import MultiAcquirerSimulator
async def main():
    app = create_app(default_acquirers=["acquirer_alpha"], seed=42, default_latency=LatencyConfig(base_ms=200, jitter_ms=0))
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://s") as c:
        r = await c.post("/acquirers/acquirer_alpah/admin/outage", json={"active": True})
        print("typo outage toggle ->", r.status_code, "registered now:", (await c.get("/acquirers")).json(),
              "| real alpha outage_active:", app.state.registry.get("acquirer_alpha").is_outage_active)
        # in-flight request when outage toggled mid-latency
        t = asyncio.create_task(c.post("/acquirers/acquirer_alpha/authorize", json={"transaction_id": "t1", "amount": 1}))
        await asyncio.sleep(0.05)
        app.state.registry.get("acquirer_alpha").set_outage(True)
        print("request accepted BEFORE outage toggle returned:", (await t).json()["decline_code"])
    a = MultiAcquirerSimulator(["alpha", "beta"], seed=42); b = MultiAcquirerSimulator(["alpha", "beta"], seed=43)
    xa = a.get("beta")._rng.uniform(size=3); xb = b.get("alpha")._rng.uniform(size=3)
    print("seed=42 beta stream == seed=43 alpha stream:", np.allclose(xa, xb))
asyncio.run(main())
```

</details>

<details><summary><code>exp13_value_policy.py</code></summary>

```python
"""With PID on, does the Phase 8 value policy change where the *current* high-value tx goes?
Alternate amount=1 and amount=10000; compare P(dispatch=leader | amount). Mock acquirer: alpha 0.95, beta 0.90."""
import asyncio, json, time, uuid
import httpx, numpy as np
from acquirer_sim.models import AuthorizeRequest, AuthorizeResponse
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig
from router_core.router import BanditRouter
from router_core.value_policy import ValueScaledExplorationConfig
rng = np.random.default_rng(5)
P = {"acquirer_alpha": 0.95, "acquirer_beta": 0.90}
def h(req):
    aid = req.url.path.split("/")[2]; ok = bool(rng.uniform() < P[aid]); b = json.loads(req.content)
    return httpx.Response(200, json=AuthorizeResponse(transaction_id=b["transaction_id"], acquirer_id=aid,
        status="AUTHORIZED" if ok else "DECLINED", authorized=ok, authorization_code="A" if ok else None,
        decline_code=None if ok else "DO_NOT_HONOR", simulated_latency_ms=0, timestamp=time.time()).model_dump())
async def run(pid):
    cfg = RouterConfig(routes=[AcquirerRouteConfig(acquirer_id=a, base_url="http://x") for a in P],
                       pid_config=PIDConfig() if pid else None, seed=11,
                       value_scaled_config=ValueScaledExplorationConfig(enabled=True, tau=100.0))
    r = BanditRouter(config=cfg, http_client=httpx.AsyncClient(transport=httpx.MockTransport(h)))
    hi = lo = hiA = loA = 0
    for i in range(6000):
        amt = 10000.0 if i % 2 else 1.0
        res = await r.route(AuthorizeRequest(transaction_id=f"t{uuid.uuid4().hex}", amount=amt))
        if i < 500: continue
        a = res.selected_acquirer == "acquirer_alpha"
        if amt > 1: hi += 1; hiA += a
        else: lo += 1; loA += a
    print(f"pid={pid}: P(alpha|high)={hiA/hi:.3f}  P(alpha|low)={loA/lo:.3f}")
import logging; logging.disable(logging.CRITICAL)
asyncio.run(run(True)); asyncio.run(run(False))
```

</details>

<details><summary><code>exp14_memory.py</code></summary>

```python
"""Effective memory of per-outcome decay, only the selected arm decays (state.py record_outcome)."""
import math
for g in (0.98, 0.95):
    neff, nhalf = 1 / (1 - g), math.log(0.5) / math.log(g)
    print(f"gamma={g}: N_eff={neff:.0f} obs of THAT arm, half-life={nhalf:.1f} obs")
    for tps in (0.5, 15, 500):
        for share, lab in ((0.94, "leader@94%"), (0.03, "floor@3%")):
            print(f"   {tps:>5} TPS {lab:11s}: N_eff window = {neff/(tps*share):8.2f} s, half-life = {nhalf/(tps*share):8.2f} s")
```

</details>

<details><summary><code>exp1_pid_step.py</code></summary>

```python
"""PID: step response, gain sweep, eigenvalues, integral bounds after centring, one-hot noise."""
import numpy as np
from router_core.pid import PIDConfig, PIDState, calculate_pid_step

ids = ["a", "b", "c"]

def run(cfg, targets, init=None):
    st = PIDState.initialize(ids)
    cur = dict(st.previous_allocation) if init is None else dict(init)
    hist, ihist = [], []
    for tgt in targets:
        r = calculate_pid_step(tgt, cur, st, cfg, dt=1.0)
        cur, st = r.smoothed_allocation, r.next_state
        hist.append([cur[k] for k in ids]); ihist.append([st.accumulated_error[k] for k in ids])
    return np.array(hist), np.array(ihist)

shipped = PIDConfig(kp=0.12, ki=0.005, kd=0.25, min_allocation=0.03)
# 1. constant step target a=0.94 (max reachable given floor) held 300 steps
tgt = {"a": 0.94, "b": 0.03, "c": 0.03}
h, ih = run(shipped, [tgt] * 300)
a = h[:, 0]
print("STEP to a=0.94: t90=", int(np.argmax(a >= 0.333 + 0.9 * (0.94 - 0.333))) + 1,
      "max=", a.max().round(4), "overshoot_above_target=", round(a.max() - 0.94, 4),
      "final=", a[-1].round(5), "max|I|=", np.abs(ih).max().round(3))
print("  first 10 a:", np.round(a[:10], 4))
# 2. one-hot target: step to always-'a' (the actual production target shape)
h1, ih1 = run(shipped, [{"a": 1.0, "b": 0.0, "c": 0.0}] * 300)
print("ONE-HOT always a: t90=", int(np.argmax(h1[:, 0] >= 0.333 + 0.9 * (0.94 - 0.333))) + 1,
      "final=", np.round(h1[-1], 4), "I_final=", np.round(ih1[-1], 3), "max|I|=", np.abs(ih1).max().round(3))
# 3. linearised closed-loop eigenvalues (unconstrained, per-component), velocity form:
# w_{t+1} = w_t + kp(r-w_t) + ki*I_t - kd(w_t - w_{t-1});  I_t = I_{t-1} + (r - w_t)
def eig(kp, ki, kd):
    # state x=[w_t, w_{t-1}, I_{t-1}], r=0
    # I_t = I_{t-1} - w_t ; w_{t+1} = w_t - kp w_t + ki(I_{t-1} - w_t) - kd w_t + kd w_{t-1}
    A = np.array([[1 - kp - ki - kd, kd, ki], [1, 0, 0], [-1, 0, 1]])
    return np.abs(np.linalg.eigvals(A)).max()
print("spectral radius shipped:", round(eig(0.12, 0.005, 0.25), 5))
for kp in [0.05, 0.12, 0.5, 1.0, 1.5]:
    for kd in [0.0, 0.25, 0.5, 0.9]:
        print(f"  kp={kp} kd={kd} ki=0.005 rho={eig(kp, 0.005, kd):.4f}", end="")
    print()
# 4. integral after clamping + centring: can |I| exceed integral_max?
cfg = PIDConfig(kp=0.0, ki=0.0, kd=0.0, integral_max=1.0, min_allocation=0.0)
st = PIDState(accumulated_error={"a": 1.0, "b": 1.0, "c": -1.0}, previous_error={k: 0.0 for k in ids},
              previous_allocation={"a": .9, "b": .05, "c": .05}, filtered_derivative={k: 0.0 for k in ids})
r = calculate_pid_step({"a": 0.0, "b": 0.0, "c": 1.0}, {"a": .9, "b": .05, "c": .05}, st, cfg)
print("I after clamp+centre:", {k: round(v, 4) for k, v in r.next_state.accumulated_error.items()})
# general random search
rng = np.random.default_rng(0)
worst = 0
for _ in range(20000):
    k = rng.integers(2, 6)
    keys = [f"k{i}" for i in range(k)]
    prev = rng.uniform(-1, 1, k); prev -= prev.mean()
    w = rng.dirichlet(np.ones(k)); t = np.eye(k)[rng.integers(k)]
    st = PIDState(accumulated_error=dict(zip(keys, prev)), previous_error=dict(zip(keys, [0.0]*k)),
                  previous_allocation=dict(zip(keys, w)), filtered_derivative=dict(zip(keys, [0.0]*k)))
    r = calculate_pid_step(dict(zip(keys, t)), dict(zip(keys, w)), st, cfg)
    worst = max(worst, max(abs(v) for v in r.next_state.accumulated_error.values()))
print("random search worst |I| after centring (I_max=1):", round(worst, 4))
```

</details>

<details><summary><code>exp2_pid_noise.py</code></summary>

```python
"""PID fed i.i.d. one-hot targets (as router.route does): mean/std of allocation vs Thompson win probs.
Also: does the PID trajectory depend on anything except the target sequence? (it shouldn't if it's a filter)"""
import numpy as np
from router_core.pid import PIDConfig, PIDState, calculate_pid_step

ids = ["a", "b", "c"]
cfg = PIDConfig()  # shipped defaults kp=.12 ki=.005 kd=.25 floor=.03
print("defaults:", cfg.kp, cfg.ki, cfg.kd, cfg.min_allocation, cfg.actuation_mode)
for p in [(0.6, 0.3, 0.1), (0.4, 0.35, 0.25), (0.98, 0.01, 0.01)]:
    rng = np.random.default_rng(1)
    st = PIDState.initialize(ids); cur = dict(st.previous_allocation)
    W = []; maxstep = 0
    for t in range(20000):
        win = ids[rng.choice(3, p=p)]
        r = calculate_pid_step({k: float(k == win) for k in ids}, cur, st, cfg)
        maxstep = max(maxstep, max(abs(r.smoothed_allocation[k] - cur[k]) for k in ids))
        cur, st = r.smoothed_allocation, r.next_state
        W.append([cur[k] for k in ids])
    W = np.array(W[1000:])
    print(f"win probs {p}: mean w={np.round(W.mean(0),3)} std w={np.round(W.std(0),3)} "
          f"max per-tx step={maxstep:.3f}")
```

</details>

<details><summary><code>exp3_simplex_fuzz.py</code></summary>

```python
"""Fuzz project_to_bounded_simplex: sum==1 and floor, N=1..10, adversarial inputs."""
import math
import numpy as np
from router_core.pid import project_to_bounded_simplex

rng = np.random.default_rng(12345)
viol_sum, viol_floor, errors, cases = [], [], [], 0
worst_sum = 0.0
for trial in range(200000):
    n = int(rng.integers(1, 11))
    mode = trial % 6
    if mode == 0:
        w = rng.normal(0, 1, n)
    elif mode == 1:
        w = rng.normal(0, 1e6, n)
    elif mode == 2:
        w = np.full(n, rng.uniform(-1, 2))
    elif mode == 3:
        w = rng.dirichlet(np.ones(n)) + rng.normal(0, 0.2, n)
    elif mode == 4:
        w = -np.abs(rng.normal(0, 10, n))
    else:
        w = rng.uniform(0, 1e-9, n)
    # floor near 1/N
    fmode = trial % 4
    if fmode == 0:
        floor = 0.03
    elif fmode == 1:
        floor = (1.0 / n) * (1 - 10 ** -rng.uniform(1, 12))
    elif fmode == 2:
        floor = 0.0
    else:
        floor = rng.uniform(0, 1.0 / n)
    if n * floor >= 1.0:
        continue
    d = {f"k{i}": float(x) for i, x in enumerate(w)}
    cases += 1
    try:
        out = project_to_bounded_simplex(d, floor)
    except Exception as e:  # noqa
        errors.append((n, floor, type(e).__name__)); continue
    s = sum(out.values())
    worst_sum = max(worst_sum, abs(s - 1))
    if abs(s - 1.0) > 1e-9:
        viol_sum.append((n, floor, list(w), s))
    if min(out.values()) < floor - 1e-12:
        viol_floor.append((n, floor, min(out.values())))
print("cases:", cases, "exceptions:", len(errors), "sum violations(>1e-9):", len(viol_sum),
      "floor violations:", len(viol_floor), "worst |sum-1|:", worst_sum)
for v in viol_sum[:3]:
    print("  SUM CE:", v[0], "floor=", v[1], "in=", [round(x, 6) for x in v[2]], "sum=", v[3])

# targeted: floor-redistribution after normalisation
for inp, fl in [({"a": 1e-300, "b": 1e-300, "c": 0.9999999}, 0.33), ]:
    o = project_to_bounded_simplex(inp, fl); print("targeted", o, sum(o.values()))
# the deficit branch: s<1 path scales clamped values; floor arms ALSO get scaled up => fine.
# excess branch where all at floor but s>1? impossible since k*floor<1.
# NaN / inf
for bad in [{"a": float("nan"), "b": 0.5, "c": 0.5}, {"a": float("inf"), "b": 0.1, "c": 0.1},
            {"a": -float("inf"), "b": 0.5, "c": 0.5}, {"a": float("nan"), "b": float("nan")}]:
    try:
        o = project_to_bounded_simplex(bad, 0.03)
        print("NaN/inf in", bad, "->", o, "sum=", sum(o.values()))
    except Exception as e:  # noqa
        print("NaN/inf in", bad, "-> raised", type(e).__name__, e)
```

</details>

<details><summary><code>exp4_deficit.py</code></summary>

```python
"""Deficit (Bresenham) scheduler as implemented in BanditRouter.route (lines 220-230), replicated exactly."""
import numpy as np

def sched(alloc_seq, ids):
    cum = {a: 0.0 for a in ids}; cnt = {a: 0 for a in ids}; out = []; maxdev = 0.0
    for w in alloc_seq:
        for a in ids:
            cum[a] += w[a]
        sel = max(sorted(ids), key=lambda a: (cum[a] - cnt[a], a))
        cnt[sel] += 1; out.append(sel)
        maxdev = max(maxdev, max(abs(cum[a] - cnt[a]) for a in ids))
    return out, cum, cnt, maxdev

ids = ["a", "b", "c"]
rng = np.random.default_rng(0)
# 1. long run random (Dirichlet) weights changing every step
N = 100000
seq = [dict(zip(ids, rng.dirichlet([2, 1, 1]))) for _ in range(N)]
out, cum, cnt, maxdev = sched(seq, ids)
print("random weights N=1e5: dispatch frac", {a: cnt[a] / N for a in ids},
      "mean alloc", {a: round(cum[a] / N, 5) for a in ids}, "max|V-C|", round(maxdev, 4))
# 2. regime change: 50k steps at (0.94,.03,.03) then 50k at (.03,.94,.03): burst for b?
seq = [{"a": .94, "b": .03, "c": .03}] * 50000 + [{"a": .03, "b": .94, "c": .03}] * 50000
out, cum, cnt, maxdev = sched(seq, ids)
post = out[50000:50050]
print("regime change: first 50 post-switch picks:", "".join(x for x in post), " max|V-C|", round(maxdev, 4))
# 3. float drift: cumulative target grows ~t; check cum sum vs count at large t
cum_total = sum(cum.values()); print("sum cum target", cum_total, "dispatched", sum(cnt.values()),
                                      "drift", cum_total - sum(cnt.values()))
# 4. drift with allocation sums != 1 exactly (e.g., 1+2e-9 from projection) for 1e8 effective steps
print("if sum(w)=1+2e-9 per step, deficit total drifts by", 2e-9 * 1e8, "after 1e8 tx (harmless)")
# 5. PID-like allocation: correlate selected arm with per-tx Thompson winner -> independence
```

</details>

<details><summary><code>exp5_concurrency.py</code></summary>

```python
"""200 concurrent BanditRouter.route() calls against in-process simulator (ASGITransport).
Checks: invariants, in-flight lag during outage, PID steps per decision, failure semantics,
and 422/garbage-200 behaviour (state mutated but no outcome recorded)."""
import asyncio, uuid
import httpx, numpy as np
from acquirer_sim.app import create_app
from acquirer_sim.models import AuthorizeRequest, LatencyConfig, OutageBehavior
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig
from router_core.router import BanditRouter

IDS = ["acquirer_alpha", "acquirer_beta", "acquirer_gamma"]

def mk(mode="stochastic", app=None, seed=7):
    app = app or create_app(default_acquirers=IDS, default_base_rate=0.95,
                            default_latency=LatencyConfig(base_ms=20, jitter_ms=5), seed=42)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://sim")
    cfg = RouterConfig(routes=[AcquirerRouteConfig(acquirer_id=a, base_url="http://sim") for a in IDS],
                       pid_config=PIDConfig(actuation_mode=mode), seed=seed)
    return app, BanditRouter(config=cfg, http_client=client)

def req(amount=100.0):
    return AuthorizeRequest(transaction_id=f"tx_{uuid.uuid4().hex}", amount=amount)

async def main():
    # A. warm up sequentially, then outage alpha, then 200 concurrent
    for mode in ["stochastic", "deficit"]:
        app, r = mk(mode)
        for _ in range(100):
            await r.route(req())
        st0 = r.pid_state.step_count
        app.state.registry.get("acquirer_alpha").set_outage(True, OutageBehavior.HTTP_503)
        alloc_before = r.current_allocation
        res = await asyncio.gather(*[r.route(req()) for _ in range(200)])
        sel = [x.selected_acquirer for x in res]
        alpha_sent = sel.count("acquirer_alpha")
        print(f"[{mode}] alloc before burst={ {k: round(v,3) for k,v in alloc_before.items()} }")
        print(f"[{mode}] 200 concurrent during alpha 503 outage: sent to alpha={alpha_sent} "
              f"(all decided before any outcome); pid steps +{r.pid_state.step_count - st0}; "
              f"sum alloc={sum(r.current_allocation.values()):.12f}; "
              f"alpha beta-param after={r.get_state('acquirer_alpha').beta:.2f}")
        if mode == "deficit":
            print(f"[{mode}] dispatched_count={r._dispatched_count} cum_target="
                  f"{ {k: round(v,2) for k,v in r._cumulative_target.items()} }")
        # sequential comparison: how many go to alpha in 200 sequential during outage
        app2, r2 = mk(mode)
        for _ in range(100):
            await r2.route(req())
        app2.state.registry.get("acquirer_alpha").set_outage(True, OutageBehavior.HTTP_503)
        seq = [ (await r2.route(req())).selected_acquirer for _ in range(200)]
        print(f"[{mode}] 200 SEQUENTIAL during alpha outage: sent to alpha={seq.count('acquirer_alpha')}")

    # B. failure semantics: count DO_NOT_HONOR recorded as acquirer failure
    app, r = mk()
    res = [await r.route(req()) for _ in range(400)]
    dnh = [x for x in res if x.response_payload and x.response_payload.decline_code == "DO_NOT_HONOR"]
    print(f"DO_NOT_HONOR declines: {len(dnh)}; recorded success=False for all: {all(not x.success for x in dnh)}")

    # C. acquirer returns 422 -> ValueError, after PID/deficit state mutated, no outcome recorded
    def h422(request):
        return httpx.Response(422, json={"detail": "bad"})
    def hgarbage(request):
        return httpx.Response(200, content=b"<html>gateway</html>")
    for name, h in [("422", h422), ("200-garbage", hgarbage)]:
        cfg = RouterConfig(routes=[AcquirerRouteConfig(acquirer_id=a, base_url="http://x") for a in IDS],
                           pid_config=PIDConfig(actuation_mode="deficit"), seed=1)
        r = BanditRouter(config=cfg, http_client=httpx.AsyncClient(transport=httpx.MockTransport(h)))
        errs = 0
        for _ in range(50):
            try:
                await r.route(req())
            except Exception as e:  # noqa
                errs += 1; et = type(e).__name__
        tot = sum(s.total_count for s in r.get_all_states().values())
        print(f"acquirer {name}: 50 tx -> exceptions={errs} ({et}); outcomes recorded={tot}; "
              f"pid steps={r.pid_state.step_count}; dispatched={sum(r._dispatched_count.values())}")
    # D. RemoteProtocolError (not NetworkError/Timeout) propagates?
    def hproto(request):
        raise httpx.RemoteProtocolError("server disconnected", request=request)
    cfg = RouterConfig(routes=[AcquirerRouteConfig(acquirer_id=a, base_url="http://x") for a in IDS],
                       pid_config=PIDConfig(), seed=1)
    r = BanditRouter(config=cfg, http_client=httpx.AsyncClient(transport=httpx.MockTransport(hproto)))
    try:
        await r.route(req()); print("RemoteProtocolError: handled")
    except Exception as e:  # noqa
        print("RemoteProtocolError: PROPAGATED as", type(e).__name__, "| outcomes recorded:",
              sum(s.total_count for s in r.get_all_states().values()))
    # E. cancellation mid-request: state mutated, no outcome
    async def slow(request):
        await asyncio.sleep(10); return httpx.Response(200)
    r = BanditRouter(config=cfg, http_client=httpx.AsyncClient(transport=httpx.MockTransport(slow)))
    t = asyncio.create_task(r.route(req())); await asyncio.sleep(0.05); t.cancel()
    try:
        await t
    except asyncio.CancelledError:
        print("cancelled mid-flight: pid steps=", r.pid_state.step_count, "outcomes recorded=",
              sum(s.total_count for s in r.get_all_states().values()))

asyncio.run(main())
```

</details>

<details><summary><code>exp6_ws_block.py</code></summary>

```python
"""Does a stalled WebSocket client (connected, never reads) add latency to / block POST /route?
Real uvicorn on 127.0.0.1:28011; acquirer mocked in-process (instant AUTHORIZED)."""
import asyncio, base64, os, socket, threading, time, uuid
import httpx, uvicorn
from acquirer_sim.models import AuthorizeResponse
from router_core.app import create_router_app
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig
from router_core.router import BanditRouter

def handler(request):
    import json
    body = json.loads(request.content)
    return httpx.Response(200, json=AuthorizeResponse(
        transaction_id=body["transaction_id"], acquirer_id="a", status="AUTHORIZED", authorized=True,
        authorization_code="AUTH_X", simulated_latency_ms=0.0, timestamp=time.time()).model_dump())

cfg = RouterConfig(routes=[AcquirerRouteConfig(acquirer_id=a, base_url="http://x") for a in ["a", "b", "c"]],
                   pid_config=PIDConfig(), seed=1)
router = BanditRouter(config=cfg, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
app = create_router_app(router=router)
server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=28011, log_level="error", ws=os.environ.get("WSIMPL", "auto")))
threading.Thread(target=server.run, daemon=True).start()
time.sleep(1.5)

# raw stalled WS client: handshake, then never read
s = socket.create_connection(("127.0.0.1", 28011))
s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
key = base64.b64encode(os.urandom(16)).decode()
s.sendall((f"GET /ws/telemetry HTTP/1.1\r\nHost: 127.0.0.1:28011\r\nUpgrade: websocket\r\n"
           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
time.sleep(0.3)
print("handshake:", s.recv(200).split(b"\r\n")[0])
# do not read anymore

lat = []
with httpx.Client(timeout=5.0) as c:
    for i in range(3000):
        t0 = time.perf_counter()
        try:
            r = c.post("http://127.0.0.1:28011/route", json={"transaction_id": f"tx_{uuid.uuid4().hex}", "amount": 10})
            lat.append((time.perf_counter() - t0) * 1000)
        except httpx.ReadTimeout:
            print(f"POST /route #{i} TIMED OUT after 5s (blocked by stalled websocket)")
            break
print(f"completed {len(lat)} routes; median {sorted(lat)[len(lat)//2]:.2f}ms; max {max(lat):.1f}ms")
server.should_exit = True
```

</details>

<details><summary><code>exp7_redis_restart.py</code></summary>

```python
"""RedisBanditStateRegistry: (a) router restart against persisted Redis state then route();
(b) two workers sharing Redis: lost updates? (c) sync redis calls per route.
fakeredis only: cannot prove real-Redis WATCH semantics under true multi-process contention."""
import asyncio, threading, time, uuid
import fakeredis, httpx
from acquirer_sim.app import create_app
from acquirer_sim.models import AuthorizeRequest, LatencyConfig
from data_layer.redis_state import RedisBanditStateRegistry
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig
from router_core.router import BanditRouter

IDS = ["acquirer_alpha", "acquirer_beta"]
server = fakeredis.FakeServer()
sim = create_app(default_acquirers=IDS, default_latency=LatencyConfig(base_ms=0, jitter_ms=0), seed=1)
def client(): return httpx.AsyncClient(transport=httpx.ASGITransport(app=sim), base_url="http://sim")
def rcfg(pid=True): return RouterConfig(routes=[AcquirerRouteConfig(acquirer_id=a, base_url="http://sim") for a in IDS],
                          pid_config=PIDConfig() if pid else None, seed=3)
def req(): return AuthorizeRequest(transaction_id=f"tx_{uuid.uuid4().hex}", amount=50)

async def main():
    r1 = BanditRouter(config=rcfg(), http_client=client(),
                      registry=RedisBanditStateRegistry(redis_client=fakeredis.FakeRedis(server=server, decode_responses=True)))
    for _ in range(20):
        await r1.route(req())
    print("worker1 ok; redis acquirers:", r1.registry.list_acquirer_ids())
    for pid in [True, False]:
        reg2 = RedisBanditStateRegistry(redis_client=fakeredis.FakeRedis(server=server, decode_responses=True))
        r2 = BanditRouter(config=rcfg(pid), http_client=client(), registry=reg2)
        print(f"[pid={pid}] restarted worker: locally registered arms={list(reg2._redis_acquirers)}; "
              f"sample_all keys={list(reg2.sample_all())}")
        try:
            res = await r2.route(req())
            print(f"[pid={pid}] restarted worker route OK -> {res.selected_acquirer}")
        except Exception as e:  # noqa
            print(f"[pid={pid}] restarted worker route FAILED: {type(e).__name__}: {e}")

    # (b) lost-update check: two registries, interleaved threads each recording 500 outcomes
    srv = fakeredis.FakeServer()
    regs = [RedisBanditStateRegistry(redis_client=fakeredis.FakeRedis(server=srv, decode_responses=True)) for _ in range(2)]
    regs[0].register_acquirer("x")
    regs[1]._redis_acquirers["x"] = regs[1].register_acquirer.__self__.__class__.__mro__ and None or None
    regs[1] = RedisBanditStateRegistry(redis_client=fakeredis.FakeRedis(server=srv, decode_responses=True))
    regs[1].get_state("x")  # lazy-register
    def work(reg):
        for _ in range(500):
            reg.record_outcome("x", success=True)
    ts = [threading.Thread(target=work, args=(r,)) for r in regs]
    [t.start() for t in ts]; [t.join() for t in ts]
    s = regs[0].get_state("x")
    print(f"2 threads x 500 successes: total_count={s.total_count} success={s.success_count} (expect 1000)")
    # (c) redis round trips per sample_all
    calls = {"n": 0}
    rc = fakeredis.FakeRedis(server=server, decode_responses=True)
    orig = rc.execute_command
    def counting(*a, **k):
        calls["n"] += 1; return orig(*a, **k)
    rc.execute_command = counting
    reg = RedisBanditStateRegistry(redis_client=rc); [reg.get_state(a) for a in IDS]
    calls["n"] = 0; reg.sample_all(); print("redis commands per sample_all (2 arms):", calls["n"])
asyncio.run(main())
```

</details>

<details><summary><code>exp8_pubsub.py</code></summary>

```python
"""(a) AsyncEventSubscriber on events:health drops HealthAlertEvents (validates as RoutingEvent).
(b) app._redis_forwarder_loop exception filter vs redis ConnectionError class hierarchy.
(c) at-most-once: events published while subscriber not yet subscribed / disconnected are lost."""
import asyncio, logging
import fakeredis, redis
from data_layer.redis_pubsub import AsyncEventPublisher, AsyncEventSubscriber

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
print("redis ConnectionError is builtin ConnectionError subclass?",
      issubclass(redis.exceptions.ConnectionError, ConnectionError),
      "| OSError?", issubclass(redis.exceptions.ConnectionError, OSError),
      "| TimeoutError?", issubclass(redis.exceptions.TimeoutError, TimeoutError))

async def main():
    srv = fakeredis.FakeServer()
    r = fakeredis.FakeAsyncRedis(server=srv, decode_responses=True)
    pub = AsyncEventPublisher(redis_client=r)
    # (c) publish before subscribe
    await pub.publish_health_alert("acquirer_alpha", 1.0, 0.0, "CRITICAL")
    sub = AsyncEventSubscriber(redis_client=fakeredis.FakeAsyncRedis(server=srv, decode_responses=True),
                               channels=["events:routing", "events:health"])
    await sub._ensure_subscribed()
    n = await pub.publish_health_alert("acquirer_alpha", 1.0, 0.0, "CRITICAL")
    ev = await sub.get_event(timeout=0.5)
    print("health alert received via AsyncEventSubscriber.get_event ->", ev)

    # (b) live forwarder against a dead Redis port
    from data_layer.config import DataLayerConfig
    try:
        cfg = DataLayerConfig(redis_host="127.0.0.1", redis_port=28099, redis_timeout_sec=0.5)
    except Exception as e:  # noqa
        print("cfg err", e); return
    s2 = AsyncEventSubscriber(config=cfg, channels=["events:routing"])
    try:
        async with s2:
            pass
    except (ConnectionError, OSError, TimeoutError) as exc:
        print("forwarder-style except CAUGHT:", type(exc).__mro__[:3])
    except Exception as exc:  # noqa
        print("forwarder-style except MISSED:", type(exc).__module__, type(exc).__name__)
asyncio.run(main())
```

</details>

<details><summary><code>exp9_lifespan.py</code></summary>

```python
"""Router app lifespan with Redis down (no Redis on this host at localhost:6379):
does the forwarder task crash, and does shutdown re-raise, skipping router.close()?"""
import asyncio, warnings
warnings.simplefilter("ignore")
from router_core.app import create_router_app
from router_core.models import AcquirerRouteConfig, RouterConfig

async def main():
    app = create_router_app(config=RouterConfig(routes=[AcquirerRouteConfig(acquirer_id="a", base_url="http://127.0.0.1:28098")]))
    router = app.state.router
    closed = {"v": False}
    orig = router.close
    async def spy():
        closed["v"] = True; await orig()
    router.close = spy
    cm = app.router.lifespan_context(app)
    await cm.__aenter__()
    await asyncio.sleep(55.0)   # let forwarder try to connect
    try:
        await cm.__aexit__(None, None, None)
        print("shutdown clean; router.close called:", closed["v"])
    except BaseException as e:  # noqa
        print("shutdown RAISED", type(e).__module__, type(e).__name__, "| router.close called:", closed["v"])
asyncio.run(main())
```

</details>

<details><summary><code>exp9b_forwarder.py</code></summary>

```python
"""Time how long the app's Redis forwarder takes to fail against a refused port, and what it raises."""
import asyncio, time, warnings
warnings.simplefilter("ignore")
from data_layer.redis_pubsub import AsyncEventSubscriber
async def main():
    t0 = time.perf_counter()
    try:
        async with AsyncEventSubscriber(channels=["events:routing", "events:health"]) as sub:
            async for ev in sub.listen():
                pass
    except (ConnectionError, OSError, TimeoutError) as exc:
        print("caught by app filter", type(exc))
    except Exception as exc:  # noqa
        print(f"escaped app filter after {time.perf_counter()-t0:.1f}s:", type(exc).__module__, type(exc).__name__)
asyncio.run(main())
```

</details>


#### Lens C, claim verification (`loom-audit-C/_audit/`)

<details><summary><code>enqueue_bench.py</code></summary>

```python
"""Measure MetricsLogger.log_routing_result enqueue cost and route() overhead with/without logger."""
import asyncio, os, time, statistics as st, httpx
from acquirer_sim.app import create_app
from acquirer_sim.models import AuthorizeRequest, LatencyConfig
from data_layer.sqlite_logger import MetricsLogger
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig
from router_core.router import BanditRouter
import logging; logging.disable(logging.CRITICAL)
DB = "_audit/enq_bench.db"
def pct(xs, p): s = sorted(xs); return s[min(len(s)-1, int(len(s)*p))]
async def main():
    for f in (DB, DB+"-wal", DB+"-shm"):
        if os.path.exists(f): os.remove(f)
    sim = create_app(default_latency=LatencyConfig(base_ms=0.0, jitter_ms=0.0), seed=1)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=sim), base_url="http://t") as c:
        cfg = RouterConfig(routes=[AcquirerRouteConfig(acquirer_id=a, base_url="http://t") for a in ("acquirer_alpha","acquirer_beta","acquirer_gamma")], pid_config=PIDConfig(), seed=1)
        ml = MetricsLogger(db_path=DB, max_queue_size=100000); await ml.start()
        r0 = BanditRouter(config=cfg, http_client=c)
        res = await r0.route(AuthorizeRequest(transaction_id="warm", amount=10.0))
        # 1) raw enqueue cost
        ns = []
        for i in range(20000):
            t = time.perf_counter_ns(); ml.log_routing_result(res); ns.append(time.perf_counter_ns() - t)
        print(f"enqueue put_nowait: mean={st.mean(ns)/1000:.3f}us p50={pct(ns,.5)/1000:.3f}us p99={pct(ns,.99)/1000:.3f}us max={max(ns)/1000:.1f}us (n=20000)")
        await asyncio.sleep(1.0)
        # 2) route() total latency with vs without logger (interleaved blocks)
        rA = BanditRouter(config=cfg, http_client=c)
        await ml.close()
        for f in (DB, DB+"-wal", DB+"-shm"):
            if os.path.exists(f): os.remove(f)
        ml = MetricsLogger(db_path=DB, max_queue_size=100000); await ml.start()
        rB = BanditRouter(config=cfg, http_client=c, metrics_logger=ml)
        la, lb = [], []
        for blk in range(10):
            for r, L in ((rA, la), (rB, lb)):
                for i in range(200):
                    t = time.perf_counter_ns(); await r.route(AuthorizeRequest(transaction_id=f"b{blk}_{i}_{id(r)}", amount=10.0)); L.append(time.perf_counter_ns() - t)
        print(f"route() no-logger : mean={st.mean(la)/1000:.1f}us p50={pct(la,.5)/1000:.1f}us")
        print(f"route() w/ logger : mean={st.mean(lb)/1000:.1f}us p50={pct(lb,.5)/1000:.1f}us  diff(mean)={(st.mean(lb)-st.mean(la))/1000:.1f}us")
        await ml.close(); print("total_written", ml.total_written)
asyncio.run(main())
```

</details>

<details><summary><code>live_config.py</code></summary>

```python
"""Audit: live/demo default configuration (router_core.server defaults, sim create_app() defaults).

3 acquirers all at 95% (sim default), decay 0.98, PIDConfig() default (stochastic actuation),
in-process ASGI transport with zero latency. Schedule: 300 warmup -> 300 outage on alpha ->
300 recovery. Reports per-transaction allocation jumps, drain time, recovery.
"""

from __future__ import annotations

import asyncio
import statistics as st
import sys

import httpx

from acquirer_sim.app import create_app
from acquirer_sim.models import AuthorizeRequest, LatencyConfig, OutageToggleRequest
from router_core.server import build_router_config, parse_args
from router_core.router import BanditRouter

A = "acquirer_alpha"
N = 300
TPS = 15.0


async def one(sim_seed: int, loom_seed: int) -> dict:
    sim = create_app(default_latency=LatencyConfig(base_ms=0.0, jitter_ms=0.0), seed=sim_seed)
    cfg = build_router_config(parse_args([]))  # exactly the server CLI defaults
    cfg = cfg.model_copy(update={"seed": loom_seed})
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=sim),
                                 base_url="http://127.0.0.1:8001") as c:
        r = BanditRouter(config=cfg, http_client=c)
        W, sel, auth = [], [], []
        for stage in range(3):
            if stage in (1, 2):
                await c.post(f"/acquirers/{A}/admin/outage",
                             json=OutageToggleRequest(active=(stage == 1)).model_dump())
            for i in range(N):
                res = await r.route(AuthorizeRequest(transaction_id=f"x{stage}_{i}", amount=50.0))
                W.append(dict(res.smoothed_allocation))
                sel.append(res.selected_acquirer)
                auth.append(res.authorized)
    keys = list(W[0])
    dmax = max(max(abs(W[k][a] - W[k - 1][a]) for a in keys) for k in range(1, len(W)))
    # alpha weight at outage start, steps until alpha weight < 0.05
    w0 = W[N - 1][A]
    drain = next((k - N + 1 for k in range(N, 2 * N) if W[k][A] < 0.05), None)
    first_alpha_rec = [k for k in range(2 * N, 3 * N) if sel[k] == A]
    return {
        "dmax": dmax, "wA_at_outage": w0, "drain_tx": drain,
        "fail_on_alpha_in_outage": sum(1 for k in range(N, 2 * N) if sel[k] == A),
        "rec_alpha_share": len(first_alpha_rec) / N,
        "wA_end": W[-1][A],
        "warm_share_A": sum(1 for k in range(N) if sel[k] == A) / N,
    }


async def main() -> None:
    rows = [await one(1000 + 10 * k, 5000 + k) for k in range(int(sys.argv[1]) if len(sys.argv) > 1 else 20)]
    for key in rows[0]:
        xs = [r[key] for r in rows if r[key] is not None]
        nn = sum(1 for r in rows if r[key] is None)
        print(f"{key:<26} mean={st.mean(xs):.4f} min={min(xs):.4f} max={max(xs):.4f} n={len(xs)} none={nn}")
    xs = [r["drain_tx"] for r in rows if r["drain_tx"] is not None]
    print(f"drain seconds at {TPS} TPS: mean={st.mean(xs) / TPS:.2f}s max={max(xs) / TPS:.2f}s")


if __name__ == "__main__":
    asyncio.run(main())
```

</details>

<details><summary><code>multiseed.py</code></summary>

```python
"""Multi-seed re-run of scripts/compare_psr.py scenario (audit lens C).

Same scenario/configuration as compare_psr.execute_scenario, but:
  * simulator seed and Loom router seed are parameters,
  * no SQLite (PSR computed from RoutingResult list; verified equal to the SQL
    numbers at seed 42 by the --check run),
  * extra metrics (dispatch-indicator jump, w_A monotonicity during outage).
Sim seeds are spaced by 10 because MultiAcquirerSimulator gives acquirer i the
stream default_rng(seed + i); adjacent seeds would share streams.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics as st
import sys
from typing import Any

import httpx
from scipy import stats as sps

from acquirer_sim.app import create_app
from acquirer_sim.models import AuthorizeRequest, LatencyConfig, OutageToggleRequest
from baseline_router.models import BaselineRouterConfig, FailoverPolicyConfig
from baseline_router.router import StaticBaselineRouter
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig

A, B = "acquirer_alpha", "acquirer_beta"


async def run(kind: str, sim_seed: int, loom_seed: int = 777, m: int = 3,
              actuation: str = "deficit", decay: float = 0.95, gray: float | None = None,
              pid: bool = True) -> dict[str, Any]:
    sim_app = create_app(default_acquirers=[A, B], default_base_rate=0.95,
                         default_latency=LatencyConfig(base_ms=0.0, jitter_ms=0.0), seed=sim_seed)
    sim_app.state.registry.get(A).set_success_rate(0.95)
    sim_app.state.registry.get(B).set_success_rate(0.94)
    transport = httpx.ASGITransport(app=sim_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        routes = [AcquirerRouteConfig(acquirer_id=a, base_url="http://testserver",
                                      state_config=AcquirerStateConfig(decay_factor=decay))
                  for a in (A, B)]
        if kind == "baseline":
            router: Any = StaticBaselineRouter(
                config=BaselineRouterConfig(
                    routes=routes, priority_order=[A, B],
                    failover_policy=FailoverPolicyConfig(consecutive_failure_threshold=m,
                                                         cooldown_transactions=30,
                                                         failback_mode="probe")),
                http_client=client)
        else:
            pc = (PIDConfig(kp=0.12, ki=0.005, kd=0.25, integral_max=1.0, min_allocation=0.03,
                            actuation_mode=actuation) if pid else None)
            router = BanditRouter(config=RouterConfig(routes=routes, pid_config=pc, seed=loom_seed),
                                  http_client=client)
        res, w = [], []
        for stage in range(3):
            if stage == 1:
                if gray is None:
                    await client.post(f"/acquirers/{A}/admin/outage",
                                      json=OutageToggleRequest(active=True).model_dump())
                else:
                    sim_app.state.registry.get(A).set_success_rate(gray)
            if stage == 2:
                if gray is None:
                    await client.post(f"/acquirers/{A}/admin/outage",
                                      json=OutageToggleRequest(active=False).model_dump())
                else:
                    sim_app.state.registry.get(A).set_success_rate(0.95)
            for i in range(50):
                r = await router.route(AuthorizeRequest(transaction_id=f"t{stage}_{i}", amount=50.0))
                res.append(r)
                w.append(r.smoothed_allocation[A] if r.smoothed_allocation
                         else (1.0 if r.selected_acquirer == A else 0.0))
    sel = [r.selected_acquirer for r in res]
    auth = [r.authorized for r in res]
    ind = [1.0 if s == A else 0.0 for s in sel]
    dw = max(abs(w[k] - w[k - 1]) for k in range(1, 150))
    dind = max(abs(ind[k] - ind[k - 1]) for k in range(1, 150))
    o = sel[50:100]
    w_out = w[50:100]
    return {
        "psr": sum(auth) / 150, "auth": sum(auth),
        "warm": sum(auth[:50]) / 50, "out": sum(auth[50:100]) / 50, "rec": sum(auth[100:]) / 50,
        "fail_alpha": sum(1 for k in range(50, 100) if sel[k] == A and not auth[k]),
        "dw_max": dw, "dispatch_jump": dind,
        "outage_flips": sum(1 for k in range(1, 50) if o[k] != o[k - 1]),
        "rec_alpha_tx": sum(1 for k in range(100, 150) if sel[k] == A),
        "wA_end": w[-1],
        # w_A rises toward the dead acquirer during outage (Tx 51..100)
        "wA_rises_in_outage": sum(1 for k in range(1, 50) if w_out[k] > w_out[k - 1] + 1e-12),
        "wA_peak_after_outage_minus_at_tx50": max(w_out) - w[49],
    }


def summ(xs: list[float]) -> str:
    n = len(xs)
    m = st.mean(xs)
    sd = st.stdev(xs) if n > 1 else 0.0
    h = sps.t.ppf(0.975, n - 1) * sd / n ** 0.5 if n > 1 else 0.0
    return (f"mean={m:8.4f} sd={sd:7.4f} 95%CI=[{m - h:8.4f},{m + h:8.4f}] "
            f"min={min(xs):8.4f} max={max(xs):8.4f} n={n}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=100)
    ap.add_argument("--mode", choices=["paired", "simonly", "loomonly", "check", "extra"],
                    default="paired")
    a = ap.parse_args()
    if a.mode == "check":
        for _ in range(2):
            b3 = await run("baseline", 42, m=3)
            b1 = await run("baseline", 42, m=1)
            lo = await run("loom", 42, 777)
            print(json.dumps({"b3": b3, "b1": b1, "loom": lo}, default=float))
        return
    rows = []
    for k in range(a.n):
        sim_seed = 42 + 10 * k if a.mode in ("paired", "simonly", "extra") else 42
        loom_seed = 777 + k if a.mode in ("paired", "loomonly", "extra") else 777
        r = {"sim_seed": sim_seed, "loom_seed": loom_seed}
        if a.mode != "loomonly" or k == 0:
            r["b3"] = await run("baseline", sim_seed, m=3)
            r["b1"] = await run("baseline", sim_seed, m=1)
        else:
            r["b3"], r["b1"] = rows[0]["b3"], rows[0]["b1"]
        r["loom"] = await run("loom", sim_seed, loom_seed)
        if a.mode == "extra":
            r["loom_stoch"] = await run("loom", sim_seed, loom_seed, actuation="stochastic")
            r["raw"] = await run("loom", sim_seed, loom_seed, pid=False)
            r["b3_gray"] = await run("baseline", sim_seed, m=3, gray=0.60)
            r["loom_gray"] = await run("loom", sim_seed, loom_seed, gray=0.60)
        rows.append(r)
    keys = [k for k in rows[0] if isinstance(rows[0][k], dict)]
    print(f"MODE={a.mode} N={a.n}")
    for cfg in keys:
        print(f"--- {cfg}")
        for met in ("psr", "warm", "out", "rec", "fail_alpha", "dw_max", "dispatch_jump",
                    "outage_flips", "rec_alpha_tx", "wA_rises_in_outage",
                    "wA_peak_after_outage_minus_at_tx50"):
            print(f"  {met:<36} {summ([r[cfg][met] for r in rows])}")
    for base in ("b3", "b1"):
        d = [r["loom"]["psr"] - r[base]["psr"] for r in rows]
        wins = sum(1 for x in d if x > 1e-12)
        ties = sum(1 for x in d if abs(x) <= 1e-12)
        print(f"PAIRED loom-{base} psr diff: {summ(d)} wins/ties/losses={wins}/{ties}/{len(d) - wins - ties}")
    if a.mode == "extra":
        for x, y in (("loom_gray", "b3_gray"), ("loom_stoch", "b3"), ("raw", "b3"), ("loom", "raw")):
            d = [r[x]["psr"] - r[y]["psr"] for r in rows]
            wins = sum(1 for v in d if v > 1e-12)
            ties = sum(1 for v in d if abs(v) <= 1e-12)
            print(f"PAIRED {x}-{y} psr diff: {summ(d)} wins/ties/losses={wins}/{ties}/{len(d) - wins - ties}")
    # rank of seed-42 Loom result
    l42 = 0.86
    below = sum(1 for r in rows if r["loom"]["psr"] < l42 - 1e-12)
    print(f"Loom PSR < 0.86 in {below}/{len(rows)} seeds")
    json.dump(rows, open(f"_audit/multiseed_{a.mode}.json", "w"), default=float)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

</details>

<details><summary><code>phase7_inproc.py</code></summary>

```python
"""Phase 7 QA script's routing config, in-process (no TCP/WS), to compute the README Phase-7 row columns."""
import asyncio, logging, httpx
from acquirer_sim.app import create_app
from acquirer_sim.models import AuthorizeRequest, LatencyConfig, OutageBehavior
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig
logging.disable(logging.CRITICAL)
IDS = ["acquirer_alpha", "acquirer_beta", "acquirer_gamma"]
async def main():
    sim = create_app(default_acquirers=IDS, default_base_rate=0.95, default_latency=LatencyConfig(base_ms=0.0, jitter_ms=0.0), seed=42)
    for a, p in zip(IDS, (0.95, 0.90, 0.85)): sim.state.registry.get(a).set_success_rate(p)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=sim), base_url="http://mock-sim:8001") as c:
        r = BanditRouter(config=RouterConfig(routes=[AcquirerRouteConfig(acquirer_id=a, base_url="http://127.0.0.1:8001", state_config=AcquirerStateConfig(alpha_prior=1.0, beta_prior=1.0, decay_factor=0.95)) for a in IDS], seed=777, pid_config=PIDConfig(kp=0.12, ki=0.005, kd=0.25, integral_max=1.0, min_allocation=0.03)), http_client=c)
        res = []
        for seq in range(1, 151):
            if seq == 51: sim.state.registry.get("acquirer_alpha").set_outage(active=True, behavior=OutageBehavior.RETURN_DECLINE)
            if seq == 101: sim.state.registry.get("acquirer_alpha").set_outage(active=False, behavior=OutageBehavior.RETURN_DECLINE)
            res.append(await r.route(AuthorizeRequest(transaction_id=f"tx_gauntlet_{seq:03d}", amount=50.0)))
    au = [x.authorized for x in res]; sel = [x.selected_acquirer for x in res]; W = [x.smoothed_allocation for x in res]
    o = sel[50:100]
    print("warm/out/rec PSR:", sum(au[:50])*2, sum(au[50:100])*2, sum(au[100:])*2, " auth:", sum(au), "/150 =", round(sum(au)/1.5, 2), "%")
    print("fail_alpha_outage:", sum(1 for k in range(50,100) if sel[k]=="acquirer_alpha" and not au[k]), " outage_flips:", sum(1 for k in range(1,50) if o[k]!=o[k-1]))
    print("dw alpha-only max: %.2f%%  dw any-arm max: %.2f%%" % (100*max(abs(W[k]["acquirer_alpha"]-W[k-1]["acquirer_alpha"]) for k in range(1,150)), 100*max(max(abs(W[k][a]-W[k-1][a]) for a in IDS) for k in range(1,150))))
    print("recovery tx to alpha:", sum(1 for k in range(100,150) if sel[k]=="acquirer_alpha"))
asyncio.run(main())
```

</details>

<details><summary><code>phase7_port8000.py</code></summary>

```python
"""QA Live Verification Script: Phase 7 Dashboard End-to-End Live Audit.

Uses a real TCP localhost server (uvicorn) and native WebSocket client (websockets)
to audit the live dashboard backend and data contracts under real network conditions.

Audits:
1. Outage Marker Alignment: Does chart outage marker line up with outage trigger?
2. Real-Time Cadence: Do health readouts and PSR numbers update at Phase 5 cadence?
3. Operator Deck Responsiveness: Do buttons cause visible state change in time?
4. Resilient Disconnect Handling: Does killing WebSocket produce reconnecting state?
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from typing import Any

import httpx
import uvicorn
import websockets

from acquirer_sim.app import create_app
from acquirer_sim.models import LatencyConfig, OutageBehavior
from router_core.app import create_router_app
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig

SERVER_HOST = "127.0.0.1"
SERVER_PORT = 8000
HTTP_URL = f"http://{SERVER_HOST}:{SERVER_PORT}"
WS_URL = f"ws://{SERVER_HOST}:{SERVER_PORT}/ws/telemetry"


def build_system() -> tuple[Any, Any]:
    """Instantiate simulated acquirers, PID bandit router, and FastAPI app."""
    sim_app = create_app(
        default_acquirers=["acquirer_alpha", "acquirer_beta", "acquirer_gamma"],
        default_base_rate=0.95,
        default_latency=LatencyConfig(base_ms=0.0, jitter_ms=0.0),
        seed=42,
    )
    sim_app.state.registry.get("acquirer_alpha").set_success_rate(0.95)
    sim_app.state.registry.get("acquirer_beta").set_success_rate(0.90)
    sim_app.state.registry.get("acquirer_gamma").set_success_rate(0.85)

    transport = httpx.ASGITransport(app=sim_app)
    http_client = httpx.AsyncClient(transport=transport, base_url="http://mock-sim:8001")

    pid_config = PIDConfig(
        kp=0.12,
        ki=0.005,
        kd=0.25,
        integral_max=1.0,
        min_allocation=0.03,
    )
    router_config = RouterConfig(
        routes=[
            AcquirerRouteConfig(
                acquirer_id="acquirer_alpha",
                base_url="http://127.0.0.1:8001",
                state_config=AcquirerStateConfig(
                    alpha_prior=1.0, beta_prior=1.0, decay_factor=0.95
                ),
            ),
            AcquirerRouteConfig(
                acquirer_id="acquirer_beta",
                base_url="http://127.0.0.1:8001",
                state_config=AcquirerStateConfig(
                    alpha_prior=1.0, beta_prior=1.0, decay_factor=0.95
                ),
            ),
            AcquirerRouteConfig(
                acquirer_id="acquirer_gamma",
                base_url="http://127.0.0.1:8001",
                state_config=AcquirerStateConfig(
                    alpha_prior=1.0, beta_prior=1.0, decay_factor=0.95
                ),
            ),
        ],
        seed=777,
        pid_config=pid_config,
    )

    router = BanditRouter(config=router_config, http_client=http_client)
    app = create_router_app(router=router)
    return app, sim_app


async def run_live_qa() -> int:
    """Execute live QA verification against real TCP server."""
    print("=" * 80)
    print(" LOOM PHASE 7 LIVE DASHBOARD END-TO-END QA AUDIT (REAL TCP Sockets)")
    print(" Verifying Tickets A, B, C, D Against Phase 3/4 150-Tx Outage Gauntlet")
    print("=" * 80)

    app, sim_app = build_system()

    # 1. Start real simulator server on 127.0.0.1:8001
    sim_config = uvicorn.Config(sim_app, host=SERVER_HOST, port=8001, log_level="warning")
    sim_server = uvicorn.Server(sim_config)
    sim_thread = threading.Thread(target=sim_server.run, daemon=True)
    sim_thread.start()

    # 2. Start real router server on 127.0.0.1:8765
    server_config = uvicorn.Config(app, host=SERVER_HOST, port=SERVER_PORT, log_level="warning")
    server = uvicorn.Server(server_config)
    server_thread = threading.Thread(target=server.run, daemon=True)
    server_thread.start()

    # Wait for both ports to be active
    async with httpx.AsyncClient() as client:
        connected = False
        for _ in range(30):
            try:
                r1 = await client.get(f"http://{SERVER_HOST}:8001/health", timeout=0.5)
                r2 = await client.get(f"{HTTP_URL}/health", timeout=0.5)
                if r1.status_code == 200 and r2.status_code == 200:
                    connected = True
                    break
            except (httpx.HTTPError, OSError):
                await asyncio.sleep(0.1)

    if not connected:
        print("[FAIL] Servers failed to start within 3 seconds.")
        return 1

    print(f"[INIT] Real TCP Simulator running on http://{SERVER_HOST}:8001")
    print(f"[INIT] Real TCP Router running on {HTTP_URL}")

    try:
        # Connect WebSocket client over real TCP
        async with websockets.connect(WS_URL) as ws:
            # 2. Read initial cold-start bootstrap frame
            raw_bootstrap = await asyncio.wait_for(ws.recv(), timeout=2.0)
            bootstrap = json.loads(raw_bootstrap)
            assert bootstrap["event_type"] == "BOOTSTRAP"
            acq_keys = list(bootstrap["states"].keys())
            print(f"[INIT] WebSocket connected over TCP. Received BOOTSTRAP: {acq_keys}")

            # ------------------------------------------------------------------
            # AUDIT 1 & 2: 150-Transaction Outage Gauntlet & Cadence Tracking
            # ------------------------------------------------------------------
            print("\n--- RUNNING 150-TRANSACTION OUTAGE GAUNTLET ---")
            received_events: list[dict[str, Any]] = []
            latencies_ms: list[float] = []

            outage_triggered_at_tx = 51
            outage_cleared_at_tx = 101

            t_start_gauntlet = time.perf_counter()

            async with httpx.AsyncClient(base_url=HTTP_URL, timeout=5.0) as http_client:
                for seq in range(1, 151):
                    # Check if we should trigger outage on Alpha at Tx 51
                    if seq == outage_triggered_at_tx:
                        print(f"\n>>> [TX #{seq}] INJECTING OUTAGE ON ALPHA...")
                        t0 = time.perf_counter()
                        sim_alpha = sim_app.state.registry.get("acquirer_alpha")
                        sim_alpha.set_outage(active=True, behavior=OutageBehavior.RETURN_DECLINE)
                        trigger_rtt = (time.perf_counter() - t0) * 1000
                        print(f"    Outage engaged on Alpha in {trigger_rtt:.2f}ms")

                    # Check if we should clear outage at Tx 101
                    if seq == outage_cleared_at_tx:
                        print(f"\n>>> [TX #{seq}] CLEARING OUTAGE ON ALPHA...")
                        t0 = time.perf_counter()
                        sim_alpha = sim_app.state.registry.get("acquirer_alpha")
                        sim_alpha.set_outage(active=False, behavior=OutageBehavior.RETURN_DECLINE)
                        clear_rtt = (time.perf_counter() - t0) * 1000
                        print(f"    Outage cleared on Alpha in {clear_rtt:.2f}ms")

                    # Dispatch transaction over HTTP
                    t_dispatch = time.perf_counter()
                    req_p = {
                        "transaction_id": f"tx_gauntlet_{seq:03d}",
                        "amount": 50.0,
                    }
                    resp = await http_client.post("/route", json=req_p)
                    assert resp.status_code == 200, f"Route returned {resp.status_code}"

                    # Receive corresponding WebSocket frame
                    raw_frame = await asyncio.wait_for(ws.recv(), timeout=2.0)
                    t_recv = time.perf_counter()

                    delta_ms = (t_recv - t_dispatch) * 1000
                    latencies_ms.append(delta_ms)

                    ws_frame = json.loads(raw_frame)
                    received_events.append(ws_frame)

            total_gauntlet_sec = time.perf_counter() - t_start_gauntlet
            tps = 150 / total_gauntlet_sec
            print(f"\n[DONE] 150 txs in {total_gauntlet_sec:.2f}s ({tps:.1f} TPS)")
            print(f"       Total WebSocket frames captured: {len(received_events)}")

            # ------------------------------------------------------------------
            # QUESTION 1: Outage Marker Alignment Verification
            # ------------------------------------------------------------------
            print("\n" + "=" * 80)
            print(" QUESTION 1 AUDIT: CHART OUTAGE MARKER ALIGNMENT")
            print("=" * 80)

            first_outage_frame = next(
                (ev for ev in received_events if ev.get("decline_code") == "ACQUIRER_OUTAGE"),
                None,
            )
            assert first_outage_frame is not None, "No ACQUIRER_OUTAGE frame!"
            marker_seq = first_outage_frame["sequence_number"]
            marker_tx_id = first_outage_frame["transaction_id"]
            marker_acquirer = first_outage_frame["selected_acquirer"]

            print(f"  Outage Trigger Injected At: Tx #{outage_triggered_at_tx}")
            print(f"  First Outage Event Captured: Seq #{marker_seq} ({marker_tx_id})")
            print(f"  Target Acquirer Marked: {marker_acquirer}")

            event_idx = next(
                i for i, ev in enumerate(received_events) if ev["sequence_number"] == marker_seq
            )
            w_pre = received_events[event_idx - 1]["smoothed_allocation"]["acquirer_alpha"]
            w_post = received_events[event_idx + 10]["smoothed_allocation"]["acquirer_alpha"]

            # Calculate Peak Step Delta (Peak Jump Metric) across all 150 transactions
            step_deltas: list[float] = []
            for i in range(1, len(received_events)):
                prev_alloc = received_events[i - 1]["smoothed_allocation"]
                curr_alloc = received_events[i]["smoothed_allocation"]
                delta = max(abs(curr_alloc[aid] - prev_alloc[aid]) for aid in curr_alloc)
                step_deltas.append(delta * 100.0)
            peak_jump_pct = max(step_deltas) if step_deltas else 0.0

            # Sample Alpha allocation curve from Tx 48 to Tx 62
            curve_samples = []
            for i in range(47, min(62, len(received_events))):
                seq_num = received_events[i]["sequence_number"]
                alpha_pct = received_events[i]["smoothed_allocation"]["acquirer_alpha"] * 100
                curve_samples.append(f"Tx #{seq_num}={alpha_pct:.1f}%")

            print(
                f"  Live Peak Single-Step Jump (Peak Jump): {peak_jump_pct:.2f}% "
                f"(vs 100.0% Static Baseline Cliff)"
            )
            print(f"  Alpha Outage Easing Curve (Tx 48-62):\n    {', '.join(curve_samples)}")

            q1_pass = (
                (marker_seq == outage_triggered_at_tx)
                and (w_post < w_pre)
                and (peak_jump_pct < 15.0)
            )
            res1_str = (
                f"PASSED [COINCIDENT ALIGNMENT VERIFIED, PEAK JUMP {peak_jump_pct:.2f}% < 15% SPEC]"
                if q1_pass
                else "FAILED"
            )
            print(f"  -> Q1 RESULT: {res1_str}")

            # ------------------------------------------------------------------
            # QUESTION 2: Real Cadence Telemetry Delivery Verification
            # ------------------------------------------------------------------
            print("\n" + "=" * 80)
            print(" QUESTION 2 AUDIT: TELEMETRY DELIVERY CADENCE & PSR CALCULATION")
            print("=" * 80)

            avg_latency = sum(latencies_ms) / len(latencies_ms)
            sorted_lat = sorted(latencies_ms)
            p50_lat = sorted_lat[int(len(sorted_lat) * 0.50)]
            p95_lat = sorted_lat[int(len(sorted_lat) * 0.95)]
            p99_lat = sorted_lat[int(len(sorted_lat) * 0.99)]

            print("  WebSocket Frame Delivery Latencies across 150 Transactions:")
            print(f"    - Mean Delivery Latency: {avg_latency:.3f} ms")
            print(f"    - Median (p50): {p50_lat:.3f} ms")
            print(f"    - 95th Percentile (p95): {p95_lat:.3f} ms")
            print(f"    - 99th Percentile (p99): {p99_lat:.3f} ms")

            n50 = sum(1 for e in received_events[0:50] if e["authorized"])
            n100 = sum(1 for e in received_events[50:100] if e["authorized"])
            n150 = sum(1 for e in received_events[100:150] if e["authorized"])
            r_psr_50 = (n50 / 50.0) * 100.0
            r_psr_100 = (n100 / 50.0) * 100.0
            r_psr_150 = (n150 / 50.0) * 100.0
            tot_auth = sum(1 for e in received_events if e["authorized"])
            lifetime_psr = (tot_auth / len(received_events)) * 100.0

            print("\n  Dynamic PSR Readouts Across Lifecycle Stages:")
            print(f"    - Warmup Stage (Tx 1-50) Rolling PSR: {r_psr_50:.1f}%")
            print(f"    - Outage Stage (Tx 51-100) Rolling PSR: {r_psr_100:.1f}%")
            print(f"    - Recovery Stage (Tx 101-150) Rolling PSR: {r_psr_150:.1f}%")
            print(f"    - Overall Lifetime PSR: {lifetime_psr:.1f}%")

            q2_pass = (avg_latency < 15.0) and (len(received_events) == 150) and (r_psr_50 >= 85.0)
            res2_str = "PASSED [REAL CADENCE 1:1 CONFIRMED]" if q2_pass else "FAILED"
            print(f"  -> Q2 RESULT: {res2_str}")

            # ------------------------------------------------------------------
            # QUESTION 3: Operator Outage-Trigger Buttons Responsiveness
            # ------------------------------------------------------------------
            print("\n" + "=" * 80)
            print(" QUESTION 3 AUDIT: OPERATOR OUTAGE TRIGGER BUTTON RESPONSIVENESS")
            print("=" * 80)

            async with httpx.AsyncClient(base_url=HTTP_URL) as http_client:
                t_btn_start = time.perf_counter()
                btn_resp = await http_client.post(
                    "/api/simulator/acquirers/acquirer_beta/outage",
                    json={
                        "active": True,
                        "behavior": "HTTP_503",
                        "transition_seconds": 0.0,
                    },
                )
                btn_rtt_ms = (time.perf_counter() - t_btn_start) * 1000
                assert btn_resp.status_code == 200
                resp_data = btn_resp.json()

                # Verify HEALTH_ALERT was pushed to WebSocket
                raw_alert = await asyncio.wait_for(ws.recv(), timeout=2.0)
                alert_event = json.loads(raw_alert)

                print("  Trigger: POST /api/simulator/acquirers/acquirer_beta/outage")
                print(f"  HTTP Round-Trip Time: {btn_rtt_ms:.2f} ms (< 100ms budget)")
                print(f"  Proxy Response Status: {btn_resp.status_code} OK")
                print(f"  Outage State: active={resp_data.get('outage_active')}")
                print(f"  WebSocket Health Alert: {alert_event.get('event_type')}")
                print(f"    - Acquirer: {alert_event.get('acquirer_id')}")
                print(f"    - Severity: {alert_event.get('severity')}")

                # Clean up beta outage
                await http_client.post(
                    "/api/simulator/acquirers/acquirer_beta/outage",
                    json={
                        "active": False,
                        "behavior": "RETURN_DECLINE",
                        "transition_seconds": 0.0,
                    },
                )
                _ = await asyncio.wait_for(ws.recv(), timeout=2.0)

            q3_pass = (
                (btn_rtt_ms < 100.0)
                and (resp_data.get("outage_active") is True)
                and (alert_event.get("severity") == "CRITICAL")
            )
            res3_str = "PASSED [INSTANT VISIBLE STATE MUTATION VERIFIED]" if q3_pass else "FAILED"
            print(f"  -> Q3 RESULT: {res3_str}")

            # ------------------------------------------------------------------
            # QUESTION 4: WebSocket Disconnect & Visible Reconnecting State
            # ------------------------------------------------------------------
            print("\n" + "=" * 80)
            print(" QUESTION 4 AUDIT: FORCED WEBSOCKET DISCONNECT & RECONNECTING")
            print("=" * 80)

            print("  Forcing WebSocket client close...")
            await ws.close()
            assert ws.close_code is not None or not getattr(ws, "open", False)
            print("  WebSocket client state immediately: CLOSED (1000 OK)")

            print("  Verifying client auto-reconnect against live server...")
            t_rec_start = time.perf_counter()
            async with websockets.connect(WS_URL) as ws_reconnected:
                raw_rec = await asyncio.wait_for(ws_reconnected.recv(), timeout=2.0)
                reconnect_data = json.loads(raw_rec)
                t_reconnect_ms = (time.perf_counter() - t_rec_start) * 1000

                assert reconnect_data["event_type"] == "BOOTSTRAP"
                rec_acqs = list(reconnect_data["states"].keys())
                print(f"  Reconnection in {t_reconnect_ms:.2f}ms. BOOTSTRAP received.")
                print(f"  State continuity preserved: Acquirers = {rec_acqs}")

            q4_pass = True
            print("  -> Q4 RESULT: PASSED [HONEST VISIBLE RECONNECTING CONFIRMED]")

        # ----------------------------------------------------------------------
        # SUMMARY REPORT
        # ----------------------------------------------------------------------
        print("\n" + "=" * 80)
        print(" QA VERIFICATION SUMMARY AUDIT MATRIX")
        print("=" * 80)
        p1 = "PASSED" if q1_pass else "FAILED"
        p2 = "PASSED" if q2_pass else "FAILED"
        p3 = "PASSED" if q3_pass else "FAILED"
        p4 = "PASSED" if q4_pass else "FAILED"
        print(f" [TC-QA-701] Outage Marker Alignment (Tx #51 coincident): {p1}")
        print(f" [TC-QA-702] Telemetry Cadence (Mean: {avg_latency:.2f}ms): {p2}")
        print(f" [TC-QA-703] Button Responsiveness ({btn_rtt_ms:.2f}ms < 100ms): {p3}")
        print(f" [TC-QA-704] Resilient Reconnecting State (No Freeze): {p4}")
        print("=" * 80)

        all_passed = q1_pass and q2_pass and q3_pass and q4_pass
        return 0 if all_passed else 1

    finally:
        server.should_exit = True
        sim_server.should_exit = True
        server_thread.join(timeout=2.0)
        sim_thread.join(timeout=2.0)
        print("[SHUTDOWN] Real TCP servers stopped cleanly.")


def main() -> None:
    """Run async live QA audit."""
    sys.exit(asyncio.run(run_live_qa()))


if __name__ == "__main__":
    main()
```

</details>

<details><summary><code>poison_batch.py</code></summary>

```python
"""One duplicate transaction_id (e.g. a client retry) inside a micro-batch drops the whole batch."""
import asyncio, os, sqlite3, httpx, logging
from acquirer_sim.app import create_app
from acquirer_sim.models import AuthorizeRequest, LatencyConfig
from data_layer.sqlite_logger import MetricsLogger
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.router import BanditRouter
logging.basicConfig(level=logging.ERROR)
DB = "_audit/poison.db"
async def main():
    for f in (DB, DB+"-wal", DB+"-shm"):
        if os.path.exists(f): os.remove(f)
    sim = create_app(default_latency=LatencyConfig(base_ms=0.0, jitter_ms=0.0), seed=1)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=sim), base_url="http://t") as c:
        ml = MetricsLogger(db_path=DB); await ml.start()
        r = BanditRouter(config=RouterConfig(routes=[AcquirerRouteConfig(acquirer_id="acquirer_alpha", base_url="http://t")], seed=1), http_client=c, metrics_logger=ml)
        await r.route(AuthorizeRequest(transaction_id="tx_retry", amount=10.0))
        await asyncio.sleep(0.2)                       # first copy committed
        await r.route(AuthorizeRequest(transaction_id="tx_retry", amount=10.0))  # client retry, same id
        for i in range(19):
            await r.route(AuthorizeRequest(transaction_id=f"tx_{i}", amount=10.0))
        await ml.close()
    n = sqlite3.connect(DB).execute("select count(*) from transactions").fetchone()[0]
    print(f"routed=21 (1 + retry + 19 unique), rows persisted={n}, total_written={ml.total_written}")
asyncio.run(main())
```

</details>

<details><summary><code>shipped_cfg.py</code></summary>

```python
"""Benchmark schedule of compare_psr, but Loom with the SHIPPED server defaults (decay 0.98, stochastic)."""
import asyncio, statistics as st, sys
sys.path.insert(0, "_audit")
from multiseed import run, summ
async def main():
    s42 = await run("loom", 42, 777, actuation="stochastic", decay=0.98)
    print("seed42/777 shipped-cfg:", {k: round(v, 4) for k, v in s42.items()})
    rows = []
    for k in range(100):
        L = await run("loom", 42 + 10 * k, 777 + k, actuation="stochastic", decay=0.98)
        B = await run("baseline", 42 + 10 * k, m=3)
        rows.append((L, B))
    for met in ("psr", "out", "dw_max", "outage_flips", "fail_alpha"):
        print(f"loom_shipped {met:<14}", summ([r[0][met] for r in rows]))
    d = [r[0]["psr"] - r[1]["psr"] for r in rows]
    print("loom_shipped - b3 psr:", summ(d), "wins", sum(x > 1e-12 for x in d), "losses", sum(x < -1e-12 for x in d))
asyncio.run(main())
```

</details>

<details><summary><code>trace_checks.py</code></summary>

```python
"""Check README narrative details: M=1 cascade (Tx 57 trip, Tx 58-86 to dead Alpha) and Phase-4 post-recovery alpha posterior."""
import asyncio, logging, httpx
from acquirer_sim.app import create_app
from acquirer_sim.models import AuthorizeRequest, LatencyConfig, OutageToggleRequest
from baseline_router.models import BaselineRouterConfig, FailoverPolicyConfig
from baseline_router.router import StaticBaselineRouter
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig
logging.disable(logging.CRITICAL)
A, B = "acquirer_alpha", "acquirer_beta"
async def go(kind):
    sim = create_app(default_acquirers=[A, B], default_base_rate=0.95, default_latency=LatencyConfig(base_ms=0.0, jitter_ms=0.0), seed=42)
    sim.state.registry.get(A).set_success_rate(0.95); sim.state.registry.get(B).set_success_rate(0.94)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=sim), base_url="http://testserver") as c:
        routes = [AcquirerRouteConfig(acquirer_id=a, base_url="http://testserver", state_config=AcquirerStateConfig(decay_factor=0.95)) for a in (A, B)]
        if kind == "m1":
            r = StaticBaselineRouter(config=BaselineRouterConfig(routes=routes, priority_order=[A, B], failover_policy=FailoverPolicyConfig(consecutive_failure_threshold=1, cooldown_transactions=30, failback_mode="probe")), http_client=c)
        else:
            r = BanditRouter(config=RouterConfig(routes=routes, pid_config=PIDConfig(kp=0.12, ki=0.005, kd=0.25, integral_max=1.0, min_allocation=0.03, actuation_mode="deficit"), seed=777), http_client=c)
        out = []
        for tx in range(1, 151):
            if tx == 51: await c.post(f"/acquirers/{A}/admin/outage", json=OutageToggleRequest(active=True).model_dump())
            if tx == 101: await c.post(f"/acquirers/{A}/admin/outage", json=OutageToggleRequest(active=False).model_dump())
            res = await r.route(AuthorizeRequest(transaction_id=f"t{tx}", amount=50.0))
            sa = r.get_state(A) if kind == "p4" else None
            out.append((tx, res.selected_acquirer[9:], res.authorized, sa))
        return out
async def main():
    m1 = await go("m1")
    print("M=1 outage trace (tx:route/auth):", " ".join(f"{t}:{s[0]}{'+' if a else '-'}" for t, s, a, _ in m1[50:100]))
    run = [];
    for t, s, a, _ in m1[50:100]:
        run.append(t) if s == "alpha" else None
    print("M=1 Tx routed to alpha during outage:", run)
    p4 = await go("p4")
    for t, s, a, st in p4[95:150]:
        if s == "alpha" or t in (100,):
            print(f"P4 tx{t} -> {s} auth={a} alpha_state a={st.alpha:.2f} b={st.beta:.2f} mean={st.expected_success_rate:.3f}")
asyncio.run(main())
```

</details>

<details><summary><code>ws_listen.py</code></summary>

```python
"""Listen to live /ws/telemetry; measure server-result-timestamp -> client receipt (same host clock)."""
import asyncio, json, time, statistics as st, websockets
async def main(sec=10.0):
    d, seqs, t0 = [], [], time.time()
    w = []
    async with websockets.connect("ws://127.0.0.1:8000/ws/telemetry") as ws:
        boot = json.loads(await ws.recv()); print("bootstrap keys:", list(boot), "states:", list(boot["states"]))
        while time.time() - t0 < sec:
            try: m = json.loads(await asyncio.wait_for(ws.recv(), 1.0))
            except asyncio.TimeoutError: continue
            if m.get("event_type") == "ROUTING_COMPLETED":
                d.append((time.time() - m["timestamp"]) * 1000); seqs.append(m["sequence_number"]); w.append(m["smoothed_allocation"])
    s = sorted(d)
    print(f"frames={len(d)} rate={len(d)/sec:.2f}/s push(ms) mean={st.mean(d):.3f} p50={s[len(s)//2]:.3f} p95={s[int(len(s)*.95)]:.3f} max={s[-1]:.3f}")
    dm = max(max(abs(w[i][k]-w[i-1][k]) for k in w[i]) for i in range(1,len(w)))
    print(f"max per-tx smoothed allocation delta (any arm) = {dm*100:.2f}%")
asyncio.run(main())
```

</details>


#### Lens D, feasibility (`loom-audit-D/_audit/`)

<details><summary><code>common.py</code></summary>

```python
"""Shared audit harness (Lens D). Scratch only.

- SimTransport: httpx transport that calls the repo's AcquirerSimulator objects directly
  (same code path as acquirer_sim/app.py authorize endpoint) but with VIRTUAL latency:
  asyncio.sleep inside the simulator is replaced by a recorder, and a request whose simulated
  latency exceeds the route timeout raises httpx.ReadTimeout (the acquirer still processed it).
- Router factories for: static baseline (repo), Loom bench config (compare_psr), Loom live
  default config (router_core/server.py defaults), and a throwaway "competent" router.
"""

from __future__ import annotations

import contextvars
import json
import math
import random
import types
from collections import deque
from dataclasses import dataclass, field
from typing import Any

import httpx
import numpy as np

import acquirer_sim.simulator as simmod
from acquirer_sim.models import AuthorizeRequest, AuthorizeResponse, LatencyConfig, OutageBehavior
from acquirer_sim.simulator import AcquirerOutageHttpException, MultiAcquirerSimulator
from baseline_router.models import BaselineRouterConfig, FailoverPolicyConfig
from baseline_router.router import StaticBaselineRouter
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig

# ---------------------------------------------------------------------------
# Virtual-latency patch of the simulator's asyncio.sleep
# ---------------------------------------------------------------------------
_last_delay: contextvars.ContextVar[float] = contextvars.ContextVar("_last_delay", default=0.0)


async def _record_sleep(sec: float) -> None:
    _last_delay.set(sec)


simmod.asyncio = types.SimpleNamespace(sleep=_record_sleep)  # type: ignore[attr-defined]

import logging  # noqa: E402

logging.disable(logging.CRITICAL)  # silence per-tx INFO logs (speed)

TECH_DECLINE_CODES = {"ACQUIRER_OUTAGE", "SYSTEM_ERROR", "ISSUER_UNAVAILABLE"}


class SimTransport(httpx.AsyncBaseTransport):
    def __init__(self, registry: MultiAcquirerSimulator) -> None:
        self.registry = registry
        self.virtual_time = 0.0
        self.timeouts: list[tuple[str, str, bool]] = []  # (tx, acq, actually_authorized)

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        parts = request.url.path.strip("/").split("/")
        acq = parts[1]
        body = json.loads(request.content)
        req = AuthorizeRequest.model_validate(body)
        sim = self.registry.get(acq)
        timeout = (request.extensions.get("timeout") or {}).get("read")
        _last_delay.set(0.0)
        try:
            resp = await sim.execute_authorization(req)
        except AcquirerOutageHttpException as e:
            d = _last_delay.get()
            if timeout is not None and d > timeout:
                self.virtual_time += timeout
                self.timeouts.append((req.transaction_id, acq, False))
                raise httpx.ReadTimeout("simulated read timeout", request=request) from None
            self.virtual_time += d
            return httpx.Response(503, json={"detail": e.message, "acquirer_id": acq}, request=request)
        d = _last_delay.get()
        if timeout is not None and d > timeout:
            self.virtual_time += timeout
            self.timeouts.append((req.transaction_id, acq, resp.authorized))
            raise httpx.ReadTimeout("simulated read timeout", request=request)
        self.virtual_time += d
        return httpx.Response(
            200,
            content=resp.model_dump_json().encode(),
            headers={"content-type": "application/json"},
            request=request,
        )


# ---------------------------------------------------------------------------
# Outcome classification (used for metrics and by the competent router)
# ---------------------------------------------------------------------------
def classify_http(resp: httpx.Response | None, exc: Exception | None) -> tuple[str, AuthorizeResponse | None]:
    if exc is not None:
        if isinstance(exc, httpx.TimeoutException):
            return "timeout", None
        return "tech", None
    assert resp is not None
    if resp.status_code == 200:
        p = AuthorizeResponse.model_validate(resp.json())
        if p.authorized:
            return "auth", p
        if p.decline_code in TECH_DECLINE_CODES:
            return "tech", p
        return "issuer", p
    return "tech", None


@dataclass
class Outcome:
    tx: str
    first: str
    attempts: list[tuple[str, str]]
    authorized: bool
    alloc: dict[str, float] | None = None


# ---------------------------------------------------------------------------
# Competent baseline router (throwaway)
# ---------------------------------------------------------------------------
class CompetentRouter:
    """Sliding-window auth-rate routing + technical-only circuit breaker + optional 1 retry.

    - est_i = (succ + a0)/(n + a0 + b0) over the last W outcomes routed to i (prior 9:1, ~0.9).
    - Preferred route: argmax est with hysteresis (switch only if better by `margin`).
    - eps exploration to a random other closed route (keeps windows fresh).
    - Breaker counts ONLY technical failures (5xx, timeout, network, ACQUIRER_OUTAGE code):
      opens after k consecutive tech failures; half-open probe after `cooldown` tx (one probe).
    - Retry: on technical failure of attempt 1, retry once on best other non-open route
      (never on issuer decline; optionally not on timeout -> ambiguous outcome).
    """

    def __init__(self, client: httpx.AsyncClient, routes: list[AcquirerRouteConfig], priority: list[str],
                 seed: int, retry: bool, retry_on_timeout: bool = False, k: int = 3,
                 cooldown: int = 30, window: int = 50, eps: float = 0.02, margin: float = 0.03,
                 health: str = "auth") -> None:
        self.health = health  # 'auth': window of approvals; 'tech': window of technical successes only
        self.client = client
        self.routes = {r.acquirer_id: r for r in routes}
        self.priority = list(priority)
        self.rng = random.Random(seed)
        self.retry = retry
        self.retry_on_timeout = retry_on_timeout
        self.k, self.cooldown, self.eps, self.margin = k, cooldown, eps, margin
        self.win = {a: deque(maxlen=window) for a in priority}
        self.consec_tech = {a: 0 for a in priority}
        self.open_at: dict[str, int | None] = {a: None for a in priority}
        self.half_open: dict[str, bool] = {a: False for a in priority}
        self.preferred = priority[0]
        self.tx = 0
        self.ambiguous_retries = 0
        self.dup_risk = 0

    def est(self, a: str) -> float:
        w = self.win[a]
        return (sum(w) + 9.0) / (len(w) + 10.0)

    def is_open(self, a: str) -> bool:
        return self.open_at[a] is not None and not self.half_open[a]

    def _update_breaker_clock(self) -> str | None:
        """Return an acquirer due for a half-open probe, if any."""
        for a in self.priority:
            oa = self.open_at[a]
            if oa is not None and not self.half_open[a] and self.tx - oa >= self.cooldown:
                self.half_open[a] = True
                return a
        return None

    def _choose(self, exclude: set[str]) -> str | None:
        cands = [a for a in self.priority if a not in exclude and not self.is_open(a)]
        if not cands:
            return None
        # hysteresis on preferred
        best = max(cands, key=lambda a: (self.est(a), -self.priority.index(a)))
        if self.preferred in cands and self.est(best) < self.est(self.preferred) + self.margin:
            best = self.preferred
        if not exclude:
            self.preferred = best
            others = [a for a in cands if a != best]
            if others and self.rng.random() < self.eps:
                return self.rng.choice(others)
        return best

    def _record(self, a: str, cls: str) -> None:
        if self.health == "tech":
            self.win[a].append(0.0 if cls in ("tech", "timeout") else 1.0)
        else:
            self.win[a].append(1.0 if cls == "auth" else 0.0)
        if cls in ("tech", "timeout"):
            self.consec_tech[a] += 1
            if self.half_open[a]:
                self.open_at[a] = self.tx
                self.half_open[a] = False
            elif self.open_at[a] is None and self.consec_tech[a] >= self.k:
                self.open_at[a] = self.tx
        else:
            self.consec_tech[a] = 0
            if self.open_at[a] is not None:
                self.open_at[a] = None
                self.half_open[a] = False

    async def _attempt(self, a: str, req: AuthorizeRequest) -> tuple[str, AuthorizeResponse | None]:
        r = self.routes[a]
        try:
            resp = await self.client.post(r.get_authorize_url(), json=req.model_dump(), timeout=r.timeout_sec)
            return classify_http(resp, None)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            return classify_http(None, exc)

    async def route(self, req: AuthorizeRequest) -> Outcome:
        self.tx += 1
        probe = self._update_breaker_clock()
        first = probe if probe is not None else self._choose(set())
        if first is None:  # everything open: fall back to the breaker opened earliest
            first = min(self.priority, key=lambda a: self.open_at[a] or 0)
        attempts = []
        cls, _ = await self._attempt(first, req)
        self._record(first, cls)
        attempts.append((first, cls))
        authorized = cls == "auth"
        if self.retry and cls in ("tech", "timeout"):
            if cls == "timeout" and not self.retry_on_timeout:
                pass
            else:
                if cls == "timeout":
                    self.ambiguous_retries += 1
                second = self._choose({first})
                if second is not None:
                    cls2, _ = await self._attempt(second, req)
                    self._record(second, cls2)
                    attempts.append((second, cls2))
                    authorized = cls2 == "auth"
        return Outcome(req.transaction_id, first, attempts, authorized)


# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------
def make_registry(rates: dict[str, float], seed: int, spike_ms: float = 500.0) -> MultiAcquirerSimulator:
    reg = MultiAcquirerSimulator(
        default_acquirers=list(rates.keys()),
        default_base_rate=0.95,
        default_latency=LatencyConfig(base_ms=0.0, jitter_ms=0.0, outage_spike_ms=spike_ms),
        seed=seed,
    )
    for a, r in rates.items():
        reg.get(a).set_success_rate(r)
    return reg


def make_routes(ids: list[str], decay: float, timeout: float = 2.0) -> list[AcquirerRouteConfig]:
    return [
        AcquirerRouteConfig(acquirer_id=a, base_url="http://testserver", timeout_sec=timeout,
                            state_config=AcquirerStateConfig(decay_factor=decay))
        for a in ids
    ]


BENCH_PID = dict(kp=0.12, ki=0.005, kd=0.25, integral_max=1.0, min_allocation=0.03, actuation_mode="deficit")


def make_router(kind: str, client: httpx.AsyncClient, ids: list[str], seed: int, timeout: float = 2.0) -> Any:
    if kind == "static_M3":
        cfg = BaselineRouterConfig(
            routes=make_routes(ids, 0.95, timeout), priority_order=ids,
            failover_policy=FailoverPolicyConfig(consecutive_failure_threshold=3, cooldown_transactions=30,
                                                 failback_mode="probe"))
        return StaticBaselineRouter(config=cfg, http_client=client)
    if kind == "loom_bench":
        return BanditRouter(config=RouterConfig(routes=make_routes(ids, 0.95, timeout),
                                                pid_config=PIDConfig(**BENCH_PID), seed=seed),
                            http_client=client)
    if kind == "loom_live":  # router_core/server.py defaults: decay .98, PIDConfig() => stochastic
        return BanditRouter(config=RouterConfig(routes=make_routes(ids, 0.98, timeout),
                                                pid_config=PIDConfig(), seed=seed),
                            http_client=client)
    if kind == "comp_noretry":
        return CompetentRouter(client, make_routes(ids, 0.95, timeout), ids, seed, retry=False)
    if kind == "comp_retry":
        return CompetentRouter(client, make_routes(ids, 0.95, timeout), ids, seed, retry=True)
    if kind == "comp_tech":  # decline-code-aware health: issuer declines are NOT acquirer failures
        return CompetentRouter(client, make_routes(ids, 0.95, timeout), ids, seed, retry=False, health="tech",
                               window=200, margin=0.01)
    raise ValueError(kind)


async def route_one(router: Any, req: AuthorizeRequest) -> Outcome:
    res = await router.route(req)
    if isinstance(res, Outcome):
        return res
    # RoutingResult from repo routers
    if res.status == "ERROR":
        cls = "timeout" if (res.error_message or "").startswith("Transport error") and "Timeout" in (res.error_message or "") else "tech"
    elif res.authorized:
        cls = "auth"
    elif res.response_payload is not None and res.response_payload.decline_code in TECH_DECLINE_CODES:
        cls = "tech"
    else:
        cls = "issuer"
    return Outcome(res.transaction_id, res.selected_acquirer, [(res.selected_acquirer, cls)], res.authorized,
                   dict(res.smoothed_allocation) if res.smoothed_allocation else None)


def mean_ci(xs: list[float]) -> tuple[float, float]:
    n = len(xs)
    m = sum(xs) / n
    if n < 2:
        return m, float("nan")
    sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    # t_{0.975, n-1}; n>=30 -> ~2.02-2.05
    t = {30: 2.045, 40: 2.023, 50: 2.010}.get(n, 1.96 if n > 60 else 2.05)
    return m, t * sd / math.sqrt(n)
```

</details>

<details><summary><code>des.py</code></summary>

```python
"""Discrete-event simulator for Tasks 3/4: wall-clock behaviour of Loom's transaction-clocked
controller vs wall-clock/count breakers under different TPS, latency, burstiness, capacity.

LoomCore re-implements BanditRouter.route()'s decision path WITHOUT HTTP using the repo's own
BanditStateRegistry / calculate_pid_step / PIDState (same RNG consumption order as router.py),
so feedback can be DELAYED by acquirer latency (concurrency). Verified against BanditRouter in
verify_des.py (sequential, zero-latency -> identical decision sequence).
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field

import numpy as np

from router_core.bandit import BanditStateRegistry
from router_core.pid import PIDConfig, PIDState, calculate_pid_step
from router_core.state import AcquirerStateConfig

ALPHA, BETA = "acquirer_alpha", "acquirer_beta"


class LoomCore:
    def __init__(self, ids: list[str], decay: float, pid: PIDConfig, seed: int) -> None:
        self.ids = list(ids)
        self.reg = BanditStateRegistry()
        for a in ids:
            self.reg.register_acquirer(a, config=AcquirerStateConfig(decay_factor=decay))
        self.rng = np.random.default_rng(seed)
        self.cfg = pid
        self.state = PIDState.initialize(ids)
        self.cur = dict(self.state.previous_allocation)
        self.cum = {a: 0.0 for a in ids}
        self.disp = {a: 0 for a in ids}

    def decide(self, now: float = 0.0) -> str:
        samples = self.reg.sample_all(rng=self.rng)
        win = max(samples.keys(), key=lambda a: (samples[a], a))
        target = {a: 1.0 if a == win else 0.0 for a in samples}
        st = calculate_pid_step(target_allocation=target, current_allocation=self.cur, state=self.state,
                                config=self.cfg, dt=1.0)
        self.cur = st.smoothed_allocation
        self.state = st.next_state
        if self.cfg.actuation_mode == "deficit":
            for a in self.ids:
                self.cum[a] += self.cur[a]
            sel = max(sorted(self.ids), key=lambda a: (self.cum[a] - self.disp[a], a))
            self.disp[sel] += 1
            return sel
        keys = sorted(self.cur.keys())
        return str(self.rng.choice(keys, p=[self.cur[k] for k in keys]))

    def feedback(self, a: str, ok: bool, tech: bool, now: float) -> None:
        self.reg.record_outcome(a, ok, timestamp=now)

    def weight(self, a: str) -> float:
        return self.cur[a]


class BreakerCore:
    """Priority router; breaker counts ONLY technical failures; opens after k consecutive tech
    failures OR (rate mode) >=50% tech failures among >=min_n attempts in the last window_s seconds.
    Cooldown is wall-clock seconds; then one half-open probe."""

    def __init__(self, ids: list[str], k: int = 3, cooldown_s: float = 30.0, window_s: float | None = None,
                 min_n: int = 5) -> None:
        self.ids = list(ids)
        self.k, self.cooldown_s, self.window_s, self.min_n = k, cooldown_s, window_s, min_n
        self.consec = {a: 0 for a in ids}
        self.open_until: dict[str, float | None] = {a: None for a in ids}
        self.probe_inflight = {a: False for a in ids}
        self.hist: dict[str, list[tuple[float, bool]]] = {a: [] for a in ids}
        self.trip_times: list[tuple[float, str]] = []

    def available(self, a: str, now: float) -> bool:
        return self.open_until[a] is None

    def decide(self, now: float, exclude: set[str] | None = None) -> str | None:
        exclude = exclude or set()
        for a in self.ids:  # half-open probe
            ou = self.open_until[a]
            if a not in exclude and ou is not None and now >= ou and not self.probe_inflight[a]:
                self.probe_inflight[a] = True
                return a
        for a in self.ids:
            if a not in exclude and self.open_until[a] is None:
                return a
        return None if exclude else self.ids[0]

    def _open(self, a: str, now: float) -> None:
        self.open_until[a] = now + self.cooldown_s
        self.trip_times.append((now, a))

    def feedback(self, a: str, ok: bool, tech: bool, now: float) -> None:
        if self.probe_inflight[a]:
            self.probe_inflight[a] = False
            if tech:
                self._open(a, now)
            else:
                self.open_until[a] = None
                self.consec[a] = 0
            return
        if self.open_until[a] is not None:
            return
        if tech:
            self.consec[a] += 1
        else:
            self.consec[a] = 0
        if self.window_s is not None:
            h = self.hist[a]
            h.append((now, tech))
            while h and h[0][0] < now - self.window_s:
                h.pop(0)
            if len(h) >= self.min_n and sum(1 for _, t in h if t) / len(h) >= 0.5:
                self._open(a, now)
                h.clear()
                return
        if self.consec[a] >= self.k:
            self._open(a, now)

    def weight(self, a: str) -> float:
        return 1.0 if self.decide_peek() == a else 0.0

    def decide_peek(self) -> str:
        for a in self.ids:
            if self.open_until[a] is None:
                return a
        return self.ids[0]


@dataclass
class World:
    rates: dict[str, float]
    lat_ok: float = 0.3          # s, normal auth latency
    outage_mode: str = "fast"    # "fast" (503 in 50ms) | "timeout" (2.0 s)
    t0: float = 0.0
    t1: float = 0.0
    dead: str = ALPHA
    beta_cap_tps: float | None = None   # capacity of beta (rate limiter, 1-s window); overflow -> 429
    collapse: bool = False              # if True, overload degrades ALL beta requests (goodput collapse)
    rng: np.random.Generator = field(default_factory=lambda: np.random.default_rng(0))
    _beta_win: list[float] = field(default_factory=list)

    def attempt(self, a: str, now: float) -> tuple[float, bool, bool]:
        """Return (latency, ok, technical_failure)."""
        if a == self.dead and self.t0 <= now < self.t1:
            return (0.05 if self.outage_mode == "fast" else 2.0), False, True
        if a == BETA and self.beta_cap_tps is not None:
            w = self._beta_win
            w.append(now)
            while w and w[0] < now - 1.0:
                w.pop(0)
            load = len(w)
            if load > self.beta_cap_tps:
                if not self.collapse:
                    return 0.02, False, True  # 429 fast reject
                # collapse: every request degrades as overload grows
                p = self.rates[a] * max(0.0, 1.0 - (load - self.beta_cap_tps) / self.beta_cap_tps)
                ok = self.rng.random() < p
                return self.lat_ok * 3, ok, not ok
        ok = self.rng.random() < self.rates[a]
        return self.lat_ok, ok, False


def poisson_arrivals(rate_fn, t_end: float, rng: np.random.Generator, rate_max: float) -> list[float]:
    t, out = 0.0, []
    while True:
        t += rng.exponential(1.0 / rate_max)
        if t >= t_end:
            return out
        if rng.random() < rate_fn(t) / rate_max:
            out.append(t)


def simulate(router, world: World, arrivals: list[float], retry: bool = False, record_every: int = 1):
    """Run DES. Returns per-decision log arrays and summary dict."""
    seq = 0
    ev: list = []
    for t in arrivals:
        heapq.heappush(ev, (t, seq, "arr", None)); seq += 1
    dec_t, dec_a, w_dead = [], [], []
    fails_dead_outage = 0
    visible_fail_outage = 0
    n_outage = 0
    n_ok = n_tot = 0
    while ev:
        now, _, kind, payload = heapq.heappop(ev)
        if kind == "arr":
            a = router.decide(now)
            dec_t.append(now); dec_a.append(a)
            w_dead.append(router.weight(world.dead))
            lat, ok, tech = world.attempt(a, now)
            heapq.heappush(ev, (now + lat, seq, "done", (a, ok, tech, now, 0))); seq += 1
        else:
            a, ok, tech, t_arr, tries = payload
            router.feedback(a, ok, tech, now)
            in_out = world.t0 <= t_arr < world.t1
            if a == world.dead and in_out and not ok:
                fails_dead_outage += 1
            if retry and tech and tries == 0:
                b = router.decide(now, exclude={a}) if isinstance(router, BreakerCore) else None
                if b is not None:
                    lat, ok2, tech2 = world.attempt(b, now)
                    heapq.heappush(ev, (now + lat, seq, "done", (b, ok2, tech2, t_arr, 1))); seq += 1
                    continue
            n_tot += 1
            n_ok += ok
            if in_out:
                n_outage += 1
                visible_fail_outage += (not ok)
    return dict(dec_t=np.array(dec_t), dec_a=dec_a, w_dead=np.array(w_dead),
                fails_dead_outage=fails_dead_outage, visible_fail_outage=visible_fail_outage,
                n_outage=n_outage, psr=n_ok / max(1, n_tot))


def first_time(dec_t, cond_arr, after: float) -> float:
    idx = np.where((dec_t >= after) & cond_arr)[0]
    return float(dec_t[idx[0]] - after) if len(idx) else float("nan")
```

</details>

<details><summary><code>exp1_scenarios.py</code></summary>

```python
"""Task 1: multi-seed scenario matrix. Static M=3 vs Loom (bench) vs Loom (live default) vs competent.

Usage: python _audit/exp1_scenarios.py [n_seeds]
"""

from __future__ import annotations

import asyncio
import sys
import time

import httpx

from common import (Outcome, SimTransport, make_registry, make_router, mean_ci, route_one)
from acquirer_sim.models import AuthorizeRequest, OutageBehavior

ROUTERS = ["static_M3", "loom_bench", "loom_live", "comp_noretry", "comp_retry"]
A2 = {"acquirer_alpha": 0.95, "acquirer_beta": 0.94}
A3 = {"acquirer_alpha": 0.95, "acquirer_beta": 0.94, "acquirer_gamma": 0.93}
DEAD = "acquirer_alpha"


def out(active, beh=OutageBehavior.RETURN_DECLINE):
    return [("outage", DEAD, active, beh)]


# name -> (rates, spike_ms, timeout, phases[(n, actions)])
SCENARIOS = {
    "S1_base_decline": (A2, 500, 2.0, [(50, []), (50, out(True)), (50, out(False))]),
    "S2_http503": (A2, 500, 2.0, [(50, []), (50, out(True, OutageBehavior.HTTP_503)), (50, out(False))]),
    "S3_latency_spike_500ms": (A2, 500, 2.0, [(50, []), (50, out(True, OutageBehavior.LATENCY_SPIKE)), (50, out(False))]),
    "S3b_latency_spike_3s_timeout": (A2, 3000, 2.0, [(50, []), (50, out(True, OutageBehavior.LATENCY_SPIKE)), (50, out(False))]),
    "S4_gray_60pct": (A2, 500, 2.0, [(50, []), (50, [("rate", DEAD, 0.60)]), (50, [("rate", DEAD, 0.95)])]),
    "S5_blip_5tx": (A2, 500, 2.0, [(50, []), (5, out(True)), (95, out(False))]),
    "S6_long_200tx": (A2, 500, 2.0, [(50, []), (200, out(True)), (50, out(False))]),
    "S7_three_acq": (A3, 500, 2.0, [(50, []), (50, out(True)), (50, out(False))]),
}


def apply(reg, actions):
    for act in actions:
        if act[0] == "outage":
            reg.get(act[1]).set_outage(active=act[2], behavior=act[3])
        elif act[0] == "rate":
            reg.get(act[1]).set_success_rate(act[2])


def divert_index(firsts: list[str], dead: str, win: int = 10) -> int:
    """Tx index (from outage start) after which >=90% of first attempts avoid `dead` for a 10-tx window."""
    n = len(firsts)
    for k in range(0, max(1, n - win + 1)):
        w = firsts[k:k + win]
        if len(w) < win:
            break
        if sum(1 for a in w if a == dead) <= win // 10:
            return k
    return n  # censored


async def run_one(scn: str, kind: str, seed: int) -> dict:
    rates, spike, timeout, phases = SCENARIOS[scn]
    reg = make_registry(rates, seed=seed, spike_ms=spike)
    tr = SimTransport(reg)
    ids = list(rates.keys())
    async with httpx.AsyncClient(transport=tr, base_url="http://testserver") as client:
        router = make_router(kind, client, ids, seed=777 + seed, timeout=timeout)
        outs: list[list[Outcome]] = []
        for pi, (n, actions) in enumerate(phases):
            apply(reg, actions)
            ph = []
            for i in range(n):
                ph.append(await route_one(router, AuthorizeRequest(transaction_id=f"{scn}_{kind}_{pi}_{i}", amount=50.0)))
            outs.append(ph)
    allx = [o for ph in outs for o in ph]
    outage = outs[1]
    firsts = [o.first for o in outage]
    res = {
        "psr": sum(o.authorized for o in allx) / len(allx),
        "psr_outage": sum(o.authorized for o in outage) / len(outage),
        "absorbed": sum(1 for o in outage for (a, c) in o.attempts if a == DEAD and c != "auth"),
        "visible_fail_outage": sum(1 for o in outage if not o.authorized),
        "divert_tx": divert_index(firsts, DEAD),
        "recovery_alpha_share": sum(1 for o in outs[2][-30:] if o.first == DEAD) / 30,
        "timeouts": len(tr.timeouts),
    }
    if outage[0].alloc is not None:
        idx = next((k for k, o in enumerate(outage) if o.alloc[DEAD] <= 0.10), len(outage))
        res["divert_w"] = idx
    if hasattr(router, "ambiguous_retries"):
        res["ambiguous_retries"] = router.ambiguous_retries
    return res


async def main(n_seeds: int) -> None:
    t0 = time.time()
    seeds = list(range(1, n_seeds + 1))
    # sanity: reproduce compare_psr seed 42 (static 138, loom 129 authorized; alpha failures 4 / 11)
    for kind in ("static_M3", "loom_bench"):
        reg = make_registry(A2, seed=42)
        tr = SimTransport(reg)
        async with httpx.AsyncClient(transport=tr, base_url="http://testserver") as client:
            r = make_router(kind, client, list(A2), seed=777)
            auth = 0
            fa = 0
            for pi, (n, acts) in enumerate(SCENARIOS["S1_base_decline"][3]):
                apply(reg, acts)
                for i in range(n):
                    o = await route_one(r, AuthorizeRequest(transaction_id=f"chk{pi}_{i}", amount=50.0))
                    auth += o.authorized
                    if pi == 1 and o.first == DEAD and not o.authorized:
                        fa += 1
            print(f"SANITY seed42 {kind}: authorized={auth}/150 outage_alpha_failures={fa}")
    print(f"n_seeds={n_seeds} (sim seed s=1..{n_seeds}; Loom router seed 777+s)")
    for scn in SCENARIOS:
        print(f"\n=== {scn} ===")
        print(f"{'router':<14}{'PSR% (95%CI)':>20}{'outagePSR%':>18}{'absorbed':>14}{'visibleFail':>14}{'divert_tx':>14}{'divert_w':>12}{'recovAlpha':>12}")
        rows = {}
        for kind in ROUTERS:
            rs = [await run_one(scn, kind, s) for s in seeds]
            rows[kind] = rs

            def f(key, scale=1.0, fmt="{:.2f}"):
                vals = [r[key] * scale for r in rs if key in r]
                if not vals:
                    return "-"
                m, h = mean_ci(vals)
                return (fmt + "±" + fmt).format(m, h)
            extra = ""
            if "ambiguous_retries" in rs[0]:
                extra = f" ambigRetries={sum(r['ambiguous_retries'] for r in rs)} timeouts={sum(r['timeouts'] for r in rs)}"
            elif rs[0]["timeouts"]:
                extra = f" timeouts={sum(r['timeouts'] for r in rs)}"
            print(f"{kind:<14}{f('psr', 100):>20}{f('psr_outage', 100):>18}{f('absorbed', 1, '{:.1f}'):>14}"
                  f"{f('visible_fail_outage', 1, '{:.1f}'):>14}{f('divert_tx', 1, '{:.1f}'):>14}{f('divert_w', 1, '{:.1f}'):>12}"
                  f"{f('recovery_alpha_share', 100, '{:.0f}'):>12}{extra}")
        # paired differences vs static on total PSR
        base = rows["static_M3"]
        diffs = []
        for kind in ROUTERS[1:]:
            d = [(rows[kind][i]["psr"] - base[i]["psr"]) * 100 for i in range(len(seeds))]
            m, h = mean_ci(d)
            diffs.append(f"{kind}: {m:+.2f}±{h:.2f}pp")
        print("  paired dPSR vs static_M3 -> " + " | ".join(diffs))
    print(f"\nelapsed {time.time() - t0:.1f}s")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 40))
```

</details>

<details><summary><code>exp1b_extra.py</code></summary>

```python
"""Task 1 extras: (a) asymmetric secondary (beta .88) with long recovery -> failback stickiness;
(b) S3b timeout outage with retry-on-timeout enabled (shows PSR gain + ambiguous-outcome retries)."""

from __future__ import annotations

import asyncio
import sys

import httpx

import exp1_scenarios as E
from common import CompetentRouter, SimTransport, make_registry, make_routes, mean_ci, route_one
from acquirer_sim.models import AuthorizeRequest, OutageBehavior

E.SCENARIOS.clear()
E.SCENARIOS["S8_weak_secondary_beta88_long_recovery"] = (
    {"acquirer_alpha": 0.95, "acquirer_beta": 0.88}, 500, 2.0,
    [(50, []), (50, E.out(True)), (200, E.out(False))])


async def s3b_retry_on_timeout(seed: int) -> dict:
    rates = E.A2
    reg = make_registry(rates, seed=seed, spike_ms=3000)
    tr = SimTransport(reg)
    async with httpx.AsyncClient(transport=tr, base_url="http://testserver") as client:
        r = CompetentRouter(client, make_routes(list(rates), 0.95), list(rates), 777 + seed, retry=True,
                            retry_on_timeout=True)
        outs = []
        for pi, (n, acts) in enumerate([(50, []), (50, E.out(True, OutageBehavior.LATENCY_SPIKE)), (50, E.out(False))]):
            E.apply(reg, acts)
            for i in range(n):
                outs.append(await route_one(r, AuthorizeRequest(transaction_id=f"t{pi}_{i}", amount=50.0)))
    return {"psr": sum(o.authorized for o in outs) / len(outs), "ambig": r.ambiguous_retries}


async def main(n: int) -> None:
    seeds = list(range(1, n + 1))
    for scn in E.SCENARIOS:
        print(f"=== {scn} (n={n}) ===")
        for kind in E.ROUTERS:
            rs = [await E.run_one(scn, kind, s) for s in seeds]
            m, h = mean_ci([r["psr"] * 100 for r in rs])
            ra = mean_ci([r["recovery_alpha_share"] * 100 for r in rs])
            print(f"{kind:<14} PSR {m:.2f}+-{h:.2f}  alpha share of first attempts in last 30 recovery tx: "
                  f"{ra[0]:.0f}+-{ra[1]:.0f}%")
    rs = [await s3b_retry_on_timeout(s) for s in seeds]
    m, h = mean_ci([r["psr"] * 100 for r in rs])
    print(f"=== S3b timeout outage, comp_retry WITH retry-on-timeout (n={n}) === PSR {m:.2f}+-{h:.2f}; "
          f"ambiguous (post-timeout) retries total={sum(r['ambig'] for r in rs)} "
          f"(= {sum(r['ambig'] for r in rs) / n:.1f}/run; each is a duplicate-charge risk if the first auth actually succeeded)")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 40))
```

</details>

<details><summary><code>exp2_decline_noise.py</code></summary>

```python
"""Task 2: failure attribution under realistic, CORRELATED issuer-decline noise.

Card model (replaces the repo simulator's independent per-acquirer declines):
  - Each transaction has one issuer decision U_tx shared by all acquirers (same card -> same issuer answer).
    issuer_ok = U_tx < p_issuer(phase).
  - Each acquirer has a technical success prob t_a; technical failure -> HTTP 200 DECLINED, code SYSTEM_ERROR.
  - authorized = issuer_ok and tech_ok. Issuer decline -> DO_NOT_HONOR.
Scenarios:
  T2a identical health: t=.995/.995, p_issuer=.88 for 2000 tx.
  T2b identical health + low-quality burst: 500 tx @.88, 200 tx @.50 (card-testing / prepaid surge), 500 tx @.88.
  T2c small real difference: t_alpha=.995, t_beta=.970, p_issuer=.88, 2000 tx (oracle: always alpha).
"""

from __future__ import annotations

import asyncio
import hashlib
import sys
import time

import httpx
import numpy as np

from common import make_router, mean_ci, route_one
from acquirer_sim.models import AuthorizeRequest, AuthorizeResponse

IDS = ["acquirer_alpha", "acquirer_beta"]
ROUTERS = ["static_M3", "loom_bench", "loom_live", "comp_noretry", "comp_tech"]


class CardTransport(httpx.AsyncBaseTransport):
    def __init__(self, tech: dict[str, float], seed: int) -> None:
        self.tech = tech
        self.p_issuer = 0.88
        self.rng = np.random.default_rng(seed)
        self.seed = seed

    def u_tx(self, tx: str) -> float:
        h = hashlib.blake2b(f"{self.seed}:{tx}".encode(), digest_size=8).digest()
        return int.from_bytes(h, "big") / 2**64

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        acq = request.url.path.strip("/").split("/")[1]
        body = AuthorizeRequest.model_validate_json(request.content)
        issuer_ok = self.u_tx(body.transaction_id) < self.p_issuer
        tech_ok = self.rng.random() < self.tech[acq]
        auth = issuer_ok and tech_ok
        code = None if auth else ("SYSTEM_ERROR" if not tech_ok else "DO_NOT_HONOR")
        p = AuthorizeResponse(transaction_id=body.transaction_id, acquirer_id=acq,
                              status="AUTHORIZED" if auth else "DECLINED", authorized=auth,
                              authorization_code="AUTH_X" if auth else None, decline_code=code,
                              decline_message=None, simulated_latency_ms=0.0, timestamp=time.time())
        return httpx.Response(200, content=p.model_dump_json().encode(),
                              headers={"content-type": "application/json"}, request=request)


SCN = {
    "T2a_identical": ({"acquirer_alpha": .995, "acquirer_beta": .995}, [(2000, .88)]),
    "T2b_identical_lowquality_burst": ({"acquirer_alpha": .995, "acquirer_beta": .995}, [(500, .88), (200, .50), (500, .88)]),
    "T2c_beta_2.5pp_worse_tech": ({"acquirer_alpha": .995, "acquirer_beta": .970}, [(2000, .88)]),
}


def episodes_below(ws: list[float], thr: float = 0.10, min_len: int = 20) -> int:
    n = run = 0
    for w in ws:
        if w <= thr:
            run += 1
            if run == min_len:
                n += 1
        else:
            run = 0
    return n


async def run(scn: str, kind: str, seed: int) -> dict:
    tech, phases = SCN[scn]
    tr = CardTransport(tech, seed)
    async with httpx.AsyncClient(transport=tr, base_url="http://testserver") as client:
        r = make_router(kind, client, IDS, seed=777 + seed)
        outs = []
        trips = 0
        phase_of = []
        for pi, (n, p) in enumerate(phases):
            tr.p_issuer = p
            for i in range(n):
                o = await route_one(r, AuthorizeRequest(transaction_id=f"{pi}_{i}", amount=50.0))
                outs.append(o)
                phase_of.append(pi)
                if kind == "static_M3":
                    trips += sum(1 for st in r._route_states.values() if st.tripped_at_tx == r._global_tx_counter)
                elif kind.startswith("comp"):
                    trips += sum(1 for a in IDS if r.open_at[a] == r.tx)
    res: dict = {
        "psr": sum(o.authorized for o in outs) / len(outs),
        "share_alpha": sum(o.first == "acquirer_alpha" for o in outs) / len(outs),
        "breaker_trips": trips,
        "switches": sum(1 for k in range(1, len(outs)) if outs[k].first != outs[k - 1].first),
    }
    if kind.startswith("loom"):
        wa = [o.alloc["acquirer_alpha"] for o in outs]
        wb = [o.alloc["acquirer_beta"] for o in outs]
        res["false_div_episodes"] = episodes_below(wa) + episodes_below(wb)
        # majority-flips: crossings of 0.5
        res["majority_flips"] = sum(1 for k in range(1, len(wa)) if (wa[k] >= .5) != (wa[k - 1] >= .5))
        res["frac_time_alpha_minority"] = sum(1 for w in wa if w < .5) / len(wa)
        if len(phases) > 1:
            pre = [wa[k] for k in range(len(wa)) if phase_of[k] == 0][-100:]
            bur = [wa[k] for k in range(len(wa)) if phase_of[k] == 1]
            res["burst_min_wa"] = min(bur)
            res["burst_max_wa"] = max(bur)
            res["pre_mean_wa"] = float(np.mean(pre))
        st = r.get_all_states()
        res["post_mean_gap"] = abs(st["acquirer_alpha"].expected_success_rate - st["acquirer_beta"].expected_success_rate)
    return res


async def main(n: int) -> None:
    seeds = list(range(1, n + 1))
    for scn in SCN:
        print(f"\n=== {scn} (n={n} seeds) ===")
        for kind in ROUTERS:
            rs = [await run(scn, kind, s) for s in seeds]
            parts = []
            for key, sc in [("psr", 100), ("share_alpha", 100), ("breaker_trips", 1), ("switches", 1),
                            ("false_div_episodes", 1), ("majority_flips", 1), ("frac_time_alpha_minority", 100),
                            ("pre_mean_wa", 1), ("burst_min_wa", 1), ("burst_max_wa", 1), ("post_mean_gap", 1)]:
                vals = [r[key] * sc for r in rs if key in r]
                if vals:
                    m, h = mean_ci(vals)
                    parts.append(f"{key}={m:.3g}+-{h:.2g}")
            print(f"{kind:<13} " + "  ".join(parts))


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 30))
```

</details>

<details><summary><code>exp3_tps_sweep.py</code></summary>

```python
"""Tasks 3+4: failures absorbed by the dead acquirer & wall-clock divert/recovery times vs TPS.

Poisson arrivals at TPS; 300-tx warmup; hard outage on alpha of D seconds; post-outage window.
Auth latency 300 ms (feedback is delayed -> in-flight decisions use stale beliefs).
Outage modes: fast (503 after 50 ms) or timeout (2 s).
Routers: Loom bench (decay .95, deficit), Loom live default (decay .98, stochastic),
breaker k=3 tech-only (30 s cooldown, 1 probe), breaker + 1 retry on tech failure.
"""

from __future__ import annotations

import math
import sys
import time

import numpy as np

from common import BENCH_PID, mean_ci
from des import ALPHA, BETA, BreakerCore, LoomCore, World, first_time, poisson_arrivals, simulate
from router_core.pid import PIDConfig

IDS = [ALPHA, BETA]


def make(kind: str, seed: int):
    if kind == "loom_bench":
        return LoomCore(IDS, 0.95, PIDConfig(**BENCH_PID), seed), False
    if kind == "loom_live":
        return LoomCore(IDS, 0.98, PIDConfig(), seed), False
    if kind == "breaker_k3":
        return BreakerCore(IDS, k=3, cooldown_s=30.0), False
    if kind == "breaker_k3_retry":
        return BreakerCore(IDS, k=3, cooldown_s=30.0), True
    raise ValueError(kind)


def run(kind: str, tps: float, dur: float, mode: str, seed: int, post_cap_tx: int = 5000) -> dict:
    rng = np.random.default_rng(10_000 + seed)
    t0 = 300 / tps
    t1 = t0 + dur
    t_end = t1 + min(600.0, post_cap_tx / tps)
    arr = poisson_arrivals(lambda t: tps, t_end, rng, tps)
    world = World(rates={ALPHA: .95, BETA: .94}, outage_mode=mode, t0=t0, t1=t1,
                  rng=np.random.default_rng(20_000 + seed))
    router, retry = make(kind, 777 + seed)
    r = simulate(router, world, arr, retry=retry)
    dt, wd = r["dec_t"], r["w_dead"]
    dead_dec = np.array([a == ALPHA for a in r["dec_a"]])
    if kind.startswith("loom"):
        divert = first_time(dt, wd <= 0.10, t0)
        detect = first_time(dt, wd <= 0.50, t0)
        recov = first_time(dt, wd >= 0.50, t1)
    else:
        divert = first_time(dt, ~dead_dec, t0)
        detect = divert
        recov = first_time(dt, dead_dec, t1)
    n_out_dec = int(((dt >= t0) & (dt < t1)).sum())
    return dict(fails=r["fails_dead_outage"], visible=r["visible_fail_outage"], divert=divert, detect=detect,
                recov=recov, n_out=n_out_dec, psr=r["psr"])


def fmt(vals, f="{:.1f}"):
    v = [x for x in vals if not math.isnan(x)]
    if not v:
        return "n/a"
    if len(v) < 2:
        return f.format(v[0])
    m, h = mean_ci(v)
    cens = len(vals) - len(v)
    return (f + "±" + f).format(m, h) + (f" ({cens} cens)" if cens else "")


def main() -> None:
    plan = [  # (tps, dur_s, mode, n_seeds)
        (0.5, 600, "fast", 30), (0.5, 60, "fast", 30),
        (15, 60, "fast", 30), (15, 600, "fast", 30), (15, 60, "timeout", 30),
        (100, 60, "fast", 30), (100, 600, "fast", 10), (100, 60, "timeout", 10),
        (1000, 60, "fast", 5), (1000, 60, "timeout", 5), (1000, 600, "fast", 3),
    ]
    if len(sys.argv) > 1 and sys.argv[1] == "quick":
        plan = [(1000, 60, "fast", 1)]
    kinds = ["loom_bench", "loom_live", "breaker_k3", "breaker_k3_retry"]
    print(f"{'TPS':>6} {'dur':>5} {'mode':>8} {'n':>3} {'router':<17}{'outage tx':>11}{'failed on dead (tx)':>24}"
          f"{'cust-visible fails':>22}{'detect s (w<=.5)':>20}{'divert s (w<=.1)':>22}{'recovery s':>22}")
    for tps, dur, mode, n in plan:
        for kind in kinds:
            t_s = time.time()
            rs = [run(kind, tps, dur, mode, s) for s in range(1, n + 1)]
            print(f"{tps:>6} {dur:>5} {mode:>8} {n:>3} {kind:<17}{np.mean([r['n_out'] for r in rs]):>11.0f}"
                  f"{fmt([r['fails'] for r in rs]):>24}{fmt([r['visible'] for r in rs]):>22}"
                  f"{fmt([r['detect'] for r in rs], '{:.2f}'):>20}{fmt([r['divert'] for r in rs], '{:.2f}'):>22}"
                  f"{fmt([r['recov'] for r in rs], '{:.1f}'):>22}   [{time.time() - t_s:.0f}s]", flush=True)


if __name__ == "__main__":
    main()
```

</details>

<details><summary><code>exp4_capacity_bursty.py</code></summary>

```python
"""Task 3 (herd/capacity claim) + Task 4 (bursty traffic) DES experiments.

(A) Capacity: lambda=100 TPS, hard outage on alpha for 300 s (fast 503). Secondary capacity limited
    (sliding 1-s window); overflow -> HTTP 429 (fast reject) or 'collapse' (overload degrades all
    requests on that acquirer). Topologies: 2 acquirers (beta cap 60 TPS) and 3 acquirers
    (beta cap 60, gamma cap 60 -> enough total capacity if split).
    Policies: Loom bench, Loom live, naive breaker (priority, 429 counts as tech failure, 1 retry),
    capacity-aware (tech breaker that ignores 429 + per-acquirer admission cap learned by AIMD +
    spill to next acquirer, shed when all full).
(B) Bursty: baseline 1 TPS with 20-s bursts at 30 TPS every 120 s; outage (fast) of 600 s starting at a
    random time; Loom vs breaker k=3: failures on dead acquirer and divert time.
"""

from __future__ import annotations

import heapq
import math
import sys

import numpy as np

from common import BENCH_PID, mean_ci
from des import BreakerCore, LoomCore, first_time, poisson_arrivals
from router_core.pid import PIDConfig

A, B, G = "acquirer_alpha", "acquirer_beta", "acquirer_gamma"


class CapWorld:
    def __init__(self, rates, caps, t0, t1, collapse, seed):
        self.rates, self.caps, self.t0, self.t1, self.collapse = rates, caps, t0, t1, collapse
        self.rng = np.random.default_rng(seed)
        self.win = {a: [] for a in rates}

    def attempt(self, a, now):
        """-> (latency, ok, tech, code)"""
        if a == A and self.t0 <= now < self.t1:
            return 0.05, False, True, "503"
        cap = self.caps.get(a)
        if cap is not None:
            w = self.win[a]
            while w and w[0] < now - 1.0:
                w.pop(0)
            if not self.collapse:
                # rate limiter: admits up to `cap` requests per sliding second, rejects the rest (429)
                if len(w) >= cap:
                    return 0.02, False, True, "429"
                w.append(now)
            else:
                w.append(now)  # collapse: offered load (incl. overflow) degrades everyone
            load = len(w)
            if self.collapse and load > cap:
                p = self.rates[a] * max(0.0, 1.0 - (load - cap) / cap)
                ok = self.rng.random() < p
                return 0.9, ok, not ok, "ok" if ok else "504"
        ok = self.rng.random() < self.rates[a]
        return 0.3, ok, False, "ok" if ok else "issuer"


class CapAwareCore:
    """Tech breaker ignoring 429 + AIMD admission cap per acquirer + spill/shed."""

    def __init__(self, ids):
        self.ids = ids
        self.br = BreakerCore(ids, k=3, cooldown_s=30.0)
        self.cap_est = {a: 1e9 for a in ids}
        self.adm = {a: [] for a in ids}

    def decide(self, now, exclude=None):
        exclude = exclude or set()
        for a in self.ids:
            if a in exclude or not (self.br.open_until[a] is None or (now >= self.br.open_until[a] and not self.br.probe_inflight[a])):
                continue
            w = self.adm[a]
            while w and w[0] < now - 1.0:
                w.pop(0)
            if len(w) < self.cap_est[a]:
                if self.br.open_until[a] is not None:
                    self.br.probe_inflight[a] = True
                w.append(now)
                return a
        return None  # shed

    def feedback(self, a, ok, tech, now, code="ok"):
        if code in ("429", "504"):  # overload signals -> capacity control, not health
            self.cap_est[a] = max(1.0, min(self.cap_est[a], len(self.adm[a])) * 0.9)
            return
        if self.cap_est[a] < 1e9:
            self.cap_est[a] += 0.05  # additive increase per success/decline
        self.br.feedback(a, ok, tech, now)

    def weight(self, a):
        return 0.0


def sim(router, world, arrivals, retry):
    ev, seq = [], 0
    for t in arrivals:
        heapq.heappush(ev, (t, seq, "arr", None)); seq += 1
    n_out = ok_out = shed = 0
    while ev:
        now, _, kind, p = heapq.heappop(ev)
        if kind == "arr":
            a = router.decide(now)
            if a is None:
                if world.t0 <= now < world.t1:
                    n_out += 1; shed += 1
                continue
            lat, ok, tech, code = world.attempt(a, now)
            heapq.heappush(ev, (now + lat, seq, "done", (a, ok, tech, code, now, 0))); seq += 1
        else:
            a, ok, tech, code, t_arr, tries = p
            if isinstance(router, CapAwareCore):
                router.feedback(a, ok, tech, now, code)
            else:
                router.feedback(a, ok, tech, now)
            if retry and tech and tries == 0:
                b = router.decide(now, exclude={a})
                if b is not None:
                    lat, ok2, tech2, code2 = world.attempt(b, now)
                    heapq.heappush(ev, (now + lat, seq, "done", (b, ok2, tech2, code2, t_arr, 1))); seq += 1
                    continue
            if world.t0 <= t_arr < world.t1:
                n_out += 1; ok_out += ok
    return ok_out / max(1, n_out), shed


class RawThompsonCore(LoomCore):
    """Loom WITHOUT the PID layer (router.py pid_config=None path): argmax of Thompson samples."""

    def decide(self, now=0.0, exclude=None):
        samples = self.reg.sample_all(rng=self.rng)
        return max(samples.keys(), key=lambda a: (samples[a], a))


def make(kind, ids, seed):
    if kind == "loom_nopid_d95":
        return RawThompsonCore(ids, 0.95, PIDConfig(**BENCH_PID), seed), False
    if kind == "loom_bench":
        return LoomCore(ids, 0.95, PIDConfig(**BENCH_PID), seed), False
    if kind == "loom_live":
        return LoomCore(ids, 0.98, PIDConfig(), seed), False
    if kind == "naive_breaker_retry":
        return BreakerCore(ids, k=3, cooldown_s=30.0), True
    if kind == "cap_aware_retry":
        return CapAwareCore(ids), True
    raise ValueError(kind)


def part_a(n):
    print("(A) CAPACITY: lambda=100 TPS, alpha hard outage 300 s; PSR of transactions arriving during outage")
    print("    (upper bound if capacity used perfectly: 2-acq = 60/100*0.94 = 56.4%; 3-acq = all routed -> ~93.5%)")
    for topo in ("2acq_beta60", "3acq_beta60_gamma60"):
        if topo == "2acq_beta60":
            rates, caps = {A: .95, B: .94}, {B: 60}
        else:
            rates, caps = {A: .95, B: .94, G: .93}, {B: 60, G: 60}
        ids = list(rates)
        for collapse in (False, True):
            for kind in ("loom_bench", "loom_live", "loom_nopid_d95", "naive_breaker_retry", "cap_aware_retry"):
                vals, sheds = [], []
                for s in range(1, n + 1):
                    rng = np.random.default_rng(500 + s)
                    t0, t1 = 30.0, 330.0
                    arr = poisson_arrivals(lambda t: 100.0, 360.0, rng, 100.0)
                    r, retry = make(kind, ids, 777 + s)
                    psr, shed = sim(r, CapWorld(rates, caps, t0, t1, collapse, 900 + s), arr, retry)
                    vals.append(psr * 100); sheds.append(shed)
                m, h = mean_ci(vals)
                print(f"  {topo:<22} {'collapse' if collapse else '429-reject':<11} {kind:<20} outage PSR {m:6.2f}±{h:.2f}%"
                      f"  shed/run {np.mean(sheds):.0f}")


def part_b(n):
    print("(B) BURSTY: 1 TPS base, 30 TPS bursts 20 s every 120 s; 600-s fast outage at random start")

    def rate(t):
        return 30.0 if (t % 120.0) < 20.0 else 1.0
    for kind in ("loom_bench", "loom_live", "breaker_k3"):
        fails, divs = [], []
        for s in range(1, n + 1):
            rng = np.random.default_rng(700 + s)
            t0 = 300.0 + rng.uniform(0, 120)
            t1 = t0 + 600.0
            arr = poisson_arrivals(rate, t1 + 60, rng, 30.0)
            from des import World, simulate, ALPHA, BETA
            if kind == "breaker_k3":
                r = BreakerCore([ALPHA, BETA], k=3, cooldown_s=30.0)
            else:
                r = make(kind, [ALPHA, BETA], 777 + s)[0]
            w = World(rates={ALPHA: .95, BETA: .94}, t0=t0, t1=t1, rng=np.random.default_rng(800 + s))
            out = simulate(r, w, arr)
            fails.append(out["fails_dead_outage"])
            dt = out["dec_t"]
            if kind == "breaker_k3":
                cond = np.array([a != ALPHA for a in out["dec_a"]])
            else:
                cond = out["w_dead"] <= 0.10
            divs.append(first_time(dt, cond, t0))
        fm, fh = mean_ci(fails)
        dm, dh = mean_ci(divs)
        print(f"  {kind:<12} failed-on-dead per outage {fm:.1f}±{fh:.1f}   divert time {dm:.1f}±{dh:.1f} s "
              f"(min {min(divs):.1f}, max {max(divs):.1f})")


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    part_a(n)
    if len(sys.argv) < 3:
        part_b(30)
```

</details>

<details><summary><code>exp5_steady_state.py</code></summary>

```python
"""Steady-state regret (no outage): how much traffic does each router send to the WORSE acquirer when
acquirers differ modestly? Repo simulator (independent per-acquirer declines), 2000 tx, alpha listed first.
PSR loss reported vs oracle 'always best acquirer' (= best rate)."""

from __future__ import annotations

import asyncio
import sys

import httpx

from common import SimTransport, make_registry, make_router, mean_ci, route_one
from acquirer_sim.models import AuthorizeRequest

PAIRS = [(0.95, 0.94), (0.95, 0.92), (0.95, 0.90), (0.90, 0.85), (0.92, 0.95)]
ROUTERS = ["static_M3", "loom_bench", "loom_live", "comp_noretry"]


async def run(kind, ra, rb, seed, n=2000):
    rates = {"acquirer_alpha": ra, "acquirer_beta": rb}
    reg = make_registry(rates, seed=seed)
    async with httpx.AsyncClient(transport=SimTransport(reg), base_url="http://testserver") as c:
        r = make_router(kind, c, list(rates), seed=777 + seed)
        outs = [await route_one(r, AuthorizeRequest(transaction_id=f"s{i}", amount=50.0)) for i in range(n)]
    best = "acquirer_alpha" if ra >= rb else "acquirer_beta"
    share_worse = sum(o.first != best for o in outs) / n
    exp_psr = sum(rates[o.first] for o in outs) / n  # expected PSR given routing (removes outcome noise)
    return share_worse, (max(ra, rb) - exp_psr) * 100


async def main(ns):
    print(f"n_seeds={ns}, 2000 tx each; loss = oracle best rate - expected PSR of routing decisions (pp)")
    for ra, rb in PAIRS:
        for kind in ROUTERS:
            rs = [await run(kind, ra, rb, s) for s in range(1, ns + 1)]
            sw = mean_ci([x[0] * 100 for x in rs])
            lo = mean_ci([x[1] for x in rs])
            print(f"alpha={ra:.2f} beta={rb:.2f} {kind:<13} share to worse acquirer {sw[0]:5.1f}±{sw[1]:.1f}%   "
                  f"PSR loss vs oracle {lo[0]:.3f}±{lo[1]:.3f} pp")


if __name__ == "__main__":
    asyncio.run(main(int(sys.argv[1]) if len(sys.argv) > 1 else 30))
```

</details>

<details><summary><code>exp5b_longwindow.py</code></summary>

```python
"""exp5b: steady-state share-to-worse for a long-memory competent router (window 1000, eps 2%, margin 0.5pp)."""
import asyncio, httpx
from common import CompetentRouter, SimTransport, make_registry, make_routes, mean_ci, route_one
from acquirer_sim.models import AuthorizeRequest
async def run(ra, rb, seed, n=2000):
    rates = {"acquirer_alpha": ra, "acquirer_beta": rb}
    reg = make_registry(rates, seed=seed)
    async with httpx.AsyncClient(transport=SimTransport(reg), base_url="http://testserver") as c:
        r = CompetentRouter(c, make_routes(list(rates), .95), list(rates), 777+seed, retry=False, window=1000, eps=0.02, margin=0.005)
        outs = [await route_one(r, AuthorizeRequest(transaction_id=f"s{i}", amount=50.0)) for i in range(n)]
    best = "acquirer_alpha" if ra >= rb else "acquirer_beta"
    return sum(o.first != best for o in outs)/n
async def main():
    for ra, rb in [(0.95,0.94),(0.95,0.92),(0.92,0.95)]:
        xs = [await run(ra, rb, s)*100 for s in range(1,31)]
        m,h = mean_ci(xs)
        print(f"alpha={ra} beta={rb} comp_longwindow share to worse {m:.1f}+-{h:.1f}% (n=30, 2000 tx)")
asyncio.run(main())
```

</details>

<details><summary><code>exp5c_horizon.py</code></summary>

```python
"""exp5c: does Loom's steady-state share-to-worse shrink with horizon? (DES core = verified replica of route()).
alpha .95 vs beta .94 (1pp) and .95 vs .92 (3pp); 50k sequential tx; share to worse in the LAST 10k tx.
Compare Loom live (decay .98), Loom bench (decay .95), and the same Loom pipeline with decay .9999 (near no forgetting)."""
import numpy as np
from common import BENCH_PID, mean_ci
from des import LoomCore
from router_core.pid import PIDConfig
IDS = ["acquirer_alpha", "acquirer_beta"]
def run(decay, pid, ra, rb, seed, n=50000):
    core = LoomCore(IDS, decay, pid, 777 + seed)
    rng = np.random.default_rng(seed)
    rates = {"acquirer_alpha": ra, "acquirer_beta": rb}
    worse = 0
    for i in range(n):
        a = core.decide()
        core.feedback(a, rng.random() < rates[a], False, 0.0)
        if i >= n - 10000 and a == "acquirer_beta":
            worse += 1
    return worse / 10000
for ra, rb in [(0.95, 0.94), (0.95, 0.92)]:
    for name, decay, pid in [("loom_live d.98 stoch", 0.98, PIDConfig()), ("loom_bench d.95 deficit", 0.95, PIDConfig(**BENCH_PID)),
                             ("loom pipeline d.9999 stoch", 0.9999, PIDConfig())]:
        xs = [run(decay, pid, ra, rb, s) * 100 for s in range(1, 11)]
        m, h = mean_ci(xs)
        print(f"alpha={ra} beta={rb} {name:<28} share to worse in tx 40k-50k: {m:.1f}+-{h:.1f}% (n=10)", flush=True)
```

</details>

<details><summary><code>hotpath_checks.py</code></summary>

```python
"""Task 5 hot-path checks (in-process, fakeredis):
 (1) Redis round-trips per routed transaction with RedisBanditStateRegistry (3 acquirers).
 (2) WATCH/MULTI contention: N threads ('replicas') updating the same acquirer -> WatchError escapes after 5 retries?
 (3) Failure semantics: state-update exception AFTER acquirer authorized -> client gets HTTP 500 for an authorized payment.
 (4) Unclassified transport error (httpx.RemoteProtocolError) -> HTTP 500, no state update, no fallback acquirer.
"""
import asyncio, threading, time, uuid, logging
import fakeredis, httpx, redis
from acquirer_sim.app import create_app
from acquirer_sim.models import LatencyConfig
from router_core.app import create_router_app
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig
from router_core.router import BanditRouter
from router_core.bandit import BanditStateRegistry
from router_core.state import AcquirerStateConfig
from data_layer.redis_state import RedisBanditStateRegistry, RedisStateStore
logging.disable(logging.CRITICAL)
IDS = ["acquirer_alpha", "acquirer_beta", "acquirer_gamma"]

# ---- (1) round trips
rt = {"n": 0}
import fakeredis._connection as _fc
orig = _fc.FakeRedisConnection.read_response
def counting(self, *a, **k):
    rt["n"] += 1
    return orig(self, *a, **k)
_fc.FakeRedisConnection.read_response = counting
rt["sends"] = 0
orig_send = _fc.FakeRedisConnection.send_packed_command
def counting_send(self, *a, **k):
    rt["sends"] += 1
    return orig_send(self, *a, **k)
_fc.FakeRedisConnection.send_packed_command = counting_send

def routes(base="http://sim"):
    return [AcquirerRouteConfig(acquirer_id=a, base_url=base, state_config=AcquirerStateConfig(decay_factor=0.98)) for a in IDS]

async def part1():
    server = fakeredis.FakeServer()
    r = fakeredis.FakeRedis(server=server)
    reg = RedisBanditStateRegistry(redis_client=r)
    sim = create_app(default_acquirers=IDS, default_latency=LatencyConfig(base_ms=0, jitter_ms=0), seed=1)
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=sim), base_url="http://sim")
    br = BanditRouter(RouterConfig(routes=routes(), pid_config=PIDConfig(), seed=1), http_client=c, registry=reg)
    from acquirer_sim.models import AuthorizeRequest
    await br.route(AuthorizeRequest(transaction_id="w", amount=1.0))
    rt["n"] = 0; rt["sends"] = 0
    N = 200
    for i in range(N):
        await br.route(AuthorizeRequest(transaction_id=f"t{i}", amount=1.0))
    print(f"(1) per routed tx (3 acquirers): Redis replies={rt['n']/N:.1f}, network round-trips (send_packed_command)={rt['sends']/N:.1f} "
          f"-> at 0.3 ms RTT ~{0.3*rt['sends']/N:.1f} ms of SYNCHRONOUS (event-loop-blocking) Redis I/O per tx")
    return server

def part2(server):
    store = RedisStateStore(redis_client=fakeredis.FakeRedis(server=server))
    cfg = AcquirerStateConfig(decay_factor=0.98)
    errs, ok = [0], [0]
    def worker():
        st = RedisStateStore(redis_client=fakeredis.FakeRedis(server=server))
        for _ in range(300):
            try:
                st.record_outcome("acquirer_alpha", cfg, True)
                ok[0] += 1
            except redis.WatchError:
                errs[0] += 1
    ths = [threading.Thread(target=worker) for _ in range(16)]
    t0 = time.time(); [t.start() for t in ths]; [t.join() for t in ths]
    print(f"(2) 16 threads x 300 record_outcome on one key: ok={ok[0]} WatchError escaped (after 5 retries)={errs[0]} "
          f"in {time.time()-t0:.1f}s")

class ExplodingRegistry(BanditStateRegistry):
    def record_outcome(self, *a, **k):
        raise redis.WatchError("simulated contention after 5 retries")

async def part3():
    sim = create_app(default_acquirers=IDS, default_latency=LatencyConfig(base_ms=0, jitter_ms=0), seed=1)
    for a in IDS:
        sim.state.registry.get(a).set_success_rate(1.0)
    c = httpx.AsyncClient(transport=httpx.ASGITransport(app=sim), base_url="http://sim")
    br = BanditRouter(RouterConfig(routes=routes(), pid_config=PIDConfig(), seed=1), http_client=c, registry=ExplodingRegistry())
    app = create_router_app(router=br)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://r") as rc:
        resp = await rc.post("/route", json={"transaction_id": "dup_risk_1", "amount": 99.0})
    tel = {a: s.authorized_count for a, s in sim.state.registry.get_all_telemetry().items()}
    print(f"(3) state-update failure after dispatch: client HTTP {resp.status_code}; acquirer authorized_count={tel} "
          f"-> merchant sees an error for a payment that WAS authorized (retry => duplicate auth risk)")

class ProtoErrTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request):
        raise httpx.RemoteProtocolError("peer closed connection without sending complete message body", request=request)

async def part4():
    c = httpx.AsyncClient(transport=ProtoErrTransport())
    reg = BanditStateRegistry()
    br = BanditRouter(RouterConfig(routes=routes(), pid_config=PIDConfig(), seed=1), http_client=c, registry=reg)
    app = create_router_app(router=br)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app, raise_app_exceptions=False), base_url="http://r") as rc:
        resp = await rc.post("/route", json={"transaction_id": "p1", "amount": 5.0})
    tot = sum(s.total_count for s in reg.get_all_states().values())
    print(f"(4) httpx.RemoteProtocolError from acquirer: client HTTP {resp.status_code}; bandit observations recorded={tot} "
          f"(not caught: router only catches TimeoutException/NetworkError; no failover/fallback)")

async def main():
    server = await part1()
    part2(server)
    await part3()
    await part4()
asyncio.run(main())
```

</details>

<details><summary><code>inproc_ceiling.py</code></summary>

```python
"""Task 5: in-process CPU ceiling of the full router stack (FastAPI router app -> BanditRouter -> httpx ->
FastAPI sim app), no sockets, sim latency 0, logging disabled vs INFO-to-null. Measures CPU cost per tx."""
import asyncio, logging, time, uuid, sys
import httpx, numpy as np
from acquirer_sim.app import create_app
from acquirer_sim.models import LatencyConfig
from router_core.app import create_router_app
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig

async def run(conc, n_total, log_info):
    logging.getLogger().handlers.clear()
    if log_info:
        logging.basicConfig(level=logging.INFO, stream=open(__import__('os').devnull, 'w'), force=True)
    else:
        logging.basicConfig(level=logging.WARNING, force=True)
    sim = create_app(default_acquirers=["acquirer_alpha","acquirer_beta","acquirer_gamma"], default_latency=LatencyConfig(base_ms=0, jitter_ms=0), seed=1)
    sim_client = httpx.AsyncClient(transport=httpx.ASGITransport(app=sim), base_url="http://sim")
    routes = [AcquirerRouteConfig(acquirer_id=a, base_url="http://sim", state_config=AcquirerStateConfig(decay_factor=0.98)) for a in ["acquirer_alpha","acquirer_beta","acquirer_gamma"]]
    br = BanditRouter(RouterConfig(routes=routes, pid_config=PIDConfig()), http_client=sim_client)
    app = create_router_app(router=br)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://router") as c:
        q = list(range(n_total)); rl = []
        async def w():
            while q:
                q.pop()
                r = await c.post("/route", json={"transaction_id": uuid.uuid4().hex, "amount": 5.0})
                rl.append(r.json()["routing_latency_ms"])
        t0 = time.perf_counter(); c0 = time.process_time()
        await asyncio.gather(*[w() for _ in range(conc)])
        el = time.perf_counter() - t0; cpu = time.process_time() - c0
    print(f"inproc conc={conc:>3} log_info={log_info!s:<5} n={n_total} TPS={n_total/el:7.1f} CPU/tx={1e6*cpu/n_total:7.0f}us  routing_decision p50={np.percentile(rl,50)*1000:.0f}us p99={np.percentile(rl,99)*1000:.0f}us", flush=True)

async def main():
    for log_info in (False, True):
        for conc in (1, 32):
            await run(conc, 3000, log_info)
asyncio.run(main())
```

</details>

<details><summary><code>loadgen.py</code></summary>

```python
"""Task 5: closed-loop asyncio load generator against the live router (POST /route).

Usage: python loadgen.py <router_url> <duration_s> <conc1,conc2,...> [label]
Reports per concurrency: achieved TPS, client-side p50/p95/p99 latency, HTTP error rate,
router-reported routing_latency_ms (decision only) and acquirer_latency_ms percentiles, status mix.
"""

from __future__ import annotations

import asyncio
import sys
import time
import uuid
from collections import Counter

import httpx
import numpy as np


async def worker(client, url, stop_at, lat, rlat, alat, codes, statuses, acqs):
    while time.perf_counter() < stop_at:
        body = {"transaction_id": f"lt_{uuid.uuid4().hex}", "amount": 42.0}
        t = time.perf_counter()
        try:
            r = await client.post(url, json=body, timeout=10.0)
            lat.append((time.perf_counter() - t) * 1000)
            codes[r.status_code] += 1
            if r.status_code == 200:
                j = r.json()
                rlat.append(j["routing_latency_ms"])
                alat.append(j["acquirer_latency_ms"])
                statuses[j["status"]] += 1
                acqs[j["selected_acquirer"]] += 1
        except Exception as e:  # noqa: BLE001
            lat.append((time.perf_counter() - t) * 1000)
            codes[type(e).__name__] += 1


async def run_level(base, conc, dur):
    limits = httpx.Limits(max_connections=conc + 10, max_keepalive_connections=conc + 10)
    async with httpx.AsyncClient(base_url=base, limits=limits) as client:
        # warm
        await client.post("/route", json={"transaction_id": f"warm_{uuid.uuid4().hex}", "amount": 1.0})
        lat, rlat, alat = [], [], []
        codes, statuses, acqs = Counter(), Counter(), Counter()
        t0 = time.perf_counter()
        stop_at = t0 + dur
        await asyncio.gather(*[worker(client, "/route", stop_at, lat, rlat, alat, codes, statuses, acqs)
                               for _ in range(conc)])
        el = time.perf_counter() - t0
    n = len(lat)
    p = lambda a, q: float(np.percentile(a, q)) if len(a) else float("nan")  # noqa: E731
    err = sum(v for k, v in codes.items() if k != 200)
    print(f"conc={conc:>4} n={n:>6} TPS={n / el:7.1f} | client ms p50={p(lat, 50):7.1f} p95={p(lat, 95):7.1f} "
          f"p99={p(lat, 99):7.1f} | routing_decision ms p50={p(rlat, 50):.3f} p99={p(rlat, 99):.3f} | "
          f"acquirer ms p50={p(alat, 50):.1f} p99={p(alat, 99):.1f} | non-200={err} ({100 * err / max(1, n):.2f}%) "
          f"codes={dict(codes)} status={dict(statuses)} acq={dict(acqs)}", flush=True)


async def main():
    base, dur, levels = sys.argv[1], float(sys.argv[2]), [int(x) for x in sys.argv[3].split(",")]
    label = sys.argv[4] if len(sys.argv) > 4 else ""
    print(f"--- {label} base={base} duration={dur}s per level ---")
    for c in levels:
        await run_level(base, c, dur)


if __name__ == "__main__":
    asyncio.run(main())
```

</details>

<details><summary><code>loadgen_sim.py</code></summary>

```python
"""Direct load against acquirer_sim (no router) to separate sim ceiling from router ceiling."""
import asyncio, sys, time, uuid
import httpx, numpy as np

async def main():
    base, dur, levels = sys.argv[1], float(sys.argv[2]), [int(x) for x in sys.argv[3].split(",")]
    for conc in levels:
        lat = []
        async with httpx.AsyncClient(base_url=base, limits=httpx.Limits(max_connections=conc+10, max_keepalive_connections=conc+10)) as c:
            stop = time.perf_counter() + dur
            async def w():
                while time.perf_counter() < stop:
                    t = time.perf_counter()
                    r = await c.post("/acquirers/acquirer_alpha/authorize", json={"transaction_id": uuid.uuid4().hex, "amount": 1.0}, timeout=10)
                    lat.append((time.perf_counter()-t)*1000)
            t0 = time.perf_counter()
            await asyncio.gather(*[w() for _ in range(conc)])
            el = time.perf_counter() - t0
        print(f"SIM direct conc={conc} TPS={len(lat)/el:.1f} p50={np.percentile(lat,50):.1f}ms p99={np.percentile(lat,99):.1f}ms", flush=True)
asyncio.run(main())
```

</details>

<details><summary><code>pool_coupling.py</code></summary>

```python
"""Task 5: shared httpx pool (max_connections=100) across acquirers. Alpha in LATENCY_SPIKE (1.8 s).
Does a slow acquirer cause PoolTimeout errors that are recorded as FAILURES of healthy acquirers?
Runs BanditRouter in-process (live default config, real pooled client) against sim on :18002."""
import asyncio, logging, sys, time, uuid
from collections import Counter
import httpx
from acquirer_sim.models import AuthorizeRequest
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig
logging.disable(logging.CRITICAL)
IDS = ["acquirer_alpha", "acquirer_beta", "acquirer_gamma"]
BASE = "http://127.0.0.1:18002"

async def main(conc, dur, spike):
    async with httpx.AsyncClient() as admin:
        await admin.post(f"{BASE}/acquirers/acquirer_alpha/admin/outage",
                         json={"active": spike, "behavior": "LATENCY_SPIKE"})
    routes = [AcquirerRouteConfig(acquirer_id=a, base_url=BASE, state_config=AcquirerStateConfig(decay_factor=0.98)) for a in IDS]
    br = BanditRouter(RouterConfig(routes=routes, pid_config=PIDConfig(), seed=3))
    await br.start()
    errs = Counter(); outcomes = Counter()
    stop = time.perf_counter() + dur
    async def w():
        while time.perf_counter() < stop:
            r = await br.route(AuthorizeRequest(transaction_id=uuid.uuid4().hex, amount=1.0))
            if r.status == "ERROR":
                kind = (r.error_message or "").split(":")[1].strip().split(" ")[0] if "Transport" in (r.error_message or "") else (r.error_message or "")[:20]
                errs[(r.selected_acquirer, kind)] += 1
            outcomes[(r.selected_acquirer, r.status, (r.response_payload.decline_code if r.response_payload else None))] += 1
    t0 = time.perf_counter()
    await asyncio.gather(*[w() for _ in range(conc)])
    el = time.perf_counter() - t0
    await br.close()
    n = sum(outcomes.values())
    print(f"spike={spike} conc={conc} n={n} TPS={n/el:.1f}")
    print("  ERROR outcomes recorded as acquirer failures:", dict(errs))
    print("  outcome mix:", dict(outcomes))
    print("  final posteriors:", {a: round(s.expected_success_rate, 3) for a, s in br.get_all_states().items()})
    async with httpx.AsyncClient() as admin:
        await admin.post(f"{BASE}/acquirers/acquirer_alpha/admin/outage", json={"active": False})

asyncio.run(main(int(sys.argv[1]), float(sys.argv[2]), sys.argv[3] == "1"))
```

</details>

<details><summary><code>revenue_model.py</code></summary>

```python
"""Task 9: back-of-envelope annual lost-GMV model. ALL inputs are assumptions (see ASSUMPTIONS) except the
per-policy behaviour coefficients, which are taken from this audit's simulations (exp1/exp3/exp5) and are
therefore simulator-bound.

Lost GMV = failed-but-recoverable transactions x ticket x (1 - customer/merchant retry recovery).
Compared against an oracle that always uses the best healthy acquirer.
"""

from __future__ import annotations

SECONDS_PER_YEAR = 365 * 24 * 3600

BASE = dict(
    gmv=1e9,               # $/yr through this routing slice                    [ASSUMPTION]
    ticket=40.0,           # $ avg ticket                                       [ASSUMPTION]
    auth=0.90,             # healthy primary auth rate                          [ASSUMPTION, typical card-not-present 85-95%: DOMAIN KNOWLEDGE med]
    recovery=0.5,          # share of failed payments recovered by shopper/merchant retry [ASSUMPTION, low confidence]
    outage_h=4.0,          # hard-outage hours/yr on primary (99.95% availability) [ASSUMPTION, med-low]
    outage_event_min=30.0, # mean outage duration (min)                         [ASSUMPTION]
    gray_h=20.0,           # gray-failure hours/yr on primary                    [ASSUMPTION, low]
    gray_drop=0.10,        # auth-rate drop during gray failure                  [ASSUMPTION]
    delta=0.01,            # steady-state auth gap primary-secondary (primary better) [ASSUMPTION; 0 => identical]
    timeout_share=0.5,     # share of outages that present as timeouts (2 s)     [ASSUMPTION]
)

# Behaviour coefficients from simulations (simulator-bound):
#  Loom live: ~10 tx ramp per outage + 3% floor of outage volume to dead acquirer   (exp1 S6, exp3)
#  Loom steady-state share to worse acquirer: 41% at 1pp gap, 27% at 3pp (exp5, live config); ~0% at 0 gap
#  static (repo, M=3, cooldown 30 TX, probe): 3 + 1/30 of outage volume (exp1 S6)  ; gray: ~80% stays on degraded
#  competent (tech-only breaker, wall-clock 30 s cooldown, 1 retry on fast tech failure): ~0 visible on fast-fail
#    outages, in-flight lambda*2s on timeout outages (not retried: idempotency); steady share-to-worse 0.065 at 1pp
#    -> uses 0.065 = Loom pipeline with decay .9999 (long memory) at 1pp gap, exp5c; outage handling by breaker


def loom_share_worse(delta: float) -> float:
    if delta <= 0:
        return 0.0
    return 0.41 if delta <= 0.015 else 0.27 if delta <= 0.04 else 0.19


def lost(policy: str, p: dict) -> dict:
    tx_yr = p["gmv"] / p["ticket"]
    tps = tx_yr / SECONDS_PER_YEAR
    events = p["outage_h"] * 60 / p["outage_event_min"]
    out_tx = tps * p["outage_h"] * 3600
    unit = p["ticket"] * (1 - p["recovery"])  # $ lost per failed recoverable tx
    # (1) hard outages: failed tx that would otherwise have been authorized at ~auth
    if policy == "no_failover":
        f_out = out_tx
    elif policy == "static_repo":
        f_out = events * 3 + out_tx / 30 + events * tps * p["timeout_share"] * 2.0
    elif policy == "loom":
        f_out = events * 10 + 0.03 * out_tx + events * tps * p["timeout_share"] * 2.0
    elif policy == "competent":
        f_out = events * tps * p["timeout_share"] * 2.0
    else:
        raise ValueError(policy)
    f_out *= p["auth"]
    # (2) gray failures: excess declines vs oracle (switch to secondary, which is worse by delta)
    gray_tx = tps * p["gray_h"] * 3600
    excess = max(0.0, p["gray_drop"] - p["delta"])
    share_on_degraded = {"no_failover": 1.0, "static_repo": 0.8, "loom": 0.15, "competent": 0.10}[policy]
    f_gray = gray_tx * excess * share_on_degraded
    # (3) steady state regret (all healthy time): share of traffic to worse acquirer x delta
    healthy_tx = tx_yr - out_tx - gray_tx
    share_worse = {"no_failover": 0.0, "static_repo": 0.0, "loom": loom_share_worse(p["delta"]),
                   "competent": 0.065 if p["delta"] > 0 else 0.0}[policy]
    f_steady = healthy_tx * share_worse * p["delta"]
    return dict(tps=tps, outage=f_out * unit, gray=f_gray * unit, steady=f_steady * unit,
                total=(f_out + f_gray + f_steady) * unit)


def fmt(x: float) -> str:
    return f"${x / 1e6:,.2f}M" if abs(x) >= 1e5 else f"${x / 1e3:,.1f}k"


def main() -> None:
    print("BASE CASE:", BASE)
    for pol in ("no_failover", "static_repo", "loom", "competent"):
        r = lost(pol, BASE)
        print(f"  {pol:<12} avgTPS={r['tps']:.2f}  lost: outage={fmt(r['outage'])} gray={fmt(r['gray'])} "
              f"steady={fmt(r['steady'])}  TOTAL={fmt(r['total'])}")
    print("\nSENSITIVITY: annual lost GMV, Loom vs static_repo vs competent (delta = steady primary advantage)")
    print(f"{'GMV':>8} {'avgTPS':>7} {'outage h/yr':>11} {'delta':>6} {'gray h':>6} | {'static_repo':>12} {'loom':>12} {'competent':>12} | {'loom - static':>14}")
    for gmv in (1e8, 1e9, 1e10):
        for oh in (1, 4, 24):
            for delta in (0.0, 0.01):
                for gh in (0, 20):
                    p = dict(BASE, gmv=gmv, outage_h=oh, delta=delta, gray_h=gh)
                    s, l, c = (lost(k, p) for k in ("static_repo", "loom", "competent"))
                    print(f"{gmv / 1e6:>7.0f}M {s['tps']:>7.2f} {oh:>11} {delta:>6.2f} {gh:>6} | {fmt(s['total']):>12} "
                          f"{fmt(l['total']):>12} {fmt(c['total']):>12} | {fmt(l['total'] - s['total']):>14}")


if __name__ == "__main__":
    main()
```

</details>

<details><summary><code>sim_spike_server.py</code></summary>

```python
"""Sim server on :18002 with outage_spike_ms=1800 (LATENCY_SPIKE just under the router's 2.0 s timeout)."""
import uvicorn
from acquirer_sim.app import create_app
from acquirer_sim.models import LatencyConfig
app = create_app(default_acquirers=["acquirer_alpha", "acquirer_beta", "acquirer_gamma"],
                 default_latency=LatencyConfig(base_ms=20, jitter_ms=5, outage_spike_ms=1800))
uvicorn.run(app, host="127.0.0.1", port=18002, log_level="warning")
```

</details>

<details><summary><code>verify_des.py</code></summary>

```python
"""Verify LoomCore (des.py) reproduces BanditRouter.route() decisions exactly (sequential, 0 latency)."""

from __future__ import annotations

import asyncio

import httpx
import numpy as np

from common import BENCH_PID, SimTransport, make_registry, make_router
from des import LoomCore
from acquirer_sim.models import AuthorizeRequest
from router_core.pid import PIDConfig

IDS = ["acquirer_alpha", "acquirer_beta"]


async def via_router(kind: str, seed: int) -> list[str]:
    reg = make_registry({"acquirer_alpha": .95, "acquirer_beta": .94}, seed=seed)
    async with httpx.AsyncClient(transport=SimTransport(reg), base_url="http://testserver") as c:
        r = make_router(kind, c, IDS, seed=777 + seed)
        sel = []
        for i in range(150):
            if i == 50:
                reg.get("acquirer_alpha").set_outage(True)
            if i == 100:
                reg.get("acquirer_alpha").set_outage(False)
            res = await r.route(AuthorizeRequest(transaction_id=f"v{i}", amount=50.0))
            sel.append(res.selected_acquirer)
        return sel


def via_core(kind: str, seed: int) -> list[str]:
    reg = make_registry({"acquirer_alpha": .95, "acquirer_beta": .94}, seed=seed)
    if kind == "loom_bench":
        core = LoomCore(IDS, 0.95, PIDConfig(**BENCH_PID), 777 + seed)
    else:
        core = LoomCore(IDS, 0.98, PIDConfig(), 777 + seed)
    sel = []
    for i in range(150):
        if i == 50:
            reg.get("acquirer_alpha").set_outage(True)
        if i == 100:
            reg.get("acquirer_alpha").set_outage(False)
        a = core.decide()
        sim = reg.get(a)
        if sim.is_outage_active:
            ok = False
        else:
            ok = float(sim._rng.uniform(0.0, 1.0)) < sim.base_success_rate
        core.feedback(a, ok, False, 0.0)
        sel.append(a)
    return sel


async def main() -> None:
    for kind in ("loom_bench", "loom_live"):
        same = 0
        for s in range(1, 11):
            same += (await via_router(kind, s)) == via_core(kind, s)
        print(f"{kind}: identical decision sequences in {same}/10 seeds")


asyncio.run(main())
```

</details>



#### Re-verification pass scripts (2026-10-02)

`exp3_rv.py` is `exp3_tps_sweep.py` with the `quick` plan replaced by `[(0.5,600,'fast',10),(15,60,'fast',10),(15,600,'fast',5),(1000,600,'fast',1)]`.

<details><summary><code>cross.py</code></summary>

```python
import asyncio, logging, statistics as st
logging.disable(logging.CRITICAL)
ns={"__name__":"c"}; exec(open("../scripts/compare_psr.py").read(), ns)
import httpx
from acquirer_sim.app import create_app
from acquirer_sim.models import AuthorizeRequest, LatencyConfig, OutageToggleRequest
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig
async def run(seed, rseed, pid=True):
    sim=create_app(default_acquirers=["acquirer_alpha","acquirer_beta"], default_base_rate=0.95, default_latency=LatencyConfig(base_ms=0,jitter_ms=0), seed=seed)
    sim.state.registry.get("acquirer_beta").set_success_rate(0.94)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=sim), base_url="http://t") as c:
        routes=[AcquirerRouteConfig(acquirer_id=a, base_url="http://t", state_config=AcquirerStateConfig(decay_factor=0.95)) for a in ("acquirer_alpha","acquirer_beta")]
        r=BanditRouter(RouterConfig(routes=routes, pid_config=PIDConfig(actuation_mode="deficit") if pid else None, seed=rseed), http_client=c)
        w=[];sel=[]
        for i in range(150):
            if i in (50,100): await c.post("/acquirers/acquirer_alpha/admin/outage", json=OutageToggleRequest(active=(i==50)).model_dump())
            res=await r.route(AuthorizeRequest(transaction_id=f"t{i}", amount=50.0))
            w.append(res.smoothed_allocation["acquirer_alpha"]); sel.append(res.selected_acquirer)
    o=w[50:100]; cross=sum(1 for k in range(1,50) if (o[k]-0.5)*(o[k-1]-0.5)<0)
    flips=sum(1 for k in range(51,100) if sel[k]!=sel[k-1])
    rises=sum(1 for k in range(51,100) if w[k]>w[k-1]+1e-12); peak=max(w[50:100])-w[49]
    return cross, flips, rises, peak
r=asyncio.run(run(42,777)); print("seed42: w_A crossings of 0.5 in outage=%d dispatch flips=%d w_A rises=%d peak rise=%.3f"%r)
R=[asyncio.run(run(s*10+42,777+s)) for s in range(50)]
print("50 seeds: crossings mean=%.2f max=%d | dispatch flips mean=%.2f | rises mean=%.2f | peak rise mean=%.3f max=%.3f"%(st.mean(x[0] for x in R),max(x[0] for x in R),st.mean(x[1] for x in R),st.mean(x[2] for x in R),st.mean(x[3] for x in R),max(x[3] for x in R)))
RB=[asyncio.run(run(s*10+42,777+s,pid=False)) for s in range(50)]
print("raw bandit 50 seeds: dispatch flips mean=%.2f"%st.mean(x[1] for x in RB))
```

</details>

<details><summary><code>rawpid.py</code></summary>

```python
import asyncio, logging, statistics as st, math, httpx
logging.disable(logging.CRITICAL)
from acquirer_sim.app import create_app
from acquirer_sim.models import AuthorizeRequest, LatencyConfig, OutageToggleRequest
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.pid import PIDConfig
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig
async def run(seed, rseed, pid):
    sim=create_app(default_acquirers=["acquirer_alpha","acquirer_beta"], default_base_rate=0.95, default_latency=LatencyConfig(base_ms=0,jitter_ms=0), seed=seed)
    sim.state.registry.get("acquirer_beta").set_success_rate(0.94)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=sim), base_url="http://t") as c:
        routes=[AcquirerRouteConfig(acquirer_id=a, base_url="http://t", state_config=AcquirerStateConfig(decay_factor=0.95)) for a in ("acquirer_alpha","acquirer_beta")]
        r=BanditRouter(RouterConfig(routes=routes, pid_config=PIDConfig(actuation_mode="deficit") if pid else None, seed=rseed), http_client=c)
        ok=0
        for i in range(150):
            if i in (50,100): await c.post("/acquirers/acquirer_alpha/admin/outage", json=OutageToggleRequest(active=(i==50)).model_dump())
            ok+=(await r.route(AuthorizeRequest(transaction_id=f"t{i}", amount=50.0))).authorized
    return ok/1.5
N=100
d=[asyncio.run(run(s*10+42,777+s,True))-asyncio.run(run(s*10+42,777+s,False)) for s in range(N)]
se=st.stdev(d)/math.sqrt(N); print(f"PID - raw PSR: mean={st.mean(d):+.2f}pp CI[{st.mean(d)-1.96*se:+.2f},{st.mean(d)+1.96*se:+.2f}] PID wins {sum(x>0 for x in d)}/ties {sum(x==0 for x in d)}/{N}")
```

</details>

<details><summary><code>exp3_rv.py</code></summary>

```python
"""Tasks 3+4: failures absorbed by the dead acquirer & wall-clock divert/recovery times vs TPS.

Poisson arrivals at TPS; 300-tx warmup; hard outage on alpha of D seconds; post-outage window.
Auth latency 300 ms (feedback is delayed -> in-flight decisions use stale beliefs).
Outage modes: fast (503 after 50 ms) or timeout (2 s).
Routers: Loom bench (decay .95, deficit), Loom live default (decay .98, stochastic),
breaker k=3 tech-only (30 s cooldown, 1 probe), breaker + 1 retry on tech failure.
"""

from __future__ import annotations

import math
import sys
import time

import numpy as np

from common import BENCH_PID, mean_ci
from des import ALPHA, BETA, BreakerCore, LoomCore, World, first_time, poisson_arrivals, simulate
from router_core.pid import PIDConfig

IDS = [ALPHA, BETA]


def make(kind: str, seed: int):
    if kind == "loom_bench":
        return LoomCore(IDS, 0.95, PIDConfig(**BENCH_PID), seed), False
    if kind == "loom_live":
        return LoomCore(IDS, 0.98, PIDConfig(), seed), False
    if kind == "breaker_k3":
        return BreakerCore(IDS, k=3, cooldown_s=30.0), False
    if kind == "breaker_k3_retry":
        return BreakerCore(IDS, k=3, cooldown_s=30.0), True
    raise ValueError(kind)


def run(kind: str, tps: float, dur: float, mode: str, seed: int, post_cap_tx: int = 5000) -> dict:
    rng = np.random.default_rng(10_000 + seed)
    t0 = 300 / tps
    t1 = t0 + dur
    t_end = t1 + min(600.0, post_cap_tx / tps)
    arr = poisson_arrivals(lambda t: tps, t_end, rng, tps)
    world = World(rates={ALPHA: .95, BETA: .94}, outage_mode=mode, t0=t0, t1=t1,
                  rng=np.random.default_rng(20_000 + seed))
    router, retry = make(kind, 777 + seed)
    r = simulate(router, world, arr, retry=retry)
    dt, wd = r["dec_t"], r["w_dead"]
    dead_dec = np.array([a == ALPHA for a in r["dec_a"]])
    if kind.startswith("loom"):
        divert = first_time(dt, wd <= 0.10, t0)
        detect = first_time(dt, wd <= 0.50, t0)
        recov = first_time(dt, wd >= 0.50, t1)
    else:
        divert = first_time(dt, ~dead_dec, t0)
        detect = divert
        recov = first_time(dt, dead_dec, t1)
    n_out_dec = int(((dt >= t0) & (dt < t1)).sum())
    return dict(fails=r["fails_dead_outage"], visible=r["visible_fail_outage"], divert=divert, detect=detect,
                recov=recov, n_out=n_out_dec, psr=r["psr"])


def fmt(vals, f="{:.1f}"):
    v = [x for x in vals if not math.isnan(x)]
    if not v:
        return "n/a"
    if len(v) < 2:
        return f.format(v[0])
    m, h = mean_ci(v)
    cens = len(vals) - len(v)
    return (f + "±" + f).format(m, h) + (f" ({cens} cens)" if cens else "")


def main() -> None:
    plan = [  # (tps, dur_s, mode, n_seeds)
        (0.5, 600, "fast", 30), (0.5, 60, "fast", 30),
        (15, 60, "fast", 30), (15, 600, "fast", 30), (15, 60, "timeout", 30),
        (100, 60, "fast", 30), (100, 600, "fast", 10), (100, 60, "timeout", 10),
        (1000, 60, "fast", 5), (1000, 60, "timeout", 5), (1000, 600, "fast", 3),
    ]
    if len(sys.argv) > 1 and sys.argv[1] == "quick":
        plan = [(0.5, 600, "fast", 10), (15, 60, "fast", 10), (15, 600, "fast", 5), (1000, 600, "fast", 1)]
    kinds = ["loom_bench", "loom_live", "breaker_k3", "breaker_k3_retry"]
    print(f"{'TPS':>6} {'dur':>5} {'mode':>8} {'n':>3} {'router':<17}{'outage tx':>11}{'failed on dead (tx)':>24}"
          f"{'cust-visible fails':>22}{'detect s (w<=.5)':>20}{'divert s (w<=.1)':>22}{'recovery s':>22}")
    for tps, dur, mode, n in plan:
        for kind in kinds:
            t_s = time.time()
            rs = [run(kind, tps, dur, mode, s) for s in range(1, n + 1)]
            print(f"{tps:>6} {dur:>5} {mode:>8} {n:>3} {kind:<17}{np.mean([r['n_out'] for r in rs]):>11.0f}"
                  f"{fmt([r['fails'] for r in rs]):>24}{fmt([r['visible'] for r in rs]):>22}"
                  f"{fmt([r['detect'] for r in rs], '{:.2f}'):>20}{fmt([r['divert'] for r in rs], '{:.2f}'):>22}"
                  f"{fmt([r['recov'] for r in rs], '{:.1f}'):>22}   [{time.time() - t_s:.0f}s]", flush=True)


if __name__ == "__main__":
    main()
```

</details>

### A.5 Final `git status` of the original repository (re-run after re-verification)

```
$ git status
On branch main
Your branch is up to date with 'origin/main'.

Untracked files:
  (use "git add <file>..." to include in what will be committed)
	AUDIT.md

nothing added to commit but untracked files present (use "git add" to track)

$ git status --short
?? AUDIT.md
```
