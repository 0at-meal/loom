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

---


## How to use this file

- Before each phase's Architect pass, paste in the relevant prior entries (not the whole file if it gets long — just the Decisions/Interfaces that phase actually depends on).
- After each phase's Architect pass produces a new contract, append it under Interface Contracts, phase-tagged.
- After QA or Tech Lead finds a gap, ambiguity, or risk, append it under Open Risks immediately — don't hold it for a later "cleanup" pass.
- Nothing here gets deleted or rewritten once added. If a decision changes later, add a new entry noting the change and why, rather than editing the old one — the history of what changed is itself useful.
