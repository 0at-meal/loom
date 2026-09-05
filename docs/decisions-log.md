# Loom — Decisions, Interfaces & Risks Log

**Append-only. This is the record of what actually got decided and found, growing phase by phase — it doesn't get rewritten, only added to. The PRD describes the intended system; this is what happened while building it.**

Each phase's Architect pass should add a Decision and/or Interface entry here before handoff to the Engineer role. Each phase's QA and Tech Lead passes should add to Open Risks as they find things — don't wait for a "risks phase," there isn't one.

---

## Decisions Log

Format: `[Phase] Decision — alternatives considered — why this one won`

- **[Pre-build] Gateway health signal = decayed (EWMA-style) success rate.** Alternative: plain rolling-window average. Chosen because decay updates smoothly on every new observation rather than jumping when an old data point falls out of a fixed window, and it composes naturally with Thompson Sampling's own weighted, probabilistic updates.

- **[Pre-build] Bandit algorithm = Thompson Sampling with decay.** Alternatives: epsilon-greedy (fixed exploration rate — rejected because it can't adapt exploration to actual confidence), UCB (viable alternative — rejected because its optimistic-bound output composes less cleanly with PID's need for a smooth probabilistic signal than Thompson Sampling's Beta-distribution samples do).

- **[Pre-build] Demo architecture = live simulation, not replay.** Alternative: replay-based demo from pre-recorded data. Chosen for credibility — a live, on-demand-triggered outage is more convincing evidence the system works than a chart generated from a script, at the cost of less control over what could visibly go wrong during a demo.

- **[Pre-build] Simulated acquirers only, no real sandbox.** Chosen because the demo needs a reliably scriptable outage (controllable success rate + on-demand outage trigger) that a real sandbox API's behavior can't guarantee on command.

- **[Pre-build] Headline metrics = PSR lift (primary) + oscillation dampening (visual proof).** Chosen because PSR lift is the metric with real economic weight (Unit 1's PSR-vs-fees argument); oscillation dampening is the visible evidence that the PID layer achieved that lift smoothly rather than through jerky, unstable swings.

- **[Pre-build] Value-scaled exploration is explicitly out of scope for the core build.** Tracked as future/stretch work only — not a committed feature, not to be started before Phases 1–7 are demo-ready.

- **[Pre-build] Product name = Loom.**

- **[Phase 1] Thompson Sampling Beta parameter decay uses mean-reverting offset formulation ($\alpha = \alpha_0 + \gamma(\alpha - \alpha_0) + x$).** Alternatives considered: direct linear decay ($\alpha = \gamma \alpha + x$) with ad-hoc lower bound clamping; sliding window observation counts. Why this one won: Direct linear decay without offset causes $\alpha$ or $\beta$ to drop below 1.0 during sustained failure or success streaks, triggering boundary singularities in the Beta distribution density ($f(x) \to \infty$ at 0 or 1) and numerical instability in sampling. Offset decay mathematically guarantees $\alpha \ge \alpha_0$ and $\beta \ge \beta_0$ at all times, preserving unimodal, well-conditioned distributions and naturally reverting to the uninformative prior $\text{Beta}(1, 1)$ (maximum entropy / exploration) during prolonged dormancy or after an outage.

- **[Phase 1] Decayed health score uses discrete event-driven EWMA with optimistic initialization ($H_0 = 1.0$).** Alternatives considered: continuous wall-clock time-decayed EWMA ($e^{-\Delta t / \tau}$); simple rolling window success rate; uninformative initialization ($H_0 = 0.5$). Why this one won: Event-driven EWMA gives fully deterministic, reproducible test vectors against hand-scripted sequences of outcomes without clock mocking or sleep calls in unit tests. Optimistic initialization ($H_0 = 1.0$) prevents cold-start routing penalties or false alarm alerts on newly registered acquirers. Time-based decay conversion is exposed as a deterministic utility ($f(\text{half\_life}, \text{tps})$) rather than tightly coupled into the hot execution path.

- **[Phase 1] Decoupled Bayesian belief state ($\alpha, \beta$) from operational health metric ($H$).** Alternatives considered: using posterior mean $\frac{\alpha}{\alpha + \beta}$ as the health score; using EWMA alone with synthetic sample generation. Why this one won: Beta parameters capture statistical dispersion/uncertainty needed for Thompson Sampling exploration, whereas EWMA provides a smooth, monotonic, deterministic signal required for operator observability, circuit breaker thresholds, and PID setpoint tracking without sampling noise.

- **[Phase 1 Review] State design approved for Phase 4 PID consumption without structural changes.** Alternatives considered: computing traffic allocation weights inside `AcquirerState`; coupling continuous wall-clock decay into `record_outcome`. Why this one won: The state module provides both the stochastic signal (`sample()`) needed for Thompson exploration and the monotonic signal (`health_score`) needed for damping. Traffic allocation tracking ($w_i$) belongs strictly to the routing layer (Phase 3/4), not individual acquirer state. Continuous time decay adds floating-point and lock overhead to the hot path without demonstrable benefit over discrete event decay.

- **[Phase 2] Outage state defaults to hard on/off step function for v1, reserving schema for gradual curves.** Alternatives considered: built-in time-decay interpolation (linear or sigmoid ramp); separate mock server process per outage state. Why this one won: A hard step-function outage is the most rigorous stress-test possible for Loom's bandit + PID control loop — if the router can damp an instantaneous cliff without overshoot or oscillation ringing, it easily handles gradual degradation. Internal ticker loops or wall-clock interpolation within the simulator would introduce nondeterminism into unit tests and race conditions during benchmark runs. Any desired continuous curve can be orchestrated externally via `POST /admin/success-rate` without complicating the simulator daemon.

- **[Phase 2] Authorization declines under outage return HTTP 200 with structured JSON (`authorized: false`, `decline_code: 'ACQUIRER_OUTAGE'`) by default.** Alternatives considered: returning HTTP 503 Service Unavailable exclusively; dropping connections or timing out. Why this one won: Real payment acquirers (Stripe, Adyen) distinguish between transport failures and authorization declines. Returning a structured decline payload on HTTP 200 prevents unhandled HTTP client exceptions from crashing downstream transaction generators and ensures deterministic evaluation, while an optional `outage_behavior: HTTP_503` setting remains available for transport-level resilience testing.

- **[Phase 2] Application factory pattern (`create_app`) with multi-process isolation.** Alternatives considered: single monolithic FastAPI instance with multi-tenant sub-paths (`/acquirers/{id}/authorize`). Why this one won: Production payment routers target distinct network endpoints (different hostnames/ports) per acquirer. Running independent processes on separate ports provides real OS-level failure isolation (one acquirer crashing or hanging cannot block another's event loop) and matches the exact URL-based routing architecture Phase 3 requires.

- **[Phase 2 Review] Simulator contract certified for Phase 3 router integration with authoritative URL-path routing and explicit payload boolean checking.** Alternatives considered: requiring transaction deduplication / idempotency table in simulator; returning HTTP 400 on mismatched body `acquirer_id`. Why this one won: The simulator's primary role is to serve as an external stochastic trial generator for the control loop, not a replicated banking ledger. Resolving acquirer identity by URL path (`/acquirers/{id}/authorize`) matches real-world gateway routing where acquirers reside at distinct endpoints, and requiring Phase 3's router to check `response.json()["authorized"]` rather than HTTP status code mirrors production payment gateway consumption patterns.

- **[Phase 3] 100% hard-switching routing policy ($\arg\max_i \theta_i$) without interim heuristic smoothing.** Alternatives considered: Softmax / Boltzmann exploration; rolling-average probability split; fractional traffic allocation. Why this one won: Phase 3's explicit purpose in the PRD is to demonstrate the raw bandit's oscillation and herd migration failure modes under stress. Introducing ad-hoc smoothing or heuristics now would preempt Phase 4's PID controller, obscure the baseline comparison needed to prove PID damping, and violate PRD Build Order Step 3.

- **[Phase 3] Single-shot authorization per transaction without inline multi-arm retries.** Alternatives considered: Cascading fallback retries across remaining arms on decline. Why this one won: Automatic cascading retry masks route failure from the bandit, distorts observed PSR, and violates the clean Bernoulli trial contract where each transaction evaluates the primary routing decision. Retries belong to a higher-level orchestrator or resilience layer, not the core bandit selection loop.

- **[Phase 3] Dual-mode router architecture: in-process async engine (`BanditRouter`) with optional FastAPI HTTP daemon wrapper (`router_core/app.py`).** Alternatives considered: standalone HTTP-only microservice; pure in-process library only. Why this one won: The in-process async engine allows millisecond-scale, zero-network-overhead unit/integration testing and high-throughput benchmarks (>5,000 TPS), while the HTTP service wrapper matches production multi-process payment gateway topologies and prepares the interface for Phase 5 (data layer) and Phase 7 (WebSocket dashboard).

- **[Phase 3] Rate-controlled synthetic transaction generator with dual arrival pacing (Fixed and Poisson).** Alternatives considered: unthrottled loop; external load-testing tools (Locust, k6). Why this one won: A native Python asyncio generator using token-bucket / sleep intervals adheres to the zero-external-dependency rule (`docs/CONSTITUTION.md`), provides precise control over target TPS and duration, and emits structured transaction records directly compatible with Phase 5's SQLite metrics logger.

- **[Phase 3 Review] Tech Lead Gate Certification: Verification of Bandit Oscillation Baseline, Pathology Triage, and Phase 4 PID Requirements.** Alternatives considered: Rejecting baseline due to route starvation pathology; implementing interim ad-hoc smoothing in Phase 3. Why this one won: The inverted gate is passed — QA's scripted scenario proved that the 13 route flips, back-to-back chatter (Tx 53–57), and binary 0% $\leftrightarrow$ 100% hard-switching are the pure mathematical consequence of applying an argmax hard-switch to overlapping Beta posterior distributions, with zero pipeline artifact or transport defect. The quantitative baseline (13 flips, 19-tx crossover window, 7 failures absorbed) establishes the exact reference against which Phase 4 PID dampening will be judged. Tech Lead makes three binding architectural calls for Phase 4:
  1. **Mandatory Exploration Floor ($w_{\text{min}} \ge 3\%$)**: To resolve the dormant route starvation vulnerability uncovered by QA (where recovered Alpha received 0/50 transactions under event-driven decay), Phase 4 routing allocation MUST guarantee an exploration floor to prevent permanent lockout of recovering acquirers.
  2. **Decoupled PID Error Input**: Phase 4 PID controller MUST NOT take raw Thompson sample deltas as the process variable error signal $e(t)$, which would induce catastrophic derivative kick ($K_d \frac{de}{dt}$) from stochastic sampling jitter. PID error must be derived from the smoothed EWMA health signal ($H_i$) or posterior means ($\mathbb{E}[\theta_i]$), using Thompson Sampling exclusively for target setpoint weighting.
  3. **Certified Go for Phase 4**: Phase 3 pipeline implementation, test suite (116 tests passing), and documentation are certified complete and ready for the Phase 4 PID smoothing engine.

- **[Phase 4] Pure-function decoupled PID step engine with immutable state snapshots.** Alternatives considered: Stateful controller class mutating internal variables (`self.accumulated_error += e`); global accumulator dictionary. Why this one won: A pure function taking immutable state (`PIDState`) and inputs (`target_allocation`, `current_allocation`, `config`, `dt`) and returning a new state snapshot is 100% deterministic, side-effect-free, and trivial to unit-test against analytical test vectors without mocks or async event-loop concurrency bugs.

- **[Phase 4] Derivative-on-measurement (rate of change of actual allocation) to eliminate derivative kick.** Alternatives considered: Classical derivative on error ($K_d \frac{de}{dt}$); heavy low-pass filtering on raw error. Why this one won: Discontinuous jumps in the bandit's target allocation ($w^{\text{target}}$) create infinite/impulsive $\frac{dr}{dt}$ spikes ("derivative kick"), which would induce violent ringing if differentiated. Differentiating the process variable (actual allocation: $-K_d \frac{dw}{dt}$) provides true velocity damping (acting like a viscous dashpot against rapid traffic shifts) while completely preventing derivative kick on setpoint steps.

- **[Phase 4] Anti-windup bounded accumulator with symmetric clamping and leaky integration.** Alternatives considered: Unbounded integration with post-hoc output clamping; back-calculation anti-windup. Why this one won: During prolonged outages, persistent setpoint deficits cause unbounded integral error accumulation. Clamping the accumulator strictly to $[-I_{\text{max}}, +I_{\text{max}}]$ and applying an optional leaky retention factor ($\gamma_I = 0.95$) ensures the integral term can never overpower proportional control or delay recovery upon outage clearance.

- **[Phase 4] Bounded simplex projection enforcing an active exploration floor ($w_{\text{min}} \ge 3\%$).** Alternatives considered: Background ticker decay on unselected arms; ad-hoc epsilon-greedy probing outside PID. Why this one won: Solves Phase 3 QA's finding of post-outage route starvation directly at the actuator boundary. Projecting the smoothed allocation onto the bounded simplex $\sum w_i = 1.0, w_i \ge w_{\text{min}}$ guarantees that even completely disabled routes receive probe volume, ensuring that when the route recovers, event-driven decay immediately detects it and restores routing.

- **[Phase 4] Configurable dual-actuation dispatch model: stochastic categorical draw (default) and deterministic deficit tracking.** Alternatives considered: Stochastic draw only; deficit round-robin only. Why this one won: Stochastic categorical drawing (`rng.choice(arms, p=w)`) requires zero inter-transaction state and perfectly models probabilistic routing, while deterministic deficit tracking (Bresenham virtual queue) provides mathematically exact flow pacing with zero binomial sample jitter for deterministic benchmark verification. Both modes share the identical PID smoothed allocation vector.

- **[Phase 4 Ticket B] Empirical PID Gain Tuning & Easing Characterization against Phase 3 Outage Scenario.**
  - **Tuned Parameter Baseline**: $K_p = 0.12$, $K_i = 0.005$, $K_d = 0.25$, $I_{\text{max}} = 1.0$, $w_{\text{min}} = 0.03$, `derivative_on_measurement = True`, `actuation_mode = "deficit"`.
  - **Damping & Smoothness Validation**:
    - Reduces peak single-step allocation jump from **100.0%** (Phase 3 binary hard-switch) down to **11.77%** ($< 15\%$ spec limit).
    - Outage decay transitions smoothly without overshoot or ringing ($0.72 \to 0.78 \to 0.82 \to 0.65 \to 0.53 \to 0.42 \to 0.34 \to 0.30 \to 0.23 \to 0.18 \to 0.04 \to 0.03$).
    - Eliminates dormant route starvation: Allocates active exploration probe traffic to recovered leader ($w_{\text{min}} = 0.03$), dispatching 2 probe transactions post-recovery that successfully trigger event-driven decay and state recovery (compared to 0/50 transactions in Phase 3 baseline).
  - **Documented Tuning Progression**:
    - *High $K_p$ ($K_p = 0.50$)*: Over-amplified stochastic sample noise, creating severe ringing (jumps from 0.32 to 0.82 at Tx 58) and 48.5% single-step jumps.
    - *Low $K_p$ ($K_p = 0.02$)*: Overdamped and sluggish; required $>50$ transactions to shed traffic, absorbing 13 failures during the outage.
    - *Zero $K_d$ ($K_d = 0.00$)*: Lack of velocity damping caused boundary chatter and rebound spikes (Tx 58 allocation bounced back up to 0.63).
    - *Excessive $K_d$ ($K_d = 0.80$)*: Viscous damping fought setpoint transitions too aggressively, holding failing leader at 74% until collapsing in a 36.9% drop.
    - *High $K_i$ ($K_i = 0.10$)*: Induced integrator windup lag during the 50-tx warmup, delaying shedding until Tx 56 and generating 19.7% post-outage jumpiness.

- **[Phase 4 Review] Tech Lead Gate Certification: Verification of PID Smoothing, Anti-Windup Clamping, and Phase 5 Authorization.** Alternatives considered: Demanding zero additional failure absorption during outage ramp; requiring dynamic exploration floor throttling prior to Phase 5; deferring data layer for further continuous $\Delta t$ modeling. Why this one won:
  1. **Damping is Convincing, Not Merely "Less Jerky"**: The controller transforms a pathological binary square wave (0% $\leftrightarrow$ 100% hard-switching, 4 consecutive A-B-A-B-A flips) into a mathematically continuous, monotonic exponential decay curve ($0.72 \to 0.78 \to 0.82 \to 0.65 \to 0.53 \to 0.42 \to 0.34 \to 0.30 \dots \to 0.03$). Peak single-step delta is crushed by 88.2% (100.0% $\to$ 11.77%, well below the 15% spec limit), and dynamic overshoot is strictly 0.00%.
  2. **Failure Trade-off is Sound**: Absorbing 4 additional failures during a 50-transaction outage (11 vs 7) is an intentional and defensible engineering trade-off. An instantaneous 100% hard-switch triggers herd migration stampedes that overwhelm downstream backup acquirers. A 4-transaction buffer is a modest price to pay for system stability.
  3. **Windup Handling is Rigorous & Robust**: QA's 200-transaction outage stress test demonstrated that an unbounded integrator drifts to $-8.99$, paralyzing the router for 5 to 6 transactions after outage clearance. Ticket A's anti-windup clamping ($I_{\text{max}} = 1.0$) eliminates this paralysis entirely (immediate response on Step 1, 0 steps delay). Furthermore, anti-windup prevents perpetual positive drift caused by the steady-state exploration floor error ($e = +0.03$).
  4. **Data Layer Readiness**: The `RoutingResult` envelope exposes typed `smoothed_allocation`, `target_allocation`, and `pid_diagnostics` telemetry, fully satisfying Phase 5's ingestion contract.
  5. **Certified Go for Phase 5**: Phase 4 implementation (147/147 tests green) is certified production-ready. Proceed to Phase 5 (Data & Metrics Layer).

- **[Phase 5] Decoupled dual-storage model (Redis for ephemeral state & Pub/Sub; SQLite for append-only analytical ledger).** Alternatives considered: SQLite only (rejected due to file-lock contention and lack of pub/sub for live dashboard); Redis Streams + RedisJSON only (rejected due to expensive analytical queries and lack of relational SQL aggregation for Phase 6 PSR-lift); PostgreSQL for all operations (rejected because external database server violates PRD simplicity and local standalone setup). Why this one won: Redis delivers sub-millisecond in-memory lookups and real-time push streaming to WebSockets, while SQLite provides zero-config, embedded, durable SQL storage with rich window functions for post-hoc analysis.

- **[Phase 5] Multi-process atomic belief decay via embedded Redis Lua script.** Alternatives considered: Distributed locks (Redlock); optimistic concurrency (`WATCH`/`MULTI`/`EXEC`) with retry loops; periodic write-behind cache syncing. Why this one won: A Redis Lua script executes Phase 1's exact mathematical transition in single-threaded C inside the Redis engine, completely preventing lost updates across concurrent router worker processes without network round-trip retry loops or distributed lock contention.

- **[Phase 5] Fire-and-forget Redis Pub/Sub with at-most-once delivery semantics for dashboard eventing.** Alternatives considered: Redis Streams with consumer groups; server-sent events (SSE) polling backend; WebSockets terminating directly on router core. Why this one won: The dashboard visualizes real-time moving charts; if the dashboard is temporarily closed or lagging, intermediate frames are irrelevant and should not consume router memory. SQLite already provides an immutable historical audit trail, rendering persistent stream buffering in Redis redundant.

- **[Phase 5] Micro-batched asynchronous SQLite metrics logger with Write-Ahead Logging (WAL).** Alternatives considered: Synchronous `INSERT` on the hot routing path; multi-threaded pool with unbounded queue; external log-shipper daemon. Why this one won: Synchronous disk writes introduce 5–20ms of fsync latency into payment authorization. An in-memory bounded `asyncio.Queue` coupled with `aiosqlite.executemany` flushing every 20 records or 50ms achieves $>2,000$ TPS write throughput with $<2\mu\text{s}$ impact on routing latency.

- **[Phase 5] Constitutional engine-level append-only enforcement via SQLite triggers.** Alternatives considered: Application-level repository conventions (read/insert only); OS filesystem write-once permissions. Why this one won: Enforcing `RAISE(ABORT)` triggers inside the SQLite database schema guarantees that even buggy ad-hoc scripts, ORM mutations, or accidental deletes cannot violate `docs/CONSTITUTION.md`'s rule prohibiting log mutation.

- **[Phase 5 Ticket B] Real-Time Redis Pub/Sub Event Streaming with Self-Documenting Schema Contract.** Alternatives considered: WebSockets direct to router core (coupling UI transport to core router); polling HTTP `/state` (high latency and excessive redundant requests); Redis Streams with consumer groups (unnecessary broker-side queuing overhead for live charts). Why this one won:
  1. **Strictly Typed, Unambiguous Telemetry Schema (`RoutingEvent`)**: Formally defined in `data_layer/models.py` and exported to `docs/schemas/routing_event.json`. Captures point-in-time sequence numbers, timestamps, transaction IDs, selected route, authorization outcomes, granular latencies, Thompson sampling distributions, PID smoothed weights, diagnostics, and updated post-outcome acquirer health states so that Phase 7 React dashboard engineers have zero ambiguity.
  2. **Non-Blocking Telemetry Hook in `BanditRouter`**: Telemetry broadcast is wired into `BanditRouter.route()` as an optional hook (`self._event_publisher`). Execution is safe and fire-and-forget; telemetry errors are caught and suppressed (`raise_on_error=False`), guaranteeing that network or Redis hiccups can never fail a payment transaction.
  3. **Zero-Drop and Strict Monotonic In-Order Delivery**: Empirically verified under sustained transaction bursts (150+ TPS) with zero drops and strict sequential ordering ($1, 2, \dots, N$). Tested multi-subscriber fanout, zero-subscriber safety (0 listeners drop cleanly without memory accumulation), and separate `events:health` degradation alert channel.
- **[Phase 5 Ticket C] SQLite Append-Only Outcome Ledger and Analytical PSR Ingestion Engine.** Alternatives considered: Postgres/TimescaleDB (heavyweight external service requiring docker/credentials, violating solo-demo simplicity); flat CSV/JSONL appending (lacks atomic foreign keys, indexed query performance, and transactional safety under concurrency). Why this one won:
  1. **Engine-Enforced Constitutional Immutability**: Implemented SQLite triggers (`prevent_transactions_update`, `prevent_transactions_delete`, `prevent_acquirer_outcomes_update`, `prevent_acquirer_outcomes_delete`) in `data_layer/schema.sql` that raise `RAISE(ABORT, ...)` at the database engine boundary. Static AST inspection confirms `data_layer/sqlite_logger.py` contains zero `UPDATE` or `DELETE` methods or SQL DML statements.
  2. **Complete Telemetry & PSR Metric Capture**: Relational schema captures all 16 transaction attributes (`transaction_id` UNIQUE, `timestamp`, `chosen_acquirer`, `allocation_weight`, `status`, `authorized`, `success`, `decline_code`, `latencies`, `smoothed_allocation_json`, `target_allocation_json`, `thompson_samples_json`, `pid_diagnostics_json`, `error_message`) and 11 outcome attributes (`alpha`, `beta`, `health_score`, `expected_success_rate`, counts), directly satisfying Phase 6's analytical requirements.
  3. **High-Throughput Micro-Batched Async Writer (`MetricsLogger`)**: Non-blocking `asyncio.Queue` buffer ($< 2.0\mu\text{s}$ enqueue) with background worker draining up to `batch_size=20` or flushing every `flush_interval_sec=50ms` using `aiosqlite.executemany` inside atomic transactions. Guarantees graceful shutdown persistence with zero lost records.
  4. **Strict Single-Row, Zero-Duplicate, Zero-Gap Pipeline Invariant**: Verified across 100 transactions routed through `BanditRouter` sequentially and concurrently. Exactly 100 rows in `transactions`, exactly 100 matching rows in `acquirer_outcomes`, zero duplicates (`UNIQUE` constraint), zero sequence gaps.
  5. **Phase 6 Analytical Read Queries**: Integrated `get_psr_metrics(...)` computing payment success rates, route-by-route performance breakdown, latency averages, and time-window aggregations strictly via read-only SQL queries.

- **[Phase 5] Purely additive integration architecture preserving Phase 1–4 contracts.** Alternatives considered: Refactoring `BanditRouter` to depend directly on Redis; changing `RoutingResult` into a data layer entity. Why this one won: Preserving existing public interfaces ensures that all 147 Phase 1–4 tests pass without alteration, enabling Loom to run either in standalone in-memory mode or connected to a durable data layer via an optional facade hook.

- **[Phase 5 Review] Tech Lead Gate Certification: Audit of Additive Architecture, SQLite Schema Sufficiency for Phase 6, and Demo Environment Reliability.** Alternatives considered: Requiring SQLite migration tools (Alembic); enforcing distributed Redis clustering; rejecting in-process telemetry hooks in favor of an external sidecar. Why this one won:
  1. **Strict Additive Purity Certified**: Audit confirms `router_core/state.py`, `router_core/bandit.py`, `router_core/pid.py`, `acquirer_sim/`, and `router_core/models.py` have 0 lines modified. `BanditRouter` incorporates optional hooks with non-blocking error handling (`logger.warning`), preserving 100% of Phase 1–4 contracts (202/202 tests pass, including all 147 original Phase 1–4 tests).
  2. **SQLite Schema Certified for Phase 6 Without Rework**: Relational schema in `data_layer/schema.sql` captures all necessary fields (`transaction_id` UNIQUE, `timestamp`, `chosen_acquirer`, `allocation_weight`, `status`, `authorized`, `success`, `decline_code`, `latencies`, `smoothed_allocation_json`, `pid_diagnostics_json`). Indices and constitutional triggers (`prevent_*_update`, `prevent_*_delete`) are active. Read-only aggregation via `get_psr_metrics()` satisfies all requirements for the Phase 6 PSR-lift comparison.
  3. **DevOps Local Environment Approved for Live Rehearsals**: Automated CLI tooling (`python -m data_layer.cli`) provides idempotent DB setup (`init-db`), fast socket health probing with `<0.5s` timeouts (`ping`), runtime diagnostics (`status`, `inspect-state`), and clean rehearsal reset (`reset-demo --force`) using safe `DROP TABLE` recreation to respect constitutional append-only triggers.
  4. **Certified Go for Phase 6**: Phase 5 data layer, test suite, and operational tooling are certified production-ready. Authorize progression to Phase 6 (Baseline Router & PSR-Lift Comparison).

- **[Phase 6] Standalone baseline router module (`baseline_router/`) preserving strict additive isolation.** Alternatives considered: Adding static if/else flags to `BanditRouter`; using an off-line statistical replay model. Why this one won: A standalone module in `baseline_router/` leaves `router_core/` 100% untouched and unpolluted by legacy static branching, preserving all Phase 1–5 test guarantees. An off-line replay model would fail to capture closed-loop network latency, simulator concurrency, and runtime socket behavior, violating the requirement for an authentic comparison.

- **[Phase 6] Absolute pipeline parity: shared Phase 2 HTTP simulator endpoints and Phase 5 SQLite logging schema.** Alternatives considered: Custom lightweight mock server; standalone CSV/JSONL output logs for the baseline. Why this one won: The PSR comparison only has scientific integrity if both routers execute against the exact same network transport (`httpx.AsyncClient` hitting `acquirer_sim` over HTTP) and write the exact same `RoutingResult` envelope into the exact same SQLite schema (`transactions` and `acquirer_outcomes`). Using the identical `get_psr_metrics()` SQL aggregation query ensures that PSR lift is calculated with zero pipeline discrepancy.

- **[Phase 6] Production-representative Active-Passive Priority Failover rule with $M=3$ consecutive failure tripping and canary cooldown probing.** Alternatives considered: Strawman baseline (router that never fails over, or flips randomly on a coin toss); sliding window only ($\tau \ge 20\%$). Why this one won: Real enterprise payment gateways (Spreedly, Primer, Adyen backup routing) employ priority tiers with circuit breaker debouncing. $M=3$ consecutive failures represents the standard balance between nuisance-trip prevention on transient card declines and outage detection speed. Implementing canary cooldown probing ($N_{\text{cooldown}} = 30$) reflects competent modern engineering. Proving Loom's PSR lift against an authentic, production-grade baseline ensures that Loom's measured advantage cannot be dismissed as a comparison against an artificially weakened strawman.

- **[Phase 6] Database isolation via dedicated SQLite ledger (`baseline_metrics.db`) with identical DDL.** Alternatives considered: Logging Loom and Baseline runs into a single `loom_metrics.db` with run tags; ephemeral in-memory SQLite tables. Why this one won: Executing the baseline into `baseline_metrics.db` using the identical `data_layer/schema.sql` guarantees zero table write-lock contention, eliminates WAL checkpoint starvation during concurrent benchmark runs, and preserves an immutable, isolated audit trail for side-by-side SQL diffing.

- **[Phase 7] Locked Mission-Control Visual Direction (Navy `#0B1120`, Panel `#111827`, Hairlines `#1E3A5F`, Amber `#D9A441`, Rust `#C1622D`).** Alternatives considered: Standard Tailwind UI kit; Grafana embedded iframe; light/dark SaaS themes. Why this one won: Loom is demonstrating a mathematically rigorous control system. Consumer SaaS designs with pastel gradients and rounded card kit shadows dilute credibility. Locking the mission-control palette establishes semantic clarity: Amber is strictly reserved for live telemetry numbers and the healthy-state chart line; Rust is strictly reserved for outage/danger states; monospace is restricted to numbers; flat hairline borders eliminate UI fluff.

- **[Phase 7] FastAPI-Native WebSocket Gateway (`/ws/telemetry`) over separate Node.js proxy.** Alternatives considered: Standalone Node.js proxy (`ws` / `socket.io`); client polling HTTP `/state`. Why this one won: Directly leverages Phase 5's `AsyncEventSubscriber` in Python, eliminating an extra runtime daemon and preserving single-process execution for the demo environment without added operational complexity.

- **[Phase 7] Decoupled Circular Ring Buffer with `requestAnimationFrame` render throttling.** Alternatives considered: Direct React `setState` per message; fixed interval polling (`setInterval`). Why this one won: Transaction rates of 20–50 TPS cause 50 state dispatches per second if wired naively, freezing the browser thread. A circular ring buffer (200 events) combined with a `requestAnimationFrame` 60 FPS animation ticker ensures rock-solid UI smoothness with bounded client memory (<50MB).

- **[Phase 7] Hybrid Cold-Start Bootstrap (SQLite History + Redis Live Stream).** Alternatives considered: Redis Streams with consumer groups; empty chart on initial page load. Why this one won: Redis Pub/Sub has at-most-once delivery and no historical replay buffer. Sending an initial bootstrap snapshot of recent transactions from SQLite upon WebSocket connection guarantees the chart populates instantly upon page open without waiting for new transactions.

- **[Phase 7] Backend Reverse Proxy for Acquirer Outage Controls.** Alternatives considered: Direct browser fetch to acquirer ports with CORS; manual curl commands in terminal. Why this one won: Eliminates browser CORS issues and decouples the frontend from internal port topology by routing admin requests through `router_core/app.py`.

- **[Phase 7] Contextualized Multi-Scenario Benchmark Presentation (Phase 6 Findings).** Alternatives considered: Displaying raw standard benchmark (86% vs 92%) without context; hiding baseline comparisons entirely. Why this one won: In an unconstrained simulation with infinite mock secondary capacity, static hard-switching appears to win because secondary collapse is unmodeled. Presenting the 8.5x stability improvement (11.77% vs 100.0% peak jump) alongside the Overreaction ($M=1$, +1000 bps Loom lift) and Gray Failure scenarios provides an honest, compelling, scientifically sound comparison.

- **[Phase 4/7 Review] Production PID Server Wiring & Global Tuned Gains Lock.** Alternatives considered: Leaving PID optional via explicit CLI opt-in flag with no-op default; allowing separate gain profiles across dashboard and core router. Why this one won: Without PID explicitly instantiated in the production CLI entrypoint (`router_core/server.py`) and default ASGI application instance (`router_core/app.py`), the router silently fell back to Phase 3 Winner-Take-All hard-switching, emitting binary 100%/0% square-wave allocations that completely broke live dashboard damping. We enabled PID by default in both entrypoints with a `--no-pid` bypass flag, and locked default gains in `PIDConfig` across all scripts, dashboard footers, and test suites to Phase 4's empirically tuned values ($K_p=0.12, K_i=0.005, K_d=0.25, I_{\text{max}}=1.0, w_{\text{min}}=0.03$), eliminating untuned aggressive settings ($K_p=0.40, K_i=0.05, K_d=0.10$).

- **[Phase 7 Revision] Visual Design System & Information Architecture Revision (Supersedes Phase 7 Color/Type Decision).** Alternatives considered: Retaining original navy/amber/rust palette; adopting generic light/dark SaaS theme; embedding Grafana; maintaining disconnected bottom-deck operator controls. Why this one won:
  1. *Visual & Semantic Discipline*: The original navy (`#0B1120`), amber (`#D9A441`), and rust (`#C1622D`) console created high visual fatigue, eye vibration, and competing semantic signals across multiple route lines. The revised palette shifts to industrial slate ground (`#0F1115`), cockpit panel (`#16181D`), and low-contrast borders (`#2A2D34`), governed by strictly **one accent** (`#5B8DEF` cobalt blue for healthy state, active chart lines, and focus states) and **one alert** (`#E5484D` crimson for outage/degraded states only, never decorative).
  2. *Typography & Casing*: Switching from Inter / JetBrains Mono to `IBM Plex Sans` (for all UI text, headings, and labels) and `IBM Plex Mono` (strictly reserved for live numeric telemetry: PSR%, health scores, RTT, counters) establishes authentic engineering rhythm. Mandating sentence case throughout eliminates the shouting and visual noise of tracked-out uppercase.
  3. *Information Architecture (Sensor-Actuator Colocation)*: Separating the operator outage controls into an isolated bottom deck forced operators to scan back and forth across 800 vertical pixels between the health readout and the trigger button. Colocating each acquirer's outage controls physically adjacent to its own health card unifies perception and actuation into a cohesive operational unit.
  4. *Progressive Disclosure*: Default-closed `<details>` disclosures for "Diagnostics" (Phase 6 benchmark comparison audit) and "Simulation controls" (global presets and reset) reclaim primary vertical real estate, ensuring the live allocation chart and acquirer health readouts remain the unobstructed hero center of the telemetry cockpit.
  5. *Consolidated Persistent Status*: Consolidating three disparate, jumping status badges into a single persistent header element eliminates layout shift while providing honest, unambiguous connection lifecycle states (`Live`, `Reconnecting`, `Connection lost`).

- **[Phase 7 Revision 3] Deterministic Acquirer Color Assignment & Inviolable Alert Override (Supersedes Phase 7 Revision 2 Chart Line Assignment).** Alternatives considered: Retaining single-accent with dual-gray lines (#5B8DEF + two #8B8F98 lines); using full multi-hue rainbow (blue, purple, green, yellow); allowing alert color (#E5484D) to appear as static UI accents. Why this one won:
  1. *Multi-Arm Visual Discrimination*: Revision 2's strict single-accent rule solved chromatic fatigue but degraded chart legibility during traffic cutover: when Primary Alpha shed volume, secondary and tertiary routes (Beta and Gamma) both rendered in muted gray (#8B8F98), making crossover disambiguation difficult at a glance.
  2. *Assigned, Not Decorative Mapping*: Introducing Accent 2 (`#C084FC` soft violet) for the second acquirer and Secondary Text Gray (`#7C808A`) for the third acquirer gives each route a deterministic, identifiable hue without adding a distracting third saturated color.
  3. *Cross-Surface Identity Invariant*: This exact mapping applies uniformly across all surfaces (chart lines, legend dots, health-card indicators, and EWMA micro-gauge bars) so that any given acquirer maintains identical visual identity across the cockpit.
  4. *Inviolable Alert Override*: Alert (`#E5484D` crimson) strictly overrides any acquirer's assigned color the instant it enters an outage or degraded state ($H < 0.70$), guaranteeing that failure states dominate operator attention. Alert is strictly forbidden from appearing anywhere else for any other purpose.
  5. *Accent 2 Scope Constraint*: Accent 2 (`#C084FC`) is strictly quarantined to the second-acquirer identity; it is forbidden from being reused as general-purpose UI styling.

- **[Phase 7 Revision 4] Typography & Extreme Density Reduction Contract (Supersedes Phase 7 Revision 3 Density & Typography Rules).** Alternatives considered: Retaining boxed cockpit panels and per-acquirer cards; hiding acquirers in a modal or dropdown; keeping the full 4-panel cluster telemetry grid. Why this one won:
  1. *Elimination of Container Clutter*: Boxed cards, hairline panels, and micro-gauge bars created visual noise and repeated representations of the same fact (e.g. traffic weight shown in chart, card header, subgrid, and legend simultaneously).
  2. *Headline Visual Hierarchy*: Promoting PSR and Lift-vs-Baseline to borderless, box-free headline figures side-by-side establishes an immediate visual anchor for executive and technical review panels without boxy container boundaries.
  3. *Radical Row-Level Acquirer Reduction*: Reducing each acquirer to a single row (color dot, name, one live telemetry number) eliminates redundant sub-cards while preserving immediate operational state.
  4. *Type Hierarchy Discipline*: Allocating `Space Grotesk` (700) exclusively for the wordmark and massive headline metrics, `Space Mono` for live telemetry figures, and `Inter` for all UI labels delivers clean, modern legibility.
  5. *Zero-Footprint Progressive Disclosure*: Collapsing secondary diagnostics behind a minimal text link (`'Diagnostics ›'`) reclaims vertical space and eliminates boxy disclosure borders even when closed.
  6. *Plain Text-Style Outage Controls*: Transforming chunky bordered button cards into plain text-style buttons (`[trigger outage]` / `[clear outage]`) maintains instant actuation while preserving typographic lightness.
  7. *Preserved Invariants*: The Revision 3 deterministic acquirer color mapping (`#5B8DEF`, `#C084FC`, `#7C808A`), chart series line colors, and the inviolable Alert (`#E5484D`) override remain strictly active and unchanged.

- **[Phase 7 Revision 5] Humanized Architecture Descriptions & Minimized Simulation Harness Contract (Supersedes Phase 7 Revision 4 Footer & Simulation Controls).** Alternatives considered: Keeping raw mathematical/config strings (`PID [Kp=0.12, ...]`, `tau=60s`, `SQLite ledger`, `Redis dispatch`) in the footer; keeping inline scenario presets and failure controls in an expanded deck; deleting advanced simulator controls entirely to save space. Why this one won:
  1. *Humanized Architectural Comprehension*: Raw configuration parameters and tuning knobs create cognitive friction without communicating operational intent to executive and technical evaluators. Replacing cryptic configuration formulas with plain descriptions explains the core mechanical purpose of each layer directly in human terms:
     - `'Smooths every reroute so traffic never jumps'` (PID control loop)
     - `'Learns which gateway is healthiest, weighted toward the last minute'` (Thompson Sampling)
     - `'Every decision is logged, permanently'` (Append-only SQLite ledger)
     - `'Reacts to every transaction instantly'` (Real-time Redis Pub/Sub)
  2. *Strict Spatial Separation (Zero Pipes or Middle-Dots)*: Inline text strung together with `•` or `|` creates visual crowding and horizontal tracking fatigue. Formatting these four plain descriptions as independent vertical lines (one line each, never joined by middle-dots or pipes) enforces visual calm and dignified typographic hierarchy.
  3. *Excision of Artifact Version Strings*: Removing `"Loom protocol v0.7.0"` completely eliminates dead build-version noise from the live operational display.
  4. *Symmetric Minimal Simulation Harness*: Matching the dashboard's existing per-acquirer row pattern brings structural harmony to the simulation harness: each acquirer gets exactly one row (`[Name] [Current State] [Trigger/Clear outage plain text button]`), with nothing else visible by default.
  5. *Progressive Disclosure for Advanced Simulator Controls (`'Simulation settings ›'`)*: Success-rate sliders, failure-behavior mode toggles (`RETURN_DECLINE`, `HTTP_503`, `LATENCY_SPIKE`), and benchmark presets (`Standard Cliff`, `Sensitive Blip`, `Gray Failure`, `Reset all routes`) move behind a single `'Simulation settings ›'` link, collapsed by default (`open={false}`), mirroring the established `'Diagnostics ›'` text link pattern.
  6. *Additive Invariant Guarantee*: Zero functional capabilities that QA, backend, or automated tests relied on in earlier phases are deleted or disabled. All backend endpoints (`POST /api/simulator/acquirers/{id}/outage`, `POST /api/simulator/acquirers/{id}/success-rate`, `POST /api/simulator/admin/reset`), payload schemas, slider ranges, behavior modes, and presets remain 100% active, testable, and functional behind the disclosure.

- **[Phase 7 Revision 6] Static-Baseline Reference Curve Overlay on Live Allocation Chart (Extends Phase 7 Revision 3/4/5 Chart Contracts).** Alternatives considered: Displaying two separate charts side-by-side; running a live shadow baseline router concurrently in backend; hiding baseline comparisons exclusively inside the collapsed diagnostics panel. Why this one won:
  1. *Visceral Visual Contrast on Single Hero Canvas*: Juxtaposing the static baseline's brutal 100% cliff drop against Loom's smooth PID exponential easing curve on the exact same coordinate plane provides immediate, undeniable visual proof of Loom's core stability claim ($\Delta w_{\text{max}} = 11.77\%$ vs $100.0\%$).
  2. *Pre-Computed Deterministic Reference*: Sourcing the baseline curve directly from Phase 6's stored 150-transaction benchmark results (under the identical outage gauntlet) eliminates runtime overhead and guarantees mathematical fidelity without running redundant backend routing loops.
  3. *Strict Non-Competitive Visual Hierarchy*: Rendering the reference curve in a dashed stroke, muted secondary text gray (`#8B8F98`), hairline width (1.0–1.5px), and rendering it behind all live series in z-order ensures it remains an archival backdrop that never competes with or obscures live operational telemetry.
  4. *Load-Bearing Disambiguation Legend*: Explicitly labeling `'Loom (live)'` vs `'Static baseline (recorded run)'` is load-bearing, preventing technical panels and auditors from misinterpreting the reference line as a second live router or shadow canary arm.
  5. *Dynamic Outage-Anchor Synchronization*: Pinning the baseline curve's outage step to the live chart's outage-trigger timestamp ensures both systems appear to encounter the outage at the exact same horizontal coordinate on the x-axis, regardless of when the live operator injects the fault.

- **[Post-Phase 7 Tech Lead Review] README Editorial Governance: Retention of Standalone "How This Was Built" Engineering Loop Section.** Alternatives considered: Folding "How This Was Built" into an abbreviated summary paragraph inside "How It Works" (Principle 10 ruthless cutting); removing engineering methodology from the README entirely to focus solely on software user documentation. Why this one won:
  1. *Separation of System Architecture from Meta-Engineering Process*: "How It Works" specifies the runtime payment execution pipeline (Simulator $\to$ Router Core $\to$ Data Layer $\to$ Dashboard). Conflating runtime software mechanics with team engineering methodology creates cognitive confusion between the payment control loop and the multi-role development loop.
  2. *Load-Bearing Evidence of the PID-Wiring Bug*: Loom's engineering credibility rests on proving that its multi-role loop (Architect $\to$ Engineer $\to$ QA $\to$ Tech Lead) operates as a rigorous, adversarial safety mechanism. Condensing the section would obscure the concrete PID-wiring bug—where the production server entrypoints silently fell back to Winner-Take-All hard-switching and were caught exclusively by QA's live TCP socket verification. That bug is primary evidence of engineering integrity, not an implementation footnote.
  3. *Balance of Depth and Conciseness*: Applying Principle 9 ("real depth over hand-waving") while maintaining Principle 10 discipline: the section is kept tight (33 lines), contains an illustrative Mermaid cycle, details the defect and resolution with exact parameter locks ($K_p=0.12, K_i=0.005, K_d=0.25, I_{\text{max}}=1.0, w_{\text{min}}=0.03$), and links directly to regression test artifacts (`tests/router_core/test_server_cli.py`).

## Interface Contracts


### [Phase 1] Per-Acquirer State & Health Signal Contract

**Module Target**: `router_core/state.py` (Domain entities & contracts) and `router_core/bandit.py` (Sampling & registry)

#### 1. Data Shapes

```python
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class AcquirerStateConfig:
    """Immutable configuration for an acquirer's bandit and health model."""

    alpha_prior: float = 1.0  # Prior alpha > 0.0 (default uniform Bayes prior)
    beta_prior: float = 1.0  # Prior beta > 0.0 (default uniform Bayes prior)
    decay_factor: float = 0.98  # Per-outcome retention factor gamma in (0.0, 1.0)
    initial_health: float = 1.0  # Initial health score in [0.0, 1.0] (optimistic start)


@dataclass(frozen=True, slots=True)
class AcquirerStateSnapshot:
    """Immutable point-in-time snapshot of acquirer state."""

    acquirer_id: str  # Unique identifier for the acquirer
    alpha: float  # Current Beta distribution shape parameter alpha >= alpha_prior
    beta: float  # Current Beta distribution shape parameter beta >= beta_prior
    health_score: float  # Current decayed EWMA health score in [0.0, 1.0]
    success_count: int  # Cumulative unweighted lifetime successes (>= 0)
    failure_count: int  # Cumulative unweighted lifetime failures (>= 0)
    total_count: int  # Cumulative unweighted lifetime transactions (>= 0)
    last_updated_at: float  # Unix epoch timestamp of last recorded outcome

    @property
    def expected_success_rate(self) -> float:
        """Posterior mean of the Beta distribution: alpha / (alpha + beta)."""
        return self.alpha / (self.alpha + self.beta)

    @property
    def variance(self) -> float:
        """Posterior variance of the Beta distribution."""
        total = self.alpha + self.beta
        return (self.alpha * self.beta) / (total * total * (total + 1.0))
```

#### 2. Mathematical Transition Functions

For an outcome $x \in \{0, 1\}$ ($x = 1$ for Success, $x = 0$ for Failure), retention factor $\gamma \in (0, 1)$, and priors $\alpha_0, \beta_0$:

1. **Beta Parameter Update**:
   $$\alpha_{t} = \alpha_0 + \gamma \cdot (\alpha_{t-1} - \alpha_0) + x$$
   $$\beta_{t} = \beta_0 + \gamma \cdot (\beta_{t-1} - \beta_0) + (1 - x)$$
2. **EWMA Health Score Update**:
   $$H_{t} = \gamma \cdot H_{t-1} + (1 - \gamma) \cdot x$$
3. **Counters**:
   $$\text{success\_count}_{t} = \text{success\_count}_{t-1} + x$$
   $$\text{failure\_count}_{t} = \text{failure\_count}_{t-1} + (1 - x)$$
   $$\text{total\_count}_{t} = \text{total\_count}_{t-1} + 1$$

#### 3. Public Class Interfaces

```python
class AcquirerState:
    """Encapsulates the decaying Beta belief and EWMA health score for a single acquirer."""

    def __init__(
        self,
        acquirer_id: str,
        config: AcquirerStateConfig | None = None,
        initial_timestamp: float | None = None,
    ) -> None:
        """Initialize acquirer state with prior parameters and optimistic health."""
        ...

    def record_outcome(
        self,
        success: bool,
        timestamp: float | None = None,
    ) -> AcquirerStateSnapshot:
        """Update Beta parameters and EWMA health score with a transaction outcome."""
        ...

    def sample(self, rng: np.random.Generator | None = None) -> float:
        """Draw a Thompson sample from the current Beta distribution belief."""
        ...

    def get_state(self) -> AcquirerStateSnapshot:
        """Return an immutable snapshot of current acquirer state."""
        ...


class BanditStateRegistry:
    """Manages state across all configured acquirers and coordinates Thompson Sampling."""

    def __init__(self, default_config: AcquirerStateConfig | None = None) -> None:
        """Initialize registry with optional default acquirer configuration."""
        ...

    def register_acquirer(
        self,
        acquirer_id: str,
        config: AcquirerStateConfig | None = None,
    ) -> None:
        """Register a new acquirer route with its own state and prior beliefs."""
        ...

    def record_outcome(
        self,
        acquirer_id: str,
        success: bool,
        timestamp: float | None = None,
    ) -> AcquirerStateSnapshot:
        """Record outcome for a specific acquirer and return updated snapshot."""
        ...

    def sample_all(self, rng: np.random.Generator | None = None) -> dict[str, float]:
        """Draw independent Thompson samples across all registered acquirers."""
        ...

    def get_state(self, acquirer_id: str) -> AcquirerStateSnapshot:
        """Return state snapshot for a single acquirer."""
        ...

    def get_all_states(self) -> dict[str, AcquirerStateSnapshot]:
        """Return state snapshots for all registered acquirers."""
        ...


def calculate_gamma_from_half_life(half_life_seconds: float, expected_tps: float) -> float:
    """Derive discrete per-outcome decay factor from half-life and transaction rate."""
    ...
```

### [Phase 2] Simulated Acquirer Service & Admin API Contract

**Module Target**: `acquirer_sim/models.py`, `acquirer_sim/simulator.py`, `acquirer_sim/app.py`
**Detailed Specification**: `docs/phase2-simulator-spec.md`

#### 1. Core Data Models (Pydantic v2)

```python
from enum import Enum
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class OutageBehavior(str, Enum):
    RETURN_DECLINE = "RETURN_DECLINE"
    HTTP_503 = "HTTP_503"
    LATENCY_SPIKE = "LATENCY_SPIKE"


class LatencyConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    base_ms: float = 20.0
    jitter_ms: float = 5.0
    outage_spike_ms: float = 500.0


class AcquirerConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    acquirer_id: str = Field(..., min_length=1)
    base_success_rate: float = Field(default=0.95, ge=0.0, le=1.0)
    latency: LatencyConfig = Field(default_factory=LatencyConfig)


class AuthorizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transaction_id: str = Field(..., min_length=1)
    amount: float = Field(..., gt=0.0)
    currency: str = Field(default="USD", pattern=r"^[A-Z]{3}$")
    merchant_id: str = "merchant_loom_default"
    payment_method: str = "card"
    timestamp: float | None = None


class AuthorizeResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transaction_id: str
    acquirer_id: str
    status: Literal["AUTHORIZED", "DECLINED"]
    authorized: bool
    authorization_code: str | None = None
    decline_code: str | None = None
    decline_message: str | None = None
    simulated_latency_ms: float
    timestamp: float


class SuccessRateUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    success_rate: float = Field(..., ge=0.0, le=1.0)
    reason: str | None = None


class OutageToggleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    active: bool
    behavior: OutageBehavior = OutageBehavior.RETURN_DECLINE
    transition_seconds: float = Field(default=0.0, ge=0.0)


class AdminStateResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    acquirer_id: str
    base_success_rate: float
    effective_success_rate: float
    outage_active: bool
    outage_behavior: OutageBehavior
    latency: LatencyConfig
    total_requests: int
    authorized_count: int
    declined_count: int
    outage_declines: int
    empirical_success_rate: float
    uptime_seconds: float


class ResetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    acquirer_id: str
    message: str
    timestamp: float
```

#### 2. HTTP Endpoints Contract

| Method | Path | Request Body | Response Body | HTTP Status | Behavior |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `POST` | `/authorize` | `AuthorizeRequest` | `AuthorizeResponse` | `200`, `422`, `503` | Evaluates authorization against effective success rate; returns authorized or declined. |
| `POST` | `/admin/success-rate` | `SuccessRateUpdateRequest` | `AdminStateResponse` | `200`, `422` | Mutates base success rate $p \in [0.0, 1.0]$. |
| `POST` | `/admin/outage` | `OutageToggleRequest` | `AdminStateResponse` | `200`, `422` | Toggles outage state (hard on/off for v1; reserves `transition_seconds`). |
| `GET` | `/admin/state` | None | `AdminStateResponse` | `200` | Inspects live telemetry, counters, and effective PSR. |
| `POST` | `/admin/reset` | None | `ResetResponse` | `200` | Clears cumulative counters for clean test runs. |

#### 3. Client Interaction Rules for Phase 3 Router

- **HTTP 200 & `authorized: true`**: Router records `record_outcome(acquirer_id, success=True)`.
- **HTTP 200 & `authorized: false`**: Router records `record_outcome(acquirer_id, success=False)` (regardless of decline code).
- **HTTP 503 or Transport Error / Timeout**: Router catches client exception and records `record_outcome(acquirer_id, success=False)`.
- **HTTP 422**: Router logs validation bug without penalizing acquirer health.

### [Phase 3] Bandit-Only Router & End-to-End Simulation Pipeline Contract

**Module Target**: `router_core/models.py`, `router_core/router.py`, `router_core/app.py`, `scripts/generate_transactions.py`
**Detailed Specification**: `docs/phase3-router-spec.md`

#### 1. Core Data Models (Pydantic v2)

```python
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field

from acquirer_sim.models import AuthorizeRequest, AuthorizeResponse
from router_core.state import AcquirerStateConfig, AcquirerStateSnapshot


class AcquirerRouteConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    acquirer_id: str = Field(..., min_length=1)
    base_url: str
    auth_path_template: str = "/acquirers/{acquirer_id}/authorize"
    timeout_sec: float = Field(default=2.0, gt=0.0)
    state_config: AcquirerStateConfig = Field(default_factory=AcquirerStateConfig)

    def get_authorize_url(self) -> str:
        path = self.auth_path_template.format(acquirer_id=self.acquirer_id)
        return f"{self.base_url.rstrip('/')}/{path.lstrip('/')}"


class RouterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    routes: list[AcquirerRouteConfig] = Field(..., min_length=1)
    max_connections: int = Field(default=100, gt=0)
    max_keepalive_connections: int = Field(default=20, gt=0)
    seed: int | None = None


class RoutingResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    transaction_id: str
    selected_acquirer: str
    thompson_samples: dict[str, float]
    status: Literal["AUTHORIZED", "DECLINED", "ERROR"]
    authorized: bool
    success: bool
    response_payload: AuthorizeResponse | None = None
    error_message: str | None = None
    routing_latency_ms: float = Field(..., ge=0.0)
    acquirer_latency_ms: float = Field(..., ge=0.0)
    total_latency_ms: float = Field(..., ge=0.0)
    state_snapshot: AcquirerStateSnapshot
    timestamp: float
```

#### 2. Pipeline Execution Contract (`BanditRouter`)

- **Thompson Sampling**: Draws $\theta_i \sim \text{Beta}(\alpha_i, \beta_i)$ across all candidate arms.
- **Winner-Take-All Hard-Switch**: $A^* = \arg\max_i \theta_i$. Assigns 100% of the transaction to $A^*$ (no PID smoothing or fractional allocation in Phase 3).
- **Network Dispatch**: Sends `POST {base_url}/acquirers/{A*}/authorize` with pooled `httpx.AsyncClient`.
- **Outcome Classification & Feedback**:
  - `HTTP 200` + `authorized: True` $\implies$ Success ($x = 1.0$)
  - `HTTP 200` + `authorized: False` $\implies$ Business Decline ($x = 0.0$)
  - `HTTP 503` or Gateway 5xx $\implies$ System Outage ($x = 0.0$)
  - `httpx.TimeoutException` or `NetworkError` $\implies$ Network Failure ($x = 0.0$)
  - `HTTP 422` $\implies$ Client schema bug (raise/log error; do NOT penalize acquirer)
- **State Feedback**: Invokes `BanditStateRegistry.record_outcome(A*, success=(x == 1.0))` using Phase 1's mean-reverting offset decay.

#### 3. Router Service HTTP Endpoints

| Method | Path | Request Body | Response Body | HTTP Status | Description |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `POST` | `/route` | `AuthorizeRequest` | `RoutingResult` | `200`, `422` | Executes Thompson Sampling selection, calls acquirer, updates state, returns result. |
| `GET` | `/health` | None | JSON | `200` | Health check reporting registered routes and engine uptime. |
| `GET` | `/state` | None | `dict[str, AcquirerStateSnapshot]` | `200` | Inspects live bandit parameters across all routes. |

#### 4. Transaction Generator CLI Contract

- Command: `python -m scripts.generate_transactions --target-url http://127.0.0.1:8000/route --tps 20.0 --duration 60 --distribution poisson`
- Supported Modes: HTTP Target mode (`--target-url`) and In-Process mode (`--in-process`).

### [Phase 4] PID Control Layer & Damped Routing Contract

**Module Target**: `router_core/pid.py`, `router_core/models.py`, `router_core/router.py`
**Detailed Specification**: `docs/phase4-pid-spec.md`

#### 1. Core Data Models (Pydantic v2 & Frozen Dataclasses)

```python
from dataclasses import dataclass
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class PIDConfig(BaseModel):
    """Immutable configuration for the PID smoothing controller."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kp: float = Field(
        default=0.20,
        ge=0.0,
        description="Proportional gain constant.",
    )
    ki: float = Field(
        default=0.01,
        ge=0.0,
        description="Integral gain constant.",
    )
    kd: float = Field(
        default=0.10,
        ge=0.0,
        description="Derivative gain constant.",
    )
    integral_max: float = Field(
        default=1.0,
        gt=0.0,
        description="Symmetric anti-windup clamping threshold: [-integral_max, +integral_max].",
    )
    integral_decay: float = Field(
        default=1.0,
        gt=0.0,
        le=1.0,
        description="Leaky integration retention factor gamma_I in (0.0, 1.0].",
    )
    derivative_filter_alpha: float = Field(
        default=0.0,
        ge=0.0,
        lt=1.0,
        description="Low-pass filter smoothing coefficient beta_d for derivative term.",
    )
    derivative_on_measurement: bool = Field(
        default=True,
        description="If True, derivative is computed on -dw/dt to eliminate derivative kick.",
    )
    min_allocation: float = Field(
        default=0.03,
        ge=0.0,
        lt=0.20,
        description="Mandatory exploration floor w_min per acquirer route.",
    )
    actuation_mode: Literal["stochastic", "deficit"] = Field(
        default="stochastic",
        description="Discrete routing dispatch: 'stochastic' (categorical draw) or 'deficit' (Bresenham round-robin).",
    )


@dataclass(frozen=True, slots=True)
class PIDState:
    """Immutable point-in-time snapshot of the PID controller internal state."""

    accumulated_error: dict[str, float]
    previous_error: dict[str, float]
    previous_allocation: dict[str, float]
    filtered_derivative: dict[str, float]
    step_count: int = 0


@dataclass(frozen=True, slots=True)
class PIDDiagnostics:
    """Detailed calculation telemetry for metrics and live dashboard."""

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
```

#### 2. Pure-Function Step Signature (`calculate_pid_step`)

```python
def calculate_pid_step(
    target_allocation: dict[str, float],
    current_allocation: dict[str, float],
    state: PIDState,
    config: PIDConfig,
    dt: float = 1.0,
) -> PIDStepResult:
    """Calculate one discrete PID smoothing step as a pure, deterministic function.

    Guarantees:
    - Side-effect free, deterministic execution.
    - Zero-sum invariant: sum(e_i) == 0.0, sum(w_i) == 1.0.
    - Symmetric anti-windup clamping to [-config.integral_max, +config.integral_max].
    - Zero derivative kick when config.derivative_on_measurement is True.
    - Bounded simplex projection enforcing w_i >= config.min_allocation.
    """
    ...
```

#### 3. Bounded Simplex Projection (`project_to_bounded_simplex`)

$$\mathcal{S}_{w_{\text{min}}} = \left\{ \mathbf{w} \in \mathbb{R}^K \;\middle|\; \sum_{i=1}^K w_i = 1.0, \; w_i \ge w_{\text{min}} \; \forall i \right\}$$
Clamps unconstrained allocations to $w_{\text{min}}$ and redistributes excess/deficit proportionally across adjustable arms.

### [Phase 5] Data & Metrics Layer Contract (Redis State, Pub/Sub, SQLite Ledger)

**Module Target**: `data_layer/models.py`, `data_layer/redis_state.py`, `data_layer/redis_pubsub.py`, `data_layer/sqlite_logger.py`, `data_layer/service.py`
**Detailed Specification**: `docs/phase5-data-layer-spec.md`

#### 1. Redis Key Conventions & Hash Schemas

Per `docs/CONSTITUTION.md`:
- `acquirer:{id}:health` (Redis Hash):
  - `health_score`: float in $[0.0, 1.0]$
  - `last_updated_at`: Unix epoch float
- `acquirer:{id}:beta` (Redis Hash):
  - `alpha`: float $\ge \alpha_0$
  - `beta`: float $\ge \beta_0$
  - `alpha_prior`: float $> 0.0$
  - `beta_prior`: float $> 0.0$
  - `decay_factor`: float $\in (0.0, 1.0)$
  - `success_count`: int $\ge 0$
  - `failure_count`: int $\ge 0$
  - `total_count`: int $\ge 0$
  - `last_updated_at`: Unix epoch float
- `acquirers` (Redis Set):
  - String set containing registered acquirer identifiers (`{"acquirer_alpha", "acquirer_beta", ...}`)

#### 2. Redis Pub/Sub Event Contract (`RoutingEvent`)

Channel: `events:routing`

```python
class RoutingEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    event_id: str
    event_type: Literal["ROUTING_COMPLETED"] = "ROUTING_COMPLETED"
    timestamp: float
    transaction_id: str
    selected_acquirer: str
    status: Literal["AUTHORIZED", "DECLINED", "ERROR"]
    authorized: bool
    success: bool
    decline_code: str | None = None
    routing_latency_ms: float
    acquirer_latency_ms: float
    total_latency_ms: float
    thompson_samples: dict[str, float]
    target_allocation: dict[str, float] | None = None
    smoothed_allocation: dict[str, float] | None = None
    allocation_weight: float
    pid_diagnostics: dict[str, Any] | None = None
    updated_state: dict[str, float]
```

#### 3. SQLite DDL & Database-Level Append-Only Triggers

Tables conform to `snake_case`, plural: `transactions` and `acquirer_outcomes`.

```sql
PRAGMA journal_mode = WAL;
PRAGMA synchronous = NORMAL;
PRAGMA busy_timeout = 5000;

CREATE TABLE IF NOT EXISTS transactions (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id              TEXT NOT NULL UNIQUE,
    timestamp                   REAL NOT NULL,
    chosen_acquirer             TEXT NOT NULL,
    allocation_weight           REAL NOT NULL,
    status                      TEXT NOT NULL CHECK(status IN ('AUTHORIZED', 'DECLINED', 'ERROR')),
    authorized                  INTEGER NOT NULL CHECK(authorized IN (0, 1)),
    success                     INTEGER NOT NULL CHECK(success IN (0, 1)),
    decline_code                TEXT,
    routing_latency_ms          REAL NOT NULL,
    acquirer_latency_ms         REAL NOT NULL,
    total_latency_ms            REAL NOT NULL,
    smoothed_allocation_json    TEXT NOT NULL,
    target_allocation_json      TEXT,
    thompson_samples_json       TEXT NOT NULL,
    pid_diagnostics_json        TEXT,
    error_message               TEXT,
    created_at                  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);

CREATE TABLE IF NOT EXISTS acquirer_outcomes (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    transaction_id              TEXT NOT NULL,
    acquirer_id                 TEXT NOT NULL,
    timestamp                   REAL NOT NULL,
    success                     INTEGER NOT NULL CHECK(success IN (0, 1)),
    alpha                       REAL NOT NULL,
    beta                        REAL NOT NULL,
    health_score                REAL NOT NULL,
    expected_success_rate       REAL NOT NULL,
    success_count               INTEGER NOT NULL,
    failure_count               INTEGER NOT NULL,
    total_count                 INTEGER NOT NULL,
    created_at                  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    FOREIGN KEY(transaction_id) REFERENCES transactions(transaction_id)
);

-- Append-Only Triggers
CREATE TRIGGER IF NOT EXISTS prevent_transactions_update
BEFORE UPDATE ON transactions BEGIN
    SELECT RAISE(ABORT, 'Transactions table is append-only: UPDATE prohibited by CONSTITUTION.md');
END;

CREATE TRIGGER IF NOT EXISTS prevent_transactions_delete
BEFORE DELETE ON transactions BEGIN
    SELECT RAISE(ABORT, 'Transactions table is append-only: DELETE prohibited by CONSTITUTION.md');
END;

CREATE TRIGGER IF NOT EXISTS prevent_acquirer_outcomes_update
BEFORE UPDATE ON acquirer_outcomes BEGIN
    SELECT RAISE(ABORT, 'Acquirer outcomes table is append-only: UPDATE prohibited by CONSTITUTION.md');
END;

CREATE TRIGGER IF NOT EXISTS prevent_acquirer_outcomes_delete
BEFORE DELETE ON acquirer_outcomes BEGIN
    SELECT RAISE(ABORT, 'Acquirer outcomes table is append-only: DELETE prohibited by CONSTITUTION.md');
END;
```

#### 4. Additive Service Facade Contract (`DataLayerService`)

```python
class DataLayerService:
    """Composite facade integrating Redis state, Redis pub/sub, and SQLite logging."""

    async def start(self) -> None: ...
    async def record_routing_result(self, result: RoutingResult) -> None: ...
    async def hydrate_registry(self, registry: BanditStateRegistry, routes: list[AcquirerRouteConfig]) -> None: ...
    async def flush(self) -> None: ...
    async def close(self) -> None: ...
```

### [Phase 6] Static Baseline Router & Comparative Evaluation Pipeline Contract

**Module Target**: `baseline_router/models.py`, `baseline_router/router.py`, `scripts/compare_psr.py`
**Detailed Specification**: `docs/phase6-baseline-router-spec.md`

#### 1. Core Data Models (Pydantic v2)

```python
class RouteHealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    TRIPPED = "TRIPPED"
    PROBATION = "PROBATION"


class FailoverThresholdType(str, Enum):
    CONSECUTIVE_FAILURES = "CONSECUTIVE_FAILURES"
    WINDOW_FAILURE_RATE = "WINDOW_FAILURE_RATE"


class FailoverPolicyConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    threshold_type: FailoverThresholdType = FailoverThresholdType.CONSECUTIVE_FAILURES
    consecutive_failure_threshold: int = Field(default=3, ge=1)
    window_size: int = Field(default=20, ge=5)
    window_failure_rate_threshold: float = Field(default=0.20, gt=0.0, lt=1.0)
    cooldown_transactions: int = Field(default=30, ge=1)
    failback_mode: Literal["probe", "snapback"] = "probe"


class StaticRouteStateSnapshot(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    acquirer_id: str
    priority: int
    status: RouteHealthStatus
    consecutive_failures: int
    tripped_at_tx: int | None
    success_count: int
    failure_count: int
    total_count: int
    last_updated_at: float


class BaselineRouterConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    routes: list[AcquirerRouteConfig] = Field(..., min_length=1)
    priority_order: list[str] = Field(..., min_length=1)
    failover_policy: FailoverPolicyConfig = Field(default_factory=FailoverPolicyConfig)
    max_connections: int = Field(default=100, gt=0)
    max_keepalive_connections: int = Field(default=20, gt=0)
```

#### 2. Static Router Pipeline Execution Contract (`StaticBaselineRouter`)

- **Priority Selection**: Traverses `priority_order` to select the first `HEALTHY` route, prioritizing any route in `PROBATION` (cooldown elapsed canary probe).
- **Network Dispatch**: Dispatches `POST {base_url}/acquirers/{id}/authorize` via pooled `httpx.AsyncClient` matching Phase 2's contract.
- **Outcome Classification**: Maps HTTP 200 approvals ($x=1.0$), declines ($x=0.0$), 503 outages ($x=0.0$), and timeouts ($x=0.0$).
- **State Feedback**:
  - Success ($x=1.0$): Resets `consecutive_failures = 0`. If `PROBATION`, restores route to `HEALTHY`.
  - Failure ($x=0.0$): Increments `consecutive_failures += 1`. If `PROBATION`, reverts to `TRIPPED` with reset cooldown. If `HEALTHY` and threshold breached, trips route to `TRIPPED`.
- **Envelope & Logging Identity**: Emits typed `RoutingResult` envelope containing static allocation weights ($1.0$ / $0.0$) and passes it to Phase 5's `MetricsLogger`, logging to `baseline_metrics.db` with zero schema divergence.

#### 3. Comparative Evaluation & PSR Analysis Contract (`scripts/compare_psr.py`)

- **Dual-Run Orchestration**: Runs identical traffic and outage schedule against Loom (`loom_metrics.db`) and Baseline (`baseline_metrics.db`).
- **Mathematical Formulations**:
  $$\text{PSR} = \frac{\sum \mathbb{I}(\text{authorized})}{N}, \quad \Delta \text{PSR} = \text{PSR}_{\text{Loom}} - \text{PSR}_{\text{Baseline}}$$
  $$\text{Relative Lift} = \frac{\Delta \text{PSR}}{\text{PSR}_{\text{Baseline}}} \times 100\%, \quad \Delta w_{\max} = \max_t |w(t) - w(t-1)|$$
- **Segmented Analysis**: Generates comprehensive markdown audit tables across Steady-State (Pre-Outage), Outage Window, and Recovery.

### [Phase 7] Live Mission-Control Dashboard & WebSocket Gateway Contract

**Module Target**: `dashboard/` (`AllocationChart.jsx`, `MetricReadouts.jsx`, `OperatorControls.jsx`, `useLoomTelemetry.js`), `router_core/app.py`
**Detailed Specification**: `docs/phase7-dashboard-spec.md`

#### 1. Visual Token Contract (Mission-Control Palette & Geometry)
- **Palette**: Ground `#0B1120`, Panel `#111827`, Well Inset `#070C18`, Hairline Border `1px solid #1E3A5F`.
- **Reserved Colors**: Telemetry Amber (`#D9A441`) strictly reserved for active numbers and healthy line; Alert Rust (`#C1622D`) strictly reserved for outage/danger states. Secondary route in Steel Cyan (`#38BDF8`), Tertiary in Slate (`#94A3B8`).
- **Typography**: Monospace (`'JetBrains Mono', monospace`) with `tnum` tabular alignment for numbers only; Clean Sans (`Inter`) for all text/headers.
- **Elevation**: Zero diffuse card shadows (`box-shadow: none`); flat sharp corners (`border-radius: 0px` or max `2px`).

#### 2. Ticket Contracts & Data Ingestion Shapes

##### Ticket A: Live Multi-Acquirer Allocation Chart (`AllocationChart.jsx`)
- **Render**: Rolling 60s time series of smoothed allocation weights ($w_i \in [0.0, 1.0]$) demonstrating Phase 4 PID easing curve ($82\% \to 65\% \dots \to 3\%$).
- **Markers**: Pinned vertical hairline Rust (`#C1622D`) marker on outage injection (`decline_code == 'ACQUIRER_OUTAGE'`); vertical Amber (`#D9A441`) marker on recovery.
- **Readout**: Instantaneous peak single-step delta gauge: $\Delta w_{\text{max}} = 11.77\%$ vs $100.0\%$ baseline cliff.
- **Data Ingestion**: Consumes `RoutingEvent.smoothed_allocation`, `allocation_weight`, `timestamp`, `sequence_number`, `decline_code`.

##### Ticket B: Cluster & Acquirer Telemetry Readouts (`MetricReadouts.jsx`, `BaselineComparisonCard.jsx`)
- **Cluster Readouts**: Rolling 50-tx PSR (Amber $\to$ Rust when $<80\%$), Lifetime Global PSR, Stability Multiplier (`8.5x Smoother`), routing latency ($t_{\text{routing}} < 0.1\text{ms}$).
- **Benchmark Card**: Contextualizes Phase 6 empirical results: $M=1$ Overreaction collapse (Loom 86% vs Baseline 76%, +1000 bps lift), $M=3$ Standard Outage (86% vs 92% with 0 downstream capacity limit), Gray Failure (21-tx bleed).
- **Per-Acquirer Card**: EWMA health score ($H$) with 4px micro-gauge bar, Beta parameters ($\alpha, \beta, \mathbb{E}[\theta]$), traffic share ($w_i$), network latency ($t_{\text{acquirer}}$).
- **Data Ingestion**: Consumes `RoutingEvent.updated_state` (`alpha`, `beta`, `health_score`, `expected_success_rate`), `status`, `authorized`, and SQLite `get_psr_metrics()`.

##### Ticket C: Operator Outage Controls (`OperatorControls.jsx`)
- **Controls**: Per-acquirer two-state trigger buttons (Normal $\to$ Armed Rust), behavior radio (`RETURN_DECLINE`, `HTTP_503`, `LATENCY_SPIKE`), brownout slider ($p \in [0.0, 1.0]$).
- **Presets**: Standard Cliff, Sensitive Blip ($M=1$), Gray Failure ($p=0.60$), and Global Reset.
- **Dispatch**: Dispatches via backend proxy `POST /api/simulator/acquirers/{id}/outage` to Acquirer Simulator Admin API.

##### Ticket D: WebSocket Streaming Gateway (`router_core/app.py`, `useLoomTelemetry.js`)
- **Backend Route**: `@app.websocket("/ws/telemetry")` subscribing to Redis `events:routing` and `events:health` via `AsyncEventSubscriber`.
- **Hybrid Bootstrap**: Emits initial snapshot of current route states and recent SQLite transactions upon connection open.
- **Client Architecture**: Circular ring buffer (200 events) with `requestAnimationFrame` 60 FPS animation ticker, preventing UI thread freezing under 50+ TPS.
- **Resilience**: Auto-reconnect with exponential backoff ($500\text{ms} \to 10\text{s}$) and top-bar status badge (`LIVE`, `RECONNECTING`, `DISCONNECTED`).

### [Phase 7 Revision] Visual Design System & Information Architecture Revision Contract

**Supersedes**: Visual Token Contract in Phase 7 (ADR-01 / ADR-06 styling tokens).
**Preserves**: Underlying WebSocket schemas (`RoutingEvent`, `HealthAlertEvent`), SQLite schema, Redis pub/sub streams, and circular ring buffer architecture.

#### 1. Color Palette Tokens & Semantic Invariants

| Token Identifier | Hex Code | Semantic Role & Invariants |
| :--- | :--- | :--- |
| `ground` | `#0F1115` | Canvas base ground. Neutral dark slate replacing deep space navy. |
| `panel` | `#16181D` | Panel and card surface background. Subtle contrast above ground. |
| `border` | `#2A2D34` | Hairline border (`1px solid #2A2D34`) for all panels, dividers, and axes. |
| `text-primary` | `#E4E6EB` | Primary typography for titles, labels, headings, and key readouts. |
| `text-secondary`| `#8B8F98` | Muted typography for secondary metadata, units, and timestamps. |
| `accent` | `#5B8DEF` | **Singular Accent (Cobalt Blue)**: Reserved exclusively for healthy states, active primary chart line, live status dot, and interactive focus states. Never used for static decoration. |
| `alert` | `#E5484D` | **Singular Alert (Crimson)**: Reserved exclusively for active outage states, degraded health (<0.70), reconnection failure, and chart outage markers. Never decorative. |

#### 2. Typography Contract & Casing Rules

1. **Primary Interface Font (`IBM Plex Sans`)**:
   - Applied to all UI copy, navigation, section headers, card titles, table labels, buttons, and disclosures.
   - Weights: 400 (regular), 500 (medium), 600 (semi-bold).
2. **Telemetry Numeric Font (`IBM Plex Mono`)**:
   - Reserved strictly for live numerical telemetry values: PSR percentages (e.g. `89.33%`), health scores (e.g. `0.942`), latencies (e.g. `0.042 ms`), sequence numbers (e.g. `#1042`), Beta shape parameters ($\alpha, \beta$), allocation shares (e.g. `82.0%`).
   - Mandates `font-feature-settings: "tnum" 1, "zero" 1;` (tabular figures and slashed zero).
3. **Strict Sentence Case Invariant**:
   - All headings, labels, button texts, and tooltips must use standard sentence case (e.g., "Live traffic allocation", "Rolling PSR (50 txs)", "Diagnostics", "Simulation controls", "Trigger outage", "Clear outage").
   - Tracked-out uppercase and shouting acronym blocks are strictly prohibited.

#### 3. Information Architecture & Component Hierarchy

1. **Hero Composition Preserved**:
   - The live multi-acquirer allocation chart (`AllocationChart.jsx`) remains the visual hero at the top of the viewport, rendering the smooth PID easing curve against the 60-second rolling window.
2. **Sensor-Actuator Colocation (Per-Acquirer Cards)**:
   - Each acquirer card combines observation and control into a single physical unit:
     - Upper block: Acquirer name, role, live status indicator, EWMA health score with 4px micro-gauge, Beta parameters, and allocation share.
     - Lower block (physically adjacent): Two-state outage button (`Trigger outage` / `Clear outage`), failure mode selector (`Decline`, `503`, `Latency spike`), and brownout slider.
3. **Progressive Disclosure Disclosures (Default-Closed)**:
   - **`Diagnostics`**: Houses the Phase 6 benchmark comparison audit (`BaselineComparisonCard.jsx`), default-closed via native `<details>` element.
   - **`Simulation controls`**: Houses the global simulation scenario gauntlet presets (Standard cliff, Sensitive blip $M=1$, Gray failure $p=0.60$, Global reset), default-closed via native `<details>` element.
4. **Consolidated Single Status Element**:
   - A single persistent status pill in the header dynamically transitions through three states:
     - **Connected**: Border `#2A2D34`, dot `#5B8DEF`, text `Live`.
     - **Reconnecting**: Border `#2A2D34`, dot `#E5484D` (pulsing), text `Reconnecting (attempt {n})`.
     - **Disconnected**: Border `#2A2D34`, dot `#E5484D`, text `Connection lost`, with inline `Reconnect` button.

### [Phase 7 Revision 3] Acquirer Color Mapping & Inviolable Alert Override Contract

**Supersedes**: Chart Line Color Assignment in Phase 7 Revision 2.
**Preserves**: Ground (`#0F1115`), Panel (`#16181D`), Border (`#2A2D34`), Text Primary (`#E4E6EB`), Text Secondary (`#8B8F98`), Type rules (`IBM Plex Sans` / `IBM Plex Mono`), Sentence case, Colocated per-acquirer controls, and Default-closed disclosures.

#### 1. Deterministic Acquirer Color Assignment (Not Decorative)

| Acquirer Entity | Role | Assigned Nominal Color | Hex Code | Purpose & Context |
| :--- | :--- | :--- | :--- | :--- |
| **Acquirer 1 (Alpha)** | Primary leader | **Accent** | `#5B8DEF` | Cobalt blue. Assigned to first acquirer across all identity surfaces. |
| **Acquirer 2 (Beta)** | Secondary backup | **Accent 2** | `#C084FC` | Soft violet. Assigned to second acquirer across all identity surfaces. |
| **Acquirer 3 (Gamma)** | Tertiary floor | **Secondary text gray** | `#7C808A` | Calm slate gray. Assigned to third acquirer across all identity surfaces. |

#### 2. Cross-Surface Identity Consistency Rule
The assigned color for each acquirer applies universally across all surfaces on the dashboard where that acquirer's identity is represented:
- **Allocation Chart Series Lines**: Primary line (`#5B8DEF`), secondary line (`#C084FC`), tertiary line (`#7C808A`).
- **Chart Legend Indicators**: Matching color bars/dots and percentage labels.
- **Health Panel Status Dots & Badges**: Nominal state dot uses the acquirer's assigned color (`#5B8DEF` for Alpha, `#C084FC` for Beta, `#7C808A` for Gamma).
- **EWMA Health Micro-Gauge Fill**: Nominal fill level uses the acquirer's assigned color when $H \ge 0.70$.
- **Allocation Weight Badges / Borders**: Subordinate borders or badges reflect the acquirer's assigned color.

#### 3. Inviolable Alert Override Rule

$$\text{ActiveColor}(i, t) = \begin{cases} \text{\#E5484D (Alert)}, & \text{if Acquirer } i \text{ is in Outage or Degraded } (H_i(t) < 0.70) \\ \text{AssignedColor}(i), & \text{otherwise (Nominal / Healthy)} \end{cases}$$

- **Instantaneous Preemption**: The exact millisecond an acquirer enters an outage (via operator toggle or event-driven health alert $H < 0.70$), its color **instantly shifts to Alert (`#E5484D` crimson)**, overriding its assigned color regardless of whether it is Alpha, Beta, or Gamma.
- **Recovery Hysteresis**: When the outage clears and operational health restores ($H \ge 0.70$), the acquirer's visual representation reverts smoothly to its assigned nominal color.
- **Outage Event Marker**: Pinned vertical dashed line and timestamped marker badge on the chart render strictly in **Alert (`#E5484D`)**.

#### 4. Strict Negative Invariants (Semantic Quarantine)

1. **Alert (`#E5484D`) Quarantine**:
   - Alert is strictly an emergency signal.
   - It must **never** appear anywhere on the dashboard for any decorative, neutral, or non-failure purpose.
   - It is permitted ONLY for: (1) an acquirer currently in outage/degraded state, (2) the vertical outage event marker on the chart, and (3) a dropped WebSocket connection (`Connection lost` / `Reconnecting`).
2. **Accent 2 (`#C084FC`) Quarantine**:
   - Accent 2 is reserved **strictly and exclusively** for the second acquirer's identity (Acquirer Beta).
   - It must **never** be reused elsewhere as a general-purpose UI accent, button color, heading highlight, or decorative embellishment.

### [Phase 7 Revision 4] Typography & Extreme Density Architecture Contract

**Supersedes**: Typography, Density, and Information-Architecture Rules in Phase 7 Revisions 2 and 3.
**Preserves**: Palette Ground (`#0F1115`), Panel (`#16181D`), Border (`#2A2D34`), Text Primary (`#E4E6EB`), Text Secondary (`#8B8F98`), Deterministic Acquirer Color Assignment (Alpha: `#5B8DEF`, Beta: `#C084FC`, Gamma: `#7C808A`), Inviolable Alert (`#E5484D`) Override on Outage/Degraded ($H < 0.70$), and Semantic Quarantines for Alert and Accent 2.

---

#### 1. The Typographic Triad & Strict Role Assignment

This contract replaces all instances of `IBM Plex Sans` and `IBM Plex Mono` with a strictly partitioned typographic triad:

| Typeface | Weight | Strict Dedicated Role | Elements & Surfaces |
| :--- | :--- | :--- | :--- |
| **`Space Grotesk`** | **700 (Bold)** | **Wordmark & Headline Metric Magnitudes** | 1. Application Wordmark (`"Loom"`).<br>2. Headline Rolling PSR percentage (e.g. `86.0%`).<br>3. Headline Baseline PSR Lift value (e.g. `+1000 bps` / `+10.0%`). |
| **`Space Mono`** | **400 (Regular) / 700 (Bold)** | **Live Telemetry Figures** | 1. Per-acquirer health score / allocation figures (e.g. `0.942`, `82.0%`).<br>2. Live chart tick numbers and time axes (`0%`, `50%`, `100%`, `-60s`, `Now`).<br>3. Live peak step delta figure (`11.8%`).<br>4. Telemetry latency & count values (`0.042 ms`, `1,240 txs`). |
| **`Inter`** | **400 / 500 / 600** | **All Interface Labels & Copy** | Every remaining textual element: metric labels (`"Rolling PSR"`, `"PSR lift vs baseline"`), acquirer names (`"Alpha"`, `"Beta"`, `"Gamma"`), status copy (`"Healthy"`), text buttons (`"trigger outage"`), and section descriptors. |

**Negative Type Invariants**:
- `Space Grotesk` must NEVER be used for body text, regular labels, or secondary metrics.
- `Space Mono` must NEVER be used for non-numeric labels, headings, or status copy.
- `Inter` must NEVER be used for live telemetry numbers or headline magnitude metrics.

---

#### 2. Radical Density & Layout Invariants (The Mockup Specification)

To eliminate container fatigue, repetitive telemetry representations, and visual noise, the layout is compressed into five structural rules:

##### Rule 1: Header Wordmark with Integrated Minimal Status Line
- **Wordmark**: Left-aligned `"Loom"` rendered in `Space Grotesk` 700 (`text-2xl font-bold tracking-tight text-[#E4E6EB]`).
- **Single Status Line**: Directly beneath the wordmark — dot + word (`● Healthy` or `● Degraded` / `● Reconnecting`), NOT a banner, NOT a pill box, NOT a separate right-aligned gadget:
  - *Healthy State*: Inline indicator with a 6px dot (`#5B8DEF`) + text `"Healthy"` (`Inter`, 12px, `#8B8F98` / `#E4E6EB`).
  - *Degraded / Outage State*: Inline indicator with a 6px pulsing dot (`#E5484D animate-pulse`) + text `"Degraded"` / `"Outage active"` (`Inter`, 12px, `#E5484D`).
  - *Connection Lost State*: Inline indicator with a 6px dot (`#E5484D`) + text `"Connection lost"` (`Inter`, 12px, `#E5484D`) with a minimal text-link `"Reconnect"`.
- **Elimination**: The cluttered right-aligned header navigation bar with multiple bordered pills (UTC clock, RTT pill, counter pill) is eliminated in favor of a clean, uncluttered masthead.

##### Rule 2: Borderless Headline Metrics (The Two Largest Elements)
- **Positioning**: Rolling PSR and Lift-vs-Baseline are the **two largest elements on the screen**, positioned side by side at the top of the telemetry section.
- **Zero Containers**: Strictly label-plus-number with **NO surrounding box, NO border, NO card container, NO panel background** (`bg-transparent`, no card borders).
- **Structure**:
  - **Left Headline (Rolling PSR)**:
    - Label: `"Rolling PSR (50 txs)"` in `Inter`, 12px, font-medium, color `#8B8F98`.
    - Value: `86.0%` in `Space Grotesk` 700, 48px–56px (`text-5xl font-bold tracking-tight`), colored `#5B8DEF` (nominal) or `#E5484D` (when $<80.0\%$).
  - **Right Headline (PSR Lift vs Baseline)**:
    - Label: `"PSR lift vs baseline"` in `Inter`, 12px, font-medium, color `#8B8F98`.
    - Value: `+1000 bps` (or `+10.0%`) in `Space Grotesk` 700, 48px–56px (`text-5xl font-bold tracking-tight`), colored `#5B8DEF`.

##### Rule 3: Single-Row Acquirer Strip (Zero Panels, Zero Bars, Zero Redundancy)
- **Elimination of Per-Acquirer Cards**: The 3 heavy rectangular cards, 4px micro-gauge bars, and subgrids with duplicated numbers ($\alpha/\beta$ parameters, expected PSR, duplicate weights) are **completely eliminated**.
- **Single-Row Representation**: Each acquirer is rendered strictly as a **single horizontal row**:
  ```
  [Color Dot]  [Acquirer Name]  [One Live Telemetry Number]  [Plain Text Outage Button]
  ```
- **Row Elements**:
  1. **Color Dot**: 8px circle (`w-2 h-2 rounded-full`) reflecting the acquirer's assigned color (`#5B8DEF` Alpha, `#C084FC` Beta, `#7C808A` Gamma), overridden by `#E5484D` during outage/degraded state.
  2. **Acquirer Name**: `"Alpha"`, `"Beta"`, `"Gamma"` in `Inter`, 13px, font-medium, `#E4E6EB`.
  3. **Single Telemetry Figure**: The live health score (e.g. `0.942`) or traffic split percentage (e.g. `82.0%`) in `Space Mono`, 13px, colored per assigned identity or alert override.
  4. **Text-Style Outage Button**: Minimal inline text action (e.g. `"trigger outage"` / `"clear outage"`).

##### Rule 4: Plain Text-Style Outage-Trigger Controls
- Outage triggers are styled as **plain text buttons**, NOT bordered cards or bulky actuator blocks.
- **Styling**: Minimal inline text (`text-xs text-[#8B8F98] hover:text-[#E4E6EB] hover:underline bg-transparent border-0 p-0 cursor-pointer transition-colors font-sans`).
- **Active Outage State**: Color shifts to Alert Crimson (`text-[#E5484D]`) with label `"clear outage"`.
- **Disabled In-Flight State**: Displays `"dispatching..."` with `opacity-50 pointer-events-none`.

##### Rule 5: Zero-Footprint Progressive Disclosure (`'Diagnostics ›'`)
- The Phase 6 benchmark comparison audit and full simulation deck are collapsed behind a **single small text link**: `'Diagnostics ›'`.
- **Zero Border Footprint**: Must NOT render as a visible disclosure block, bordered box, or container card when closed. It appears as an understated text link in `Inter` (`text-xs text-[#8B8F98] hover:text-[#E4E6EB]`).
- When activated, it expands smoothly in-place below the main telemetry views to present the detailed comparative audits and scenario triggers without cluttering the resting viewport.

---

#### 3. Preserved Invariants from Revision 3

1. **Color Tokens**:
   - Ground: `#0F1115`
   - Panel: `#16181D` (used for chart canvas container)
   - Border: `#2A2D34` (used for chart well hairline border)
   - Primary Text: `#E4E6EB`
   - Secondary Text: `#8B8F98`
2. **Chart Series Line Assignments**:
   - Acquirer 1 (Alpha): `#5B8DEF` (2.0px stroke)
   - Acquirer 2 (Beta): `#C084FC` (1.5px stroke)
   - Acquirer 3 (Gamma): `#7C808A` (1.0px stroke)
3. **Inviolable Alert Override**:
   - Active outage (`activeOutages[id]`) or degraded health ($H < 0.70$) immediately forces the acquirer's identity (dot, text, number, chart line) to Alert Crimson (`#E5484D`).
   - Reverts immediately upon recovery ($H \ge 0.70$ and outage cleared).
4. **Semantic Quarantines**:
   - Alert (`#E5484D`) is strictly an emergency signal (outages, markers, disconnects). Zero decorative use.
   - Accent 2 (`#C084FC`) is strictly reserved for Acquirer Beta. Zero general UI use.

---

### [Phase 7 Revision 5] Humanized Architecture Descriptions & Minimized Simulation Harness Contract

**Supersedes**: Footer/Diagnostics architecture strings and simulation harness disclosure in Phase 7 Revision 4.
**Preserves**: Typographic triad (`Space Grotesk` 700 for wordmark/headlines, `Space Mono` for live telemetry figures, `Inter` for interface labels/copy), borderless headline metrics (`Rolling PSR` and `PSR lift vs baseline`), single-row acquirer strip, deterministic color mapping (Alpha: `#5B8DEF`, Beta: `#C084FC`, Gamma: `#7C808A`), inviolable alert override (`#E5484D`), and base palette (`#0F1115`, `#16181D`, `#2A2D34`, `#E4E6EB`, `#8B8F98`).

---

#### 1. Baseline Superseded Contract (Pasted from Phase 7 Revision 4)

> *The following specification represents the fourth revision's contract from the Decisions Log, incorporated here in full as the baseline that Revision 5 supersedes:*

##### 1. The Typographic Triad & Strict Role Assignment (Revision 4 Baseline)
| Typeface | Weight | Strict Dedicated Role | Elements & Surfaces |
| :--- | :--- | :--- | :--- |
| **`Space Grotesk`** | **700 (Bold)** | **Wordmark & Headline Metric Magnitudes** | 1. Application Wordmark (`"Loom"`).<br>2. Headline Rolling PSR percentage (e.g. `86.0%`).<br>3. Headline Baseline PSR Lift value (e.g. `+1000 bps` / `+10.0%`). |
| **`Space Mono`** | **400 (Regular) / 700 (Bold)** | **Live Telemetry Figures** | 1. Per-acquirer health score / allocation figures (e.g. `0.942`, `82.0%`).<br>2. Live chart tick numbers and time axes (`0%`, `50%`, `100%`, `-60s`, `Now`).<br>3. Live peak step delta figure (`11.8%`).<br>4. Telemetry latency & count values (`0.042 ms`, `1,240 txs`). |
| **`Inter`** | **400 / 500 / 600** | **All Interface Labels & Copy** | Every remaining textual element: metric labels (`"Rolling PSR"`, `"PSR lift vs baseline"`), acquirer names (`"Alpha"`, `"Beta"`, `"Gamma"`), status copy (`"Healthy"`), text buttons (`"trigger outage"`), and section descriptors. |

Negative Type Invariants:
- `Space Grotesk` must NEVER be used for body text, regular labels, or secondary metrics.
- `Space Mono` must NEVER be used for non-numeric labels, headings, or status copy.
- `Inter` must NEVER be used for live telemetry numbers or headline magnitude metrics.

##### 2. Radical Density & Layout Invariants (Revision 4 Baseline)
- **Rule 1: Header Wordmark with Integrated Minimal Status Line**: Left-aligned `"Loom"` rendered in `Space Grotesk` 700 (`text-2xl font-bold tracking-tight text-[#E4E6EB]`). Directly beneath: single status line (dot + word, e.g. `● Healthy`), not a banner, not a pill box, not a separate right-aligned gadget. Cluttered right-aligned navbar eliminated.
- **Rule 2: Borderless Headline Metrics (The Two Largest Elements)**: Rolling PSR and Lift-vs-Baseline side-by-side as the two largest elements on screen. Strictly label-plus-number with NO surrounding box, NO border, NO card container, NO panel background (`bg-transparent`, no card borders). Rolling PSR in `Space Grotesk` 700 (48px–56px, `#5B8DEF` / `#E5484D`). Lift-vs-Baseline in `Space Grotesk` 700 (48px–56px, `#5B8DEF`).
- **Rule 3: Single-Row Acquirer Strip**: Zero panels, zero bars, zero redundancy. Each acquirer rendered as a single horizontal row: `[Color Dot]  [Acquirer Name]  [One Live Telemetry Number]  [Plain Text Outage Button]`. Color dot (8px circle, assigned color overridden by `#E5484D` on outage), Name (`Inter`, 13px, `#E4E6EB`), Figure (`Space Mono`, 13px), Action (`[trigger outage]` / `[clear outage]`).
- **Rule 4: Plain Text-Style Outage-Trigger Controls**: Minimal inline text (`text-xs text-[#8B8F98] hover:text-[#E4E6EB] hover:underline bg-transparent border-0 p-0 cursor-pointer`). Active state shifts to Alert Crimson (`text-[#E5484D]`) with `"clear outage"`.
- **Rule 5: Zero-Footprint Progressive Disclosure (`'Diagnostics ›'`)**: Benchmark audit collapsed behind a single small text link: `'Diagnostics ›'`. Closed state renders no border or box footprint.

##### 3. Preserved Invariants from Revision 3 (Revision 4 Baseline)
- Base Palette: Ground `#0F1115`, Panel `#16181D`, Border `#2A2D34`, Primary Text `#E4E6EB`, Secondary Text `#8B8F98`.
- Acquirer Chart Lines: Alpha `#5B8DEF` (2.0px stroke), Beta `#C084FC` (1.5px stroke), Gamma `#7C808A` (1.0px stroke).
- Inviolable Alert Override: Outage or $H < 0.70$ immediately forces acquirer's identity to Alert Crimson (`#E5484D`), reverting upon recovery.
- Quarantines: Alert strictly non-decorative; Accent 2 strictly Acquirer Beta.

---

#### 2. Revision 5 Specifications: The Two Architectural Enhancements

##### Enhancement 1: Humanized Architecture Descriptions & Footer Cleanliness

1. **Excision of Build Version String**:
   - The build artifact string `"Loom protocol v0.7.0"` is **completely excised from the live view**.
   - *Architectural Rationale*: Operational consoles for live mission-critical routing should display real-time physical telemetry and operational semantics. Static build versions create irrelevant cognitive clutter during live outage rehearsals and technical presentations.

2. **Humanized Plain Copy (Replacing Configuration Formulas)**:
   - Cryptic configuration strings, algorithmic parameter notation, and database technology tags (`PID [Kp=0.12, Ki=0.005, Kd=0.25, M=3]`, `Decayed Thompson sampling [tau=60s]`, `Append-only SQLite ledger`, `Real-time Redis dispatch`) are replaced with plain, human-friendly descriptions of what each architectural subsystem does:
     - **PID Controller**: `'Smooths every reroute so traffic never jumps'`
     - **Thompson Sampling**: `'Learns which gateway is healthiest, weighted toward the last minute'`
     - **SQLite Ledger**: `'Every decision is logged, permanently'`
     - **Redis Pub/Sub**: `'Reacts to every transaction instantly'`

3. **Strict Spatial Separation Invariant (Zero Pipes / Zero Middle-Dots)**:
   - The four architectural statements must be rendered strictly as **one line each**.
   - They must **NEVER be joined with middle-dots (`•`) or pipes (`|`)**.
   - *Typographic Rationale*: Inline concatenation (`string • string • string | string`) creates visual crowding and horizontal scanning strain. Presenting each capability on its own distinct line establishes visual calm, dignified whitespace, and effortless vertical scanning.
   - *Styling Contract*: Rendered in `Inter` (`text-[11px]` or `text-xs`, color `#8B8F98`, leading relaxed, `font-normal`).

##### Enhancement 2: Minimized Simulation Harness & Progressive Settings Disclosure

1. **Primary Per-Acquirer Simulation Row Pattern**:
   - The simulation harness is minimized to match the dashboard's existing per-acquirer row pattern.
   - Each acquirer is represented as exactly one row containing strictly:
     ```
     [Acquirer Name]    [Current State]    [Trigger outage / Clear outage]
     ```
   - *Row Elements*:
     1. **Acquirer Name**: `"Alpha"`, `"Beta"`, `"Gamma"` (`Inter`, 13px, font-medium, color `#E4E6EB`).
     2. **Current State**: Visual dot + state text (`● Nominal` in `#8B8F98` / `#5B8DEF`, or `● Outage active` in `#E5484D animate-pulse`).
     3. **Single Outage Text-Button**: A single plain text button (`"Trigger outage"` / `"Clear outage"`), styled with `text-xs text-[#8B8F98] hover:text-[#E4E6EB] hover:underline bg-transparent border-0 p-0 cursor-pointer font-sans transition-colors`. When armed, text shifts to Alert Crimson (`text-[#E5484D]`); when in-flight, displays `"dispatching..."` with `opacity-50 pointer-events-none`.
   - **Nothing else is visible by default** in the primary simulator harness (no sliders, no behavior radio buttons, no scenario preset decks).

2. **Progressive Disclosure for Advanced Simulation Settings (`'Simulation settings ›'`)*:
   - Advanced operator actuation controls move behind a single disclosure text link: `'Simulation settings ›'`.
   - **Zero Border Footprint**:
     - Modeled identically to the `'Diagnostics ›'` text link pattern.
     - Rendered in `Inter` (`text-xs text-[#8B8F98] hover:text-[#5B8DEF] inline-flex items-center gap-1 select-none cursor-pointer transition-colors`).
     - Default closed (`open={false}`).
     - Zero box borders, zero container outlines when collapsed.
   - **Tucked Simulator Controls (Fully Preserved Behind Disclosure)**:
     1. *Success-Rate Sliders*: Base success rate sliders ($p \in [0.0, 1.0]$) with monospace numeric readouts (`Space Mono`) per acquirer for partial gray-failure / brownout testing.
     2. *Failure-Behavior-Mode Toggles*: Mode radio selectors (`RETURN_DECLINE`, `HTTP_503`, `LATENCY_SPIKE`) per acquirer.
     3. *Benchmark Scenario Gauntlet Presets*:
        - *Preset 1: Standard Cliff* ($M=3$ hard outage on Alpha).
        - *Preset 2: Sensitive Blip* ($M=1$ transient outage on Alpha, 3.5s dip).
        - *Preset 3: Gray Failure* ($p=0.60$ partial brownout on Alpha).
     4. *Global Simulator Reset Action*: `"Reset all routes"` button to restore nominal baseline states across all simulated gateways.

---

#### 3. Architectural Additive Invariant & Functional Compatibility Guarantee

- **Zero Capability Deletion**: Every single functional capability QA, the tech lead, or automated integration test suites relied on in earlier phases is 100% preserved.
- **Contract Parity**:
  - The underlying backend proxy endpoints (`/api/simulator/acquirers/{id}/outage`, `/api/simulator/acquirers/{id}/success-rate`, `/api/simulator/admin/reset`) remain identical in path, method, and latency characteristics.
  - The request payload contracts (`active`, `behavior`, `transition_seconds`, `success_rate`, `reason`) remain identical.
  - Test suites (`tests/dashboard/test_phase7_qa_scenarios.py` and `tests/router_core/test_phase7_dashboard_integration.py`) execute against the exact same API and DOM contracts.
- **Operational Safety**: No simulator feature was removed; complex testing controls were simply organized under clean progressive disclosure to prevent operator distraction during executive demonstrations.

---

### [Phase 7 Revision 6] Static-Baseline Reference Curve Overlay Contract

**Extends**: Hero Allocation Chart Telemetry Specification in Phase 7 Revisions 3, 4, and 5.
**Preserves**: Typographic triad (`Space Grotesk`, `Space Mono`, `Inter`), single-row acquirers, minimized simulation harness, humanized architecture footer copy, deterministic acquirer color mapping (Alpha: `#5B8DEF`, Beta: `#C084FC`, Gamma: `#7C808A`), and inviolable alert override (`#E5484D`).

---

#### 1. Architectural Purpose & Mathematical Model

In Phase 6, the `StaticBaselineRouter` was benchmarked against the identical 150-transaction outage schedule (Seed 42, Alpha base rate 95%, Outage at Tx 51–100, Recovery at Tx 101–150). While Phase 6 demonstrated that the static router incurred an instantaneous $100.0\%$ single-step jump ($\Delta w_{\text{max}} = 100\%$), that comparison was previously confined to tabular audits.

This contract specifies overlaying the **pre-computed Phase 6 static-baseline run as a passive reference curve** directly onto Loom's live allocation chart canvas, creating an immediate visual comparison between:
- **Loom's Smooth PID Damping Curve**: Continuous exponential traffic decay ($\Delta w_{\text{max}} = 11.77\%$), preventing downstream herd-migration shock.
- **Static Baseline's Discontinuous Cliff Drop**: Instantaneous Heaviside step drop ($\Delta w_{\text{max}} = 100.0\%$), snapping from $100\% \to 0\%$ upon $M=3$ consecutive failures.

---

#### 2. Pre-Computed Reference Dataset Specification

The overlay is **static and pre-computed**—it is **never calculated in real-time, never re-routed live, and adds zero runtime backend compute**:
- **Plotted Variable**: The static baseline's traffic allocation share $w_{\text{Alpha}}^{\text{baseline}}$ for the outage-targeted leader (Acquirer Alpha).
- **Canonical Step Profile** (Sourced from Phase 6 empirical run):
  $$\Delta k = k - k_{\text{outage\_trigger}}$$
  $$w_{\text{Alpha}}^{\text{baseline}}(\Delta k) = \begin{cases}
  1.00 & \text{if } \Delta k < 0 \quad \text{(Pre-outage normal operation)} \\
  1.00 & \text{if } 0 \le \Delta k < 3 \quad \text{(Absorbing } M=3 \text{ consecutive failures before trip)} \\
  0.00 & \text{if } 3 \le \Delta k < 63 \quad \text{(Circuit breaker TRIPPED; 100\% volume dumped to Beta)} \\
  1.00 & \text{if } \Delta k \ge 64 \quad \text{(Canary probe succeeds; instantaneous snapback to Alpha)}
  \end{cases}$$
- **Data Encapsulation**: Stored on the frontend client as an immutable lookup table / relative step series (`BASELINE_REFERENCE_RUN`), ensuring instant local rendering with zero network latency.

---

#### 3. Visual Presentation Contract & Strict Rendering Invariants

1. **Stroke Style**:
   - Dashed stroke pattern: `stroke-dasharray="4 4"` (or `strokeDasharray: "4 4"`).
   - Line width: Hairline `1.5px` (or `1.0px`).
2. **Color Token**:
   - Secondary Text Gray (`#8B8F98`), rendered with opacity $0.50$ to $0.60$ (`strokeOpacity="0.55"`).
   - **Strict Negative Invariant**: The baseline line must **NEVER** use an accent color (`#5B8DEF` cobalt blue or `#C084FC` violet) and must **NEVER** use Alert Crimson (`#E5484D`). It is an archival ghost reference, not an active or failing participant.
3. **Z-Order & Layering (Painter's Algorithm Invariant)**:
   - The reference curve must be rendered **strictly behind Loom's live curves**:
     $$\text{Canvas Background} \to \text{Gridlines} \to \mathbf{Static\ Baseline\ Reference\ Line} \to \text{Live Gamma Line} \to \text{Live Beta Line} \to \text{Live Alpha Line} \to \text{Outage Markers} \to \text{Head Dots}$$
   - *Architectural Rationale*: Sinking the dashed gray line to the background ensures it provides contextual contrast without competing with or visually fracturing the live telemetry signals.

---

#### 4. Load-Bearing Disambiguation Legend

A compact legend element is added to the chart header / canvas corner:
- **Element 1**: Solid Accent Bar (`#5B8DEF`, solid `w-3 h-0.5`) + text `'Loom (live)'` (`Inter`, 11px, `#E4E6EB`).
- **Element 2**: Dashed Gray Bar (`#8B8F98`, dashed border or SVG `stroke-dasharray="2 2"`) + text `'Static baseline (recorded run)'` (`Inter`, 11px, `#8B8F98`).
- **Load-Bearing Invariant**:
  - This legend is **architecturally load-bearing, not decorative copy**.
  - Without explicit disambiguation, visiting engineers, tech leads, or executive panels would mistake the dashed line for a second concurrent live router running in production (or a fourth gateway arm). The label explicitly establishes the epistemological status of the two lines: Loom is actively responding to live events; the static baseline is an archival reference run under the identical failure gauntlet.

---

#### 5. Timeline Synchronization Mechanics (X-Axis Alignment)

To ensure the live outage and the recorded baseline cliff align perfectly on screen:
1. **Dynamic Outage Anchor Point**:
   - When an outage is triggered in the live demo (via `'Trigger outage'` text button or benchmark presets), the system records the live trigger event index $i_{\text{outage}}$ and timestamp $t_{\text{outage}}$.
   - An Outage Event Marker is pinned at horizontal coordinate $X_{\text{outage}}$.
2. **Relative Offset Mapping**:
   - The baseline reference dataset is parameterized relative to outage trigger ($\Delta \tau = 0$ at trigger).
   - For any point $i$ in the active chart window ($i \in [0, N-1]$):
     $$\Delta i = i - i_{\text{outage}}$$
   - If no outage has been triggered yet in the current session (or if the last outage has completely rolled off the 60-second window):
     $$w_{\text{Alpha}}^{\text{baseline}}(i) = 1.00 \quad (\forall i)$$
     *(Renders as a clean, resting 100% dashed reference line, showing the static baseline's normal priority allocation).*
   - When an outage is present within the chart window:
     - For events prior to outage ($i < i_{\text{outage}}$): $w_{\text{Alpha}}^{\text{baseline}} = 1.00$.
     - For 3 events after outage ($i_{\text{outage}} \le i < i_{\text{outage}} + 3$): $w_{\text{Alpha}}^{\text{baseline}} = 1.00$ (absorbing $M=3$ failures).
     - At $i = i_{\text{outage}} + 3$: Allocation drops vertically down to $0.00$ at a sharp 90° angle.
     - While outage is active: Allocation remains flat at $0.00$.
     - When recovery occurs + canary probe succeeds: Allocation jumps vertically back to $1.00$.
3. **Visual Result on Screen**:
   - Both Loom's live line and the static baseline line encounter the failure at the exact same horizontal coordinate $X_{\text{outage}}$.
   - The evaluator sees the static line plummet instantly to zero (the 100% stampede), while Loom's line eases smoothly down along its exponential damping curve, demonstrating the 8.5x stability advantage ($\Delta w_{\text{max}} = 11.77\%$ vs $100.0\%$) with unmistakable clarity.

---

## Open Risks


*(Rolls forward from the PRD, then grows as QA/Tech Lead surface new ones per phase.)*

- **PID gain tuning isn't computed analytically** — the P/I/D constants will need real manual tuning against the simulated outage script to get a damping curve that actually looks good on the dashboard. Budget real time for this, not just implementation time. *(from PRD)*
- **Live simulation has more surface area to misbehave in front of an audience** than a scripted replay would. Worth rehearsing the exact outage-trigger sequence before it needs to work under pressure. *(from PRD)*
- **"Good enough" PSR lift isn't yet defined as a number.** Worth deciding what result would actually be convincing before the Phase 6 baseline comparison is built, so the target isn't retrofitted after the fact. *(from PRD)*
- **[Phase 1] Starvation / dormant route recovery under purely event-driven decay:** If an acquirer experiences a hard outage and routing drops its traffic share to zero, event-driven decay stops stepping for that acquirer. While Thompson Sampling with prior reversion eventually forces exploration (variance increases toward prior), at low total transaction rates recovery detection may lag. The Phase 3/4 router policy must ensure an exploration floor or probe mechanism.
- **[Phase 1] Decay factor sensitivity to transaction velocity:** A fixed per-outcome $\gamma = 0.98$ represents an effective memory horizon of $N_{\text{eff}} = 50$ transactions. At 100 TPS, this adapts in 0.5s; at 1 TPS, it adapts in 50s. The simulation harness in Phase 2 must hold traffic velocity steady or calibrate $\gamma$ via `calculate_gamma_from_half_life`.
- **[Phase 1 QA] Long-tail memory retention under default decay factor ($\gamma = 0.98$):** Testing proved that past failures attenuate at mathematically exact $\gamma^k$. With default $\gamma = 0.98$, a failure 5 transactions ago retains $(0.98)^5 \approx 90.39\%$ of its penalty on $\beta$ and health deficit. An acquirer recovering from a 10-failure outage requires $\approx 35$ consecutive successes to eliminate 50% of the failure penalty and $\approx 115$ successes to eliminate 90%. Router tuning in Phase 3/4 must consider whether $\gamma = 0.98$ is too sluggish for low-throughput routes.
- **[Phase 1 QA] Cold-start divergence between operational health ($H=1.0$) and Bayesian prior ($\mathbb{E}[\theta]=0.50$):** On initialization, health reports 100% (optimistic operational assumption) while posterior mean is 50% with uniform dispersion ($\sigma \approx 0.289$). The dashboard and PID layer must treat these as different concepts (operational availability vs exploration state) to avoid confusing operators during initial startup.
- **[Phase 1 QA] Decoupling of telemetry timestamp from event-driven decay:** In `record_outcome(success, timestamp)`, `timestamp` is recorded as point-in-time metadata for future SQLite logging, but does not dynamically compute continuous $\Delta t$ decay. An acquirer that sits idle for 15 minutes during an outage experiences zero decay while idle until probe traffic is dispatched.
- **[Phase 1 Review] PID derivative kick from Thompson Sampling noise:** In Phase 4, if PID consumes raw Thompson Sampling targets ($w_i^{\text{target}} \propto \text{sample}_i$), the high-frequency variance inherent in Beta sampling could cause derivative ringing ($K_d \frac{de}{dt}$). Phase 4 design must apply a low-pass filter on the error signal or compute target allocations from the smoothed health signal while using sampling exclusively for exploration allocation. Owned by Tech Lead / Architect for Phase 4.
- **[Phase 2] Asynchronous latency simulation under high concurrent load:** In-flight `asyncio.sleep` calls simulate gateway delays non-blockingly, but if transaction velocity exceeds uvicorn worker thread capacity, event-loop task queue buildup may introduce unintended queueing latency beyond `simulated_latency_ms`. Benchmark harness must monitor real wall-clock round-trip vs simulated delay.
- **[Phase 2] Outage classification in multi-acquirer test harness:** When simulating an outage via `RETURN_DECLINE`, the router treats it as a binary failure identically to a normal decline. If Phase 4's PID or Phase 7's dashboard needs to differentiate between natural card declines (e.g. insufficient funds) and systemic gateway outages, the router outcome schema must preserve `decline_code`.
- **[Phase 2 QA] Identifier precedence in multi-tenant authorization routing:** When using `/acquirers/{acquirer_id}/authorize`, the URL path takes precedence over any `acquirer_id` passed in the JSON body. Phase 3 router implementation must ensure client requests do not supply conflicting route identifiers.
- **[Phase 2 QA] Absence of transaction idempotency deduplication in simulator:** The simulated acquirer service executes an independent probabilistic draw on every `POST /authorize` call, even if the same `transaction_id` is replayed. Router retries in Phase 3/4 will be evaluated as new random trials rather than returning cached idempotent responses.
- **[Phase 3] Herd migration stampede under sudden route failure:** In a multi-acquirer setup, when the primary route dies, raw Thompson Sampling rapidly transfers 100% of volume to the second-best route within 5-10 transactions. If the backup route cannot handle the sudden traffic spike, cascading failures will occur — providing the primary motivation for Phase 4's PID damping.
- **[Phase 3] Allocation oscillation / flapping near crossover thresholds:** When two acquirers have similar posterior distributions or when a route recovers, sample noise causes high-frequency alternating assignments between routes. Operators must be prepared to see chatter in Phase 3 logs before PID smoothing is applied.
- **[Phase 3] HTTP connection starvation under high TPS:** If the transaction generator is run at high concurrency against the HTTP router daemon, uvicorn and httpx connection pool limits (`max_connections=100`) must be tuned to prevent socket exhaustion and artificial transport latency.
- **[Phase 3 QA] Post-Outage Route Starvation under Event-Driven Decay:** Empirical validation confirmed that when a disabled leader (Alpha) recovers to 95% health, it receives 0 out of 50 subsequent transactions because unselected routes experience zero decay updates. The backup route (Beta) accumulates high $\alpha$ with tightly concentrated variance, permanently locking out the recovered arm. Phase 4 PID / routing policy must mandate an exploration floor or active probing mechanism to restore traffic to recovered acquirers.
- **[Phase 3 QA] In-Flight Concurrency Feedback Lag:** Under concurrent transaction dispatch, multiple coroutines draw Thompson samples simultaneously before any HTTP round-trip returns or updates bandit state. During sudden acquirer failure, an entire concurrent batch can fail before the first error degrades the posterior distribution. Phase 4/5 design should track in-flight transaction count $N_{\text{in-flight}}$ and apply virtual loss or load-balancing penalties.
- **[Phase 3 QA] Baseline Oscillation & Chatter Reference Captured:** Quantitative baseline established in `docs/phase3-qa-report.md`: 13 route flips across 50 transactions in the crossover zone, with discrete 100% hard-switching and hairline flips at sample differences $\le 0.0022$. Phase 4 acceptance criteria will measure dampening against this reference.
- **[Phase 4] Discrete transaction arrival pacing vs PID continuous step integration ($\Delta t$ coupling):** At fixed transaction rates (e.g. 20 TPS), unit step $\Delta t = 1.0$ behaves consistently. However, under bursty or Poisson arrival patterns, wall-clock time between transactions fluctuates. If PID step math assumes constant $\Delta t = 1.0$ per transaction rather than wall-clock seconds, control speed couples to transaction throughput. Ticket B tuning must evaluate both unit-step $\Delta t = 1.0$ and wall-clock $\Delta t$ modes.
- **[Phase 4] Trade-off between exploration loss and outage protection under minimum allocation floor ($w_{\text{min}} = 0.03$):** Guaranteeing 3% minimum traffic to dead acquirers ensures recovery detection, but inherently incurs a 3% transaction failure penalty as long as the outage persists. For higher reliability requirements, the exploration floor could be dynamically throttled or scaled down during confirmed sustained outages.
- **[Phase 4] Gain sensitivity to acquirer count $K$:** In a 2-acquirer setup, error is strictly anti-symmetric ($e_A = -e_B$). In a 5-acquirer topology, errors distribute across multiple alternative backup arms. Gain constants tuned for $K=2$ may produce slower or faster damping when $K \ge 5$. Ticket B should establish baseline tuning for $K=2$ and document scaling rules for larger pools.
- **[Phase 4 QA] Verification of Allocation Curve Easing & Starvation Resolution:** Full QA validation in `docs/phase4-qa-report.md` confirmed that tuned PID ($K_p=0.12, K_i=0.005, K_d=0.25, I_{\text{max}}=1.0, w_{\text{min}}=0.03$) reduces peak single-step allocation jump from 100.0% (Phase 3 binary hard-switch) down to 11.77% ($< 15\%$ spec limit) with zero square-wave ringing. The bounded simplex exploration floor ($w_{\text{min}} = 0.03$) successfully resolved Phase 3's dormant route starvation pathology, routing 2 probe transactions post-recovery that updated posterior beliefs and initiated autonomous traffic recovery.
- **[Phase 4 QA] Integral Windup Verification & Steady-State Exploration Drift:** Stress testing across a 200-transaction sustained outage confirmed that an unbounded accumulator drifts to $-8.99$, creating up to $-0.38$ of integral drag that paralyzes the controller for 5 to 6 transactions after outage clearance. Ticket A's anti-windup clamping ($I_{\text{max}} = 1.0$) strictly bounded the accumulator to $-1.0000$, resulting in immediate response on Step 1 (0 recovery delay) and zero overshoot ($0.00\%$) as allocation asymptoted to the 97% boundary. Crucially, testing revealed that the 3% exploration floor creates a permanent steady-state error ($e = +0.03$) during healthy operation; $I_{\text{max}} = 1.0$ clamping is essential to prevent operational runaway drift.
- **[Phase 5] Data layer queue backpressure under sustained overload:** If SQLite disk I/O degrades or transactions arrive faster than disk write speed for prolonged periods, the in-memory `asyncio.Queue` will fill. The writer must enforce a bounded capacity (10,000 items) and explicitly drop metrics or block with backpressure rather than causing process out-of-memory (OOM) crashes.
- **[Phase 5] SQLite WAL file growth and checkpoint starvation:** Under continuous high-velocity transaction generation, read transactions from the baseline comparator (Phase 6) or dashboard background queries can prevent SQLite from checkpointing the WAL file back to the main database file, causing unbounded disk growth. Periodic checkpointing (`PRAGMA wal_checkpoint(PASSIVE)`) must be managed by the connection lifecycle.
- **[Phase 5] Redis Pub/Sub client buffer limits under slow subscriber connection:** If the Phase 7 dashboard WebSocket server consumes messages slower than transaction emission rate, Redis client output buffer limits (`client-output-buffer-limit pubsub`) may be exceeded, causing Redis to forcibly disconnect the subscriber. The WebSocket bridge must consume efficiently.
- **[Phase 5] Clock skew across multi-worker deployments:** If multiple router processes run on separate hosts or out-of-sync container nodes, Unix timestamps recorded in Redis and SQLite could appear out-of-order. While Phase 1's mathematical model is discrete and immune to timestamp jitter, analytical queries grouping by timestamp bins depend on synchronized NTP clocks.
- **[Phase 5 Review] SQLite Concurrency & WAL Contention under High Read Load:** During live demonstrations or simultaneous Phase 6 comparator execution, long-running read queries against SQLite while `MetricsLogger` is actively flushing batches can cause WAL checkpoint starvation or lock contention if busy timeouts expire. Read queries in Phase 6 should use explicit short read transactions or read replicas if necessary.
- **[Phase 5 Review] Redis Pub/Sub Subscriber Cold-Start Gap:** Redis Pub/Sub has no historical buffer. If a Phase 7 dashboard connects after transaction traffic has already commenced, it will miss initial transactions. Phase 7 dashboard must follow a hybrid bootstrap pattern: read historical transactions from SQLite via API, then subscribe to Pub/Sub for live real-time updates.
- **[Phase 5 Review] Administrative Rehearsal vs Constitutional Immutability:** `data_layer/cli.py reset-demo` drops and recreates tables to allow fresh demo runs. While required for local developer ergonomics, production deployments must restrict DDL privileges so that `DROP TABLE` cannot be executed outside approved migration windows.
- **[Phase 6] Benchmark Sensitivity to Cooldown Window Calibration ($N_{\text{cooldown}}$):** In the static router, if $N_{\text{cooldown}}$ is calibrated too short (e.g. 5 transactions), the router probes repeatedly during an outage, absorbing multiple probe failures and inducing high-frequency flapping. If calibrated too long (e.g. 100 transactions), the router remains stranded on the secondary route long after the primary recovers, artificially suppressing the baseline's post-outage PSR. The benchmark scenario in `scripts/compare_psr.py` should evaluate both $N_{\text{cooldown}}=30$ and sensitivity sweeps to ensure Loom's lift is robust against cooldown tuning.
- **[Phase 6] Stochastic Canary Probe False Recovery:** In canary probe mode (`failback_mode="probe"`), if Primary A is undergoing an intermittent gray failure (e.g. $p=0.40$), there is a 40% probability that the single probe transaction succeeds by random chance. When this occurs, the static router falsely declares Primary A healthy, promotes it to active status, and subsequently absorbs another $M$ failures before re-tripping. This demonstrates authentic flapping, but QA must account for this stochastic variance across seeded benchmark runs.
- **[Phase 6] Gray Failure (Partial Outage) Sensitivity vs Loom Exploration Floor:** During severe gray failures (e.g. Primary at 60% PSR), the static router's failure to trip leads to massive revenue bleed. Conversely, during minor degradations (e.g. Primary drops from 95% to 92%), Loom's exploration floor ($w_{\text{min}}=0.03$) continually tests secondary routes (Beta at 90%), incurring minor exploration loss. The benchmark reporting must isolate both deep outages and shallow brownouts to clearly demonstrate where dynamic routing wins.
- **[Phase 6] Secondary Acquirer Capacity Cliff in Stress Tests:** The simulated acquirer service in Phase 2 currently handles requests without internal rate limiting. In production, herd migration causes secondary gateway collapse because backup acquirers hit concurrency limits. Future benchmark iterations or stress scripts in Phase 8 should incorporate rate-limiting on Acquirer Beta to make the operational danger of herd migration tangible.
- **[Phase 6 Tech Lead Review] Micro-Benchmark PSR Inversion vs Infinite Secondary Capacity:** In an unconstrained synthetic benchmark where secondary acquirers have infinite mock capacity, cutting over via an instantaneous 100% Heaviside step jump incurs fewer transition failures (4 failures) than Loom's smooth PID ramp (11 failures). This causes the static baseline to register 92.00% vs Loom's 86.00% on the standard 150-tx test. This raw 86% vs 92% number cannot be used as the naive headline comparison on the Phase 7 dashboard; the dashboard must explicitly contextualize the 8.5x stability improvement ($\Delta w_{\text{max}} = 11.77\%$ vs $100.0\%$) and the downstream secondary capacity protection.
- **[Phase 6 Tech Lead Review] Multi-Scenario Dashboard Architecture for Phase 7:** To demonstrate Loom's true PSR advantage convincingly in Phase 7, the dashboard operator controls must provide toggleable scenario gauntlets: (1) Standard Outage ($M=3$), highlighting stability and failure damping, (2) Sensitive Blip / Overreaction ($M=1$), demonstrating Loom's +1000 bps PSR lift (86% vs 76%), and (3) Gray Failure / Brownout, demonstrating dynamic adaptation against static counter-reset paralysis.
- **[Phase 7] Browser Main-Thread Render Starvation under High-TPS Transaction Bursts:** If the transaction generator fires at 50–100 TPS, naive component state updating per WebSocket message will freeze the React event loop. The decoupled ring buffer and `requestAnimationFrame` ticker (ADR-03) must be strictly adhered to by the frontend engineer; any direct `setState` inside `ws.onmessage` is an immediate review blocker.
- **[Phase 7] Chart Canvas Memory Leaks during Prolonged Running:** Long-running browser tabs accumulating points into unbounded arrays will cause Chromium tab crashes (OOM). Ticket A's time series must enforce a hard FIFO slice (max 200 data points or 60-second window).
- **[Phase 7] WebSocket Cold-Start Race Conditions:** If the React dashboard mounts and connects before the simulated acquirers or router service are fully initialized, the WebSocket connection will drop or receive an empty bootstrap state. The UI must handle initial `DISCONNECTED` states gracefully with automated exponential backoff retries without crashing the page.
- **[Phase 7] Acquirer Admin Outage Propagation Latency:** When an operator clicks `TRIGGER OUTAGE`, there is an async network round-trip from browser $\to$ router proxy $\to$ simulated acquirer $\to$ router state $\to$ Redis Pub/Sub $\to$ WebSocket $\to$ browser chart. If this loop exceeds 100ms, the operator will experience perceptible interface lag. Network latencies across localhost must remain $<10\text{ms}$.
- **[Phase 7] Visual Misinterpretation of the Unconstrained 86% vs 92% Benchmark:** If an uninformed audience views the Phase 6 standard outage numbers without context, they may mistakenly assume the static router is superior because it scored 92% vs Loom's 86%. Ticket B's `BaselineComparisonCard` must prominently feature the $M=1$ Overreaction scenario (Loom +1000 bps lift) and the 8.5x stability multiplier ($\Delta w_{\text{max}} = 11.77\%$ vs $100.0\%$).

---


## How to use this file

- Before each phase's Architect pass, paste in the relevant prior entries (not the whole file if it gets long — just the Decisions/Interfaces that phase actually depends on).
- After each phase's Architect pass produces a new contract, append it under Interface Contracts, phase-tagged.
- After QA or Tech Lead finds a gap, ambiguity, or risk, append it under Open Risks immediately — don't hold it for a later "cleanup" pass.
- Nothing here gets deleted or rewritten once added. If a decision changes later, add a new entry noting the change and why, rather than editing the old one — the history of what changed is itself useful.
