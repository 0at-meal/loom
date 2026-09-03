# Phase 1 Specification — Per-Acquirer Health Signal & Bandit State

**Role**: Systems Architect
**Audience**: Backend Engineer, QA Engineer, Tech Lead
**Scope**: Phase 1 (Standalone health signal + bandit state engine)
**Status**: Settled Architecture Contract

---

## 1. Executive Summary & Component Boundary

Phase 1 establishes the mathematical foundation of Loom's routing engine: the per-acquirer state representation. This component maintains the dual belief system for each acquirer:
1. **Bayesian Belief ($\alpha, \beta$)**: A probability distribution representing statistical uncertainty and success likelihood, sampled via Thompson Sampling.
2. **Decayed Health Score ($H$)**: A deterministic, exponentially weighted moving average (EWMA) representing current operational health for observability, alerting, and control loop inputs.

### Explicit Scope Boundaries
- **In Scope for Phase 1**:
  - In-memory data structures for individual acquirer state and multi-acquirer registry.
  - Mathematical state transitions for discrete transaction outcomes (Success / Failure).
  - Discounted Thompson Sampling state decay and EWMA health updates.
  - Deterministic evaluation and sampling interfaces.
  - Unit tests verifying numerical accuracy against hand-scripted outcome vectors.
- **Explicitly Out of Scope for Phase 1**:
  - PID control smoothing (Phase 4).
  - Redis persistence or pub/sub serialization (Phase 5).
  - SQLite transaction logging (Phase 5).
  - HTTP / FastAPI endpoints (Phase 2 & Phase 3).
  - Background ticker threads or asynchronous loops.

---

## 2. Core Data Shapes

The state model is divided into three distinct schemas:
1. `AcquirerStateConfig`: Immutable configuration parameters.
2. `AcquirerStateSnapshot`: Immutable, serializable point-in-time state read model.
3. `AcquirerState`: Mutable in-memory domain entity encapsulating state transitions.

### 2.1 Configuration Schema (`AcquirerStateConfig`)

| Field | Type | Default | Domain / Constraints | Description |
| :--- | :--- | :--- | :--- | :--- |
| `alpha_prior` | `float` | `1.0` | $\alpha_0 > 0.0$ | Prior shape parameter $\alpha$ for Beta distribution (default: standard Bayes-Laplace uniform prior). |
| `beta_prior` | `float` | `1.0` | $\beta_0 > 0.0$ | Prior shape parameter $\beta$ for Beta distribution (default: standard Bayes-Laplace uniform prior). |
| `decay_factor` | `float` | `0.98` | $\gamma \in (0.0, 1.0)$ | Per-outcome retention factor $\gamma$ applied to accumulated observations. |
| `initial_health` | `float` | `1.0` | $H_0 \in [0.0, 1.0]$ | Baseline health score at initialization (default: optimistic 100%). |

### 2.2 Read Snapshot Schema (`AcquirerStateSnapshot`)

| Field | Type | Domain / Constraints | Description |
| :--- | :--- | :--- | :--- |
| `acquirer_id` | `str` | Non-empty string | Unique acquirer route identifier (e.g., `"acquirer_alpha"`, `"stripe"`). |
| `alpha` | `float` | $\alpha \ge \alpha_0 > 0.0$ | Current shape parameter $\alpha$ (pseudo-successes + prior). |
| `beta` | `float` | $\beta \ge \beta_0 > 0.0$ | Current shape parameter $\beta$ (pseudo-failures + prior). |
| `health_score` | `float` | $H \in [0.0, 1.0]$ | Current decayed EWMA health score. |
| `success_count` | `int` | $\ge 0$ | Cumulative unweighted successes observed over lifetime. |
| `failure_count` | `int` | $\ge 0$ | Cumulative unweighted failures observed over lifetime. |
| `total_count` | `int` | $\ge 0$ | Cumulative unweighted outcomes: `success_count + failure_count`. |
| `last_updated_at` | `float` | $\ge 0.0$ | Epoch timestamp (seconds) of the most recently processed outcome. |

#### Derived Read-Only Properties
- **Expected Success Rate (Posterior Mean)**:
  $$\mathbb{E}[\theta] = \frac{\alpha}{\alpha + \beta}$$
- **Posterior Variance**:
  $$\text{Var}(\theta) = \frac{\alpha \beta}{(\alpha + \beta)^2 (\alpha + \beta + 1)}$$
- **Effective Sample Size ($N_{\text{eff}}$)**:
  $$N_{\text{eff}} = (\alpha - \alpha_0) + (\beta - \beta_0)$$

---

## 3. Mathematical Foundations of Decay

### 3.1 What the Decay Factor ($\gamma$) Actually Does
The decay factor $\gamma \in (0.0, 1.0)$ controls the rate of exponential forgetting for past outcomes.

#### A. Exponential Memory Weighting
In an outcome stream $x_t, x_{t-1}, x_{t-2}, \dots, x_1$, an observation $k$ steps in the past carries weight $\gamma^k$. Because $0 < \gamma < 1$:
$$\lim_{k \to \infty} \gamma^k = 0$$
Recent transactions dominate the state; stale performance degrades exponentially.

#### B. Effective Sample Horizon ($N_{\text{eff}}$)
The sum of the infinite geometric weighting series defines the equivalent sample window size:
$$N_{\text{eff}} = \sum_{k=0}^{\infty} \gamma^k = \frac{1}{1 - \gamma}$$
- For $\gamma = 0.90 \implies N_{\text{eff}} = 10$ transactions.
- For $\gamma = 0.95 \implies N_{\text{eff}} = 20$ transactions.
- For $\gamma = 0.98 \implies N_{\text{eff}} = 50$ transactions (Default).
- For $\gamma = 0.99 \implies N_{\text{eff}} = 100$ transactions.

#### C. Half-Life in Transaction Count ($N_{1/2}$)
The half-life $N_{1/2}$ is the number of transactions after which past evidence loses 50% of its influence:
$$\gamma^{N_{1/2}} = 0.5 \implies N_{1/2} = \frac{\ln(0.5)}{\ln(\gamma)} \approx \frac{0.69315}{-\ln(\gamma)}$$
- For $\gamma = 0.98$: $N_{1/2} \approx 34.3$ transactions.

#### D. Bridging Wall-Clock Half-Life to Discrete Decay Factor
Given a required wall-clock half-life $t_{1/2}$ in seconds (e.g. `DECAY_HALF_LIFE_SEC=60.0` from `.env.example`) and an estimated transaction throughput $R$ (transactions per second across that acquirer):
$$\gamma = \exp\left(-\frac{\ln 2}{R \cdot t_{1/2}}\right) = 0.5^{\frac{1}{R \cdot t_{1/2}}}$$

---

## 4. State Transition & Update Rules

Every transaction produces a binary outcome $x_t \in \{0, 1\}$, where $x_t = 1$ denotes authorization success, and $x_t = 0$ denotes authorization failure.

### 4.1 Thompson Sampling Beta Parameter Update

#### The Mathematical Problem with Naive Decay
Naive decay applies linear scaling: $\alpha_t = \gamma \alpha_{t-1} + x_t$. Under sustained failures ($x_t = 0$), $\alpha$ decays geometrically: $\alpha \to 0$. When $\alpha < 1.0$, the Beta probability density function $f(\theta) \propto \theta^{\alpha - 1}(1 - \theta)^{\beta - 1}$ develops a vertical asymptote at $\theta = 0$, producing severe sampling singularities and numerical underflow.

#### The Architectural Solution: Mean-Reverting Offset Decay
We decay only the *accumulated empirical evidence* $(\alpha - \alpha_0)$ and $(\beta - \beta_0)$, preserving the prior floor at all times:
$$\alpha_t = \alpha_0 + \gamma \cdot (\alpha_{t-1} - \alpha_0) + x_t$$
$$\beta_t = \beta_0 + \gamma \cdot (\beta_{t-1} - \beta_0) + (1 - x_t)$$

#### Concrete Update Rules
1. **On Success ($x_t = 1$):**
   $$\alpha_t = \alpha_0 + \gamma \cdot (\alpha_{t-1} - \alpha_0) + 1.0$$
   $$\beta_t = \beta_0 + \gamma \cdot (\beta_{t-1} - \beta_0)$$
2. **On Failure ($x_t = 0$):**
   $$\alpha_t = \alpha_0 + \gamma \cdot (\alpha_{t-1} - \alpha_0)$$
   $$\beta_t = \beta_0 + \gamma \cdot (\beta_{t-1} - \beta_0) + 1.0$$

#### Invariant Guarantee
For any initial state with $\alpha \ge \alpha_0$ and $\beta \ge \beta_0$, and $\gamma \in (0, 1)$:
$$\alpha_t \ge \alpha_0 \quad \text{and} \quad \beta_t \ge \beta_0 \quad \forall t \ge 0$$
When $\alpha_0 = 1.0, \beta_0 = 1.0$, the parameters are strictly bounded below by $1.0$. The distribution never degenerates into a U-shaped boundary spike.

### 4.2 Decayed Health Score Update (EWMA)

The operational health score $H_t \in [0.0, 1.0]$ tracks recent success rate through standard first-order exponential smoothing:
$$H_t = \gamma \cdot H_{t-1} + (1 - \gamma) \cdot x_t$$

#### Concrete Update Rules
1. **On Success ($x_t = 1$):**
   $$H_t = \gamma \cdot H_{t-1} + (1 - \gamma) = H_{t-1} + (1 - \gamma)(1.0 - H_{t-1})$$
   *(Health closes $(1 - \gamma)$ of the remaining distance toward 1.0)*
2. **On Failure ($x_t = 0$):**
   $$H_t = \gamma \cdot H_{t-1}$$
   *(Health is attenuated directly by factor $\gamma$)*

### 4.3 Lifetime Counters
Counters track cumulative unweighted volume for post-hoc validation, auditing, and SQLite persistence:
- $\text{success\_count}_t = \text{success\_count}_{t-1} + x_t$
- $\text{failure\_count}_t = \text{failure\_count}_{t-1} + (1 - x_t)$
- $\text{total\_count}_t = \text{total\_count}_{t-1} + 1$

---

## 5. Concrete Numerical Trace (Verification Test Vector)

To ensure the Backend Engineer and QA Engineer can verify implementation down to floating-point precision, here is the deterministic trace for $\alpha_0 = 1.0$, $\beta_0 = 1.0$, $\gamma = 0.90$, $H_0 = 1.0$:

| Step $t$ | Outcome $x_t$ | Description | $\alpha_t$ | $\beta_t$ | $H_t$ | $\mathbb{E}[\theta] = \frac{\alpha}{\alpha+\beta}$ |
| :---: | :---: | :--- | :---: | :---: | :---: | :---: |
| 0 | — | Initial Prior State | $1.000000$ | $1.000000$ | $1.000000$ | $0.500000$ |
| 1 | Success ($1$) | First transaction succeeds | $2.000000$ | $1.000000$ | $1.000000$ | $0.666667$ |
| 2 | Success ($1$) | Second transaction succeeds | $2.900000$ | $1.000000$ | $1.000000$ | $0.743590$ |
| 3 | Success ($1$) | Third transaction succeeds | $3.710000$ | $1.000000$ | $1.000000$ | $0.787686$ |
| 4 | Failure ($0$) | Sudden failure | $3.439000$ | $2.000000$ | $0.900000$ | $0.632285$ |
| 5 | Failure ($0$) | Outage continues | $3.195100$ | $2.900000$ | $0.810000$ | $0.524208$ |
| 6 | Failure ($0$) | Outage continues | $2.975590$ | $3.710000$ | $0.729000$ | $0.445089$ |
| 7 | Success ($1$) | Probe recovers | $3.778031$ | $3.439000$ | $0.756100$ | $0.523485$ |

*Notice:*
- On Step 4 (first failure), $H$ drops immediately from $1.00$ to $0.90$, and $\beta$ steps from $1.0$ to $2.0$.
- By Step 6 (three consecutive failures), $\beta = 3.71 > \alpha = 2.97$, flipping the posterior expectation below $0.50$.
- On Step 7, a recovery transaction immediately pulls $\alpha$ up from $2.97$ to $3.78$ and increases health to $0.756$.

---

## 6. Public Interface Contracts (Python)

All contracts strictly adhere to `docs/CONSTITUTION.md`:
- Type hints on all signatures.
- One-line docstrings stating *what* each public method does, not *how*.
- State mutations are explicitly declared in method names (`record_outcome`).
- No bare `except:` statements.

### 6.1 `router_core/state.py`

```python
"""Domain models and state transition logic for per-acquirer health and bandit beliefs."""

from __future__ import annotations

import time
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True, slots=True)
class AcquirerStateConfig:
    """Immutable configuration for an acquirer's bandit and health model."""

    alpha_prior: float = 1.0
    beta_prior: float = 1.0
    decay_factor: float = 0.98
    initial_health: float = 1.0

    def __post_init__(self) -> None:
        """Validate invariant constraints on configuration parameters."""
        if self.alpha_prior <= 0.0:
            raise ValueError(f"alpha_prior must be > 0.0, got {self.alpha_prior}")
        if self.beta_prior <= 0.0:
            raise ValueError(f"beta_prior must be > 0.0, got {self.beta_prior}")
        if not (0.0 < self.decay_factor < 1.0):
            raise ValueError(f"decay_factor must be in (0.0, 1.0), got {self.decay_factor}")
        if not (0.0 <= self.initial_health <= 1.0):
            raise ValueError(f"initial_health must be in [0.0, 1.0], got {self.initial_health}")


@dataclass(frozen=True, slots=True)
class AcquirerStateSnapshot:
    """Immutable point-in-time snapshot of acquirer state."""

    acquirer_id: str
    alpha: float
    beta: float
    health_score: float
    success_count: int
    failure_count: int
    total_count: int
    last_updated_at: float

    @property
    def expected_success_rate(self) -> float:
        """Posterior mean of the Beta distribution: alpha / (alpha + beta)."""
        return self.alpha / (self.alpha + self.beta)

    @property
    def variance(self) -> float:
        """Posterior variance of the Beta distribution."""
        total = self.alpha + self.beta
        return (self.alpha * self.beta) / (total * total * (total + 1.0))

    @property
    def effective_sample_size(self) -> float:
        """Sum of decayed observation pseudo-counts excluding priors."""
        return max(0.0, (self.alpha - 1.0) + (self.beta - 1.0))


class AcquirerState:
    """Encapsulates the decaying Beta belief and EWMA health score for a single acquirer."""

    def __init__(
        self,
        acquirer_id: str,
        config: AcquirerStateConfig | None = None,
        initial_timestamp: float | None = None,
    ) -> None:
        """Initialize acquirer state with prior parameters and optimistic health."""
        if not acquirer_id or not acquirer_id.strip():
            raise ValueError("acquirer_id cannot be empty")

        self._acquirer_id: str = acquirer_id
        self._config: AcquirerStateConfig = config or AcquirerStateConfig()
        self._alpha: float = self._config.alpha_prior
        self._beta: float = self._config.beta_prior
        self._health_score: float = self._config.initial_health
        self._success_count: int = 0
        self._failure_count: int = 0
        self._last_updated_at: float = (
            initial_timestamp if initial_timestamp is not None else time.time()
        )

    @property
    def acquirer_id(self) -> str:
        """Return the unique identifier for this acquirer."""
        return self._acquirer_id

    @property
    def config(self) -> AcquirerStateConfig:
        """Return the configuration parameters for this acquirer."""
        return self._config

    def record_outcome(
        self,
        success: bool,
        timestamp: float | None = None,
    ) -> AcquirerStateSnapshot:
        """Update Beta parameters and EWMA health score with a transaction outcome."""
        gamma = self._config.decay_factor
        a0 = self._config.alpha_prior
        b0 = self._config.beta_prior
        x = 1.0 if success else 0.0

        # Mean-reverting decayed Beta parameters
        self._alpha = a0 + gamma * (self._alpha - a0) + x
        self._beta = b0 + gamma * (self._beta - b0) + (1.0 - x)

        # EWMA health score
        self._health_score = gamma * self._health_score + (1.0 - gamma) * x

        # Cumulative counters
        if success:
            self._success_count += 1
        else:
            self._failure_count += 1

        self._last_updated_at = timestamp if timestamp is not None else time.time()
        return self.get_state()

    def sample(self, rng: np.random.Generator | None = None) -> float:
        """Draw a Thompson sample from the current Beta distribution belief."""
        generator = rng if rng is not None else np.random.default_rng()
        return float(generator.beta(self._alpha, self._beta))

    def get_state(self) -> AcquirerStateSnapshot:
        """Return an immutable snapshot of current acquirer state."""
        return AcquirerStateSnapshot(
            acquirer_id=self._acquirer_id,
            alpha=self._alpha,
            beta=self._beta,
            health_score=self._health_score,
            success_count=self._success_count,
            failure_count=self._failure_count,
            total_count=self._success_count + self._failure_count,
            last_updated_at=self._last_updated_at,
        )
```

### 6.2 `router_core/bandit.py`

```python
"""Multi-acquirer registry and Thompson Sampling coordinator."""

from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np

from router_core.state import AcquirerState, AcquirerStateConfig, AcquirerStateSnapshot


def calculate_gamma_from_half_life(half_life_seconds: float, expected_tps: float) -> float:
    """Derive discrete per-outcome decay factor from half-life and transaction rate."""
    if half_life_seconds <= 0.0:
        raise ValueError(f"half_life_seconds must be > 0.0, got {half_life_seconds}")
    if expected_tps <= 0.0:
        raise ValueError(f"expected_tps must be > 0.0, got {expected_tps}")

    n_half = half_life_seconds * expected_tps
    return math.pow(0.5, 1.0 / n_half)


class BanditStateRegistry:
    """Manages state across all configured acquirers and coordinates Thompson Sampling."""

    def __init__(self, default_config: AcquirerStateConfig | None = None) -> None:
        """Initialize registry with optional default acquirer configuration."""
        self._default_config: AcquirerStateConfig = default_config or AcquirerStateConfig()
        self._acquirers: dict[str, AcquirerState] = {}

    def register_acquirer(
        self,
        acquirer_id: str,
        config: AcquirerStateConfig | None = None,
        initial_timestamp: float | None = None,
    ) -> AcquirerState:
        """Register a new acquirer route with its own state and prior beliefs."""
        if acquirer_id in self._acquirers:
            raise ValueError(f"Acquirer '{acquirer_id}' is already registered")

        effective_config = config or self._default_config
        state = AcquirerState(
            acquirer_id=acquirer_id,
            config=effective_config,
            initial_timestamp=initial_timestamp,
        )
        self._acquirers[acquirer_id] = state
        return state

    def record_outcome(
        self,
        acquirer_id: str,
        success: bool,
        timestamp: float | None = None,
    ) -> AcquirerStateSnapshot:
        """Record outcome for a specific acquirer and return updated snapshot."""
        state = self._acquirers.get(acquirer_id)
        if state is None:
            raise KeyError(f"Acquirer '{acquirer_id}' not found in registry")
        return state.record_outcome(success=success, timestamp=timestamp)

    def sample_all(self, rng: np.random.Generator | None = None) -> dict[str, float]:
        """Draw independent Thompson samples across all registered acquirers."""
        generator = rng if rng is not None else np.random.default_rng()
        return {
            acquirer_id: state.sample(rng=generator)
            for acquirer_id, state in self._acquirers.items()
        }

    def get_state(self, acquirer_id: str) -> AcquirerStateSnapshot:
        """Return state snapshot for a single acquirer."""
        state = self._acquirers.get(acquirer_id)
        if state is None:
            raise KeyError(f"Acquirer '{acquirer_id}' not found in registry")
        return state.get_state()

    def get_all_states(self) -> dict[str, AcquirerStateSnapshot]:
        """Return state snapshots for all registered acquirers."""
        return {
            acquirer_id: state.get_state()
            for acquirer_id, state in self._acquirers.items()
        }

    def list_acquirer_ids(self) -> list[str]:
        """Return list of all currently registered acquirer identifiers."""
        return list(self._acquirers.keys())
```

---

## 7. Performance & Latency Budgets

| Operation | Implementation Complexity | Latency Budget | Rationale |
| :--- | :--- | :--- | :--- |
| `record_outcome` | Pure arithmetic ($O(1)$) | $< 1.0\ \mu\text{s}$ | In-memory float scalar arithmetic on cached registers. |
| `sample` (single) | `numpy.random.beta` | $< 2.5\ \mu\text{s}$ | Generating Beta random variate via NumPy PRNG. |
| `sample_all` (3 acquirers) | $K$ Beta draws | $< 10.0\ \mu\text{s}$ | Well below the router's overall budget (sub-millisecond total). |
| `get_state` | Dataclass instantiation | $< 0.5\ \mu\text{s}$ | Zero allocation overhead using `slots=True`. |

---

## 8. Hand-off Checklist for Backend Engineer

When implementing `router_core/state.py` and `router_core/bandit.py`:
1. [ ] Implement `AcquirerStateConfig` with validation logic in `__post_init__`.
2. [ ] Implement `AcquirerStateSnapshot` with `@property` methods for `expected_success_rate`, `variance`, and `effective_sample_size`.
3. [ ] Implement `AcquirerState` with `record_outcome(success, timestamp)`, `sample(rng)`, and `get_state()`.
4. [ ] Implement `BanditStateRegistry` with `register_acquirer`, `record_outcome`, `sample_all`, and snapshot accessors.
5. [ ] Implement `calculate_gamma_from_half_life(half_life_seconds, expected_tps)`.
6. [ ] Add unit test suite in `tests/router_core/test_state.py` checking:
   - Config boundary validation.
   - Exact numerical step matching against Section 5 test vector.
   - Beta parameter bounds ($\alpha \ge \alpha_0, \beta \ge \beta_0$).
   - Health score bounds ($0.0 \le H \le 1.0$).
   - Thompson Sampling statistical behavior (mean converges to empirical rate under repeated sampling).
   - Multi-acquirer registry isolation.
