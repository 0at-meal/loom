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
    alpha_prior: float = 1.0
    beta_prior: float = 1.0

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
        return max(0.0, (self.alpha - self.alpha_prior) + (self.beta - self.beta_prior))


class AcquirerState:
    """Encapsulates the decaying Beta belief and EWMA health score for a single acquirer."""

    def __init__(
        self,
        acquirer_id: str,
        config: AcquirerStateConfig | None = None,
        initial_timestamp: float | None = None,
    ) -> None:
        """Initialize acquirer state with prior parameters and optimistic health."""
        if not isinstance(acquirer_id, str) or not acquirer_id.strip():
            raise ValueError("acquirer_id must be a non-empty string")

        self._acquirer_id: str = acquirer_id.strip()
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

        # Mean-reverting decayed Beta parameters, clamped above prior against floating-point drift
        self._alpha = max(a0, a0 + gamma * (self._alpha - a0) + x)
        self._beta = max(b0, b0 + gamma * (self._beta - b0) + (1.0 - x))

        # EWMA health score update, strictly clamped in [0.0, 1.0]
        new_health = gamma * self._health_score + (1.0 - gamma) * x
        self._health_score = max(0.0, min(1.0, new_health))

        # Cumulative unweighted lifetime counters
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
            alpha_prior=self._config.alpha_prior,
            beta_prior=self._config.beta_prior,
        )
