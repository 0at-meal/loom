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

## Interface Contracts

### [Phase 1] Per-Acquirer State & Health Signal Contract

**Module Target**: `router_core/state.py` (Domain entities & contracts) and `router_core/bandit.py` (Sampling & registry)

#### 1. Data Shapes

```python
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class AcquirerStateConfig:
    """Immutable configuration for an acquirer's bandit and health model."""
    alpha_prior: float = 1.0       # Prior alpha > 0.0 (default uniform Bayes prior)
    beta_prior: float = 1.0        # Prior beta > 0.0 (default uniform Bayes prior)
    decay_factor: float = 0.98      # Per-outcome retention factor gamma in (0.0, 1.0)
    initial_health: float = 1.0    # Initial health score in [0.0, 1.0] (optimistic start)

@dataclass(frozen=True, slots=True)
class AcquirerStateSnapshot:
    """Immutable point-in-time snapshot of acquirer state."""
    acquirer_id: str               # Unique identifier for the acquirer
    alpha: float                   # Current Beta distribution shape parameter alpha >= alpha_prior
    beta: float                    # Current Beta distribution shape parameter beta >= beta_prior
    health_score: float            # Current decayed EWMA health score in [0.0, 1.0]
    success_count: int             # Cumulative unweighted lifetime successes (>= 0)
    failure_count: int             # Cumulative unweighted lifetime failures (>= 0)
    total_count: int               # Cumulative unweighted lifetime transactions (>= 0)
    last_updated_at: float         # Unix epoch timestamp of last recorded outcome

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

---

## How to use this file

- Before each phase's Architect pass, paste in the relevant prior entries (not the whole file if it gets long — just the Decisions/Interfaces that phase actually depends on).
- After each phase's Architect pass produces a new contract, append it under Interface Contracts, phase-tagged.
- After QA or Tech Lead finds a gap, ambiguity, or risk, append it under Open Risks immediately — don't hold it for a later "cleanup" pass.
- Nothing here gets deleted or rewritten once added. If a decision changes later, add a new entry noting the change and why, rather than editing the old one — the history of what changed is itself useful.
