"""Domain models and state transition logic for per-acquirer health and bandit beliefs."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from enum import StrEnum

import numpy as np

# Half-life of an observation's weight, in seconds. 2.3 s matches the old per-observation
# gamma=0.98 (half-life 34 observations) for an arm carrying all of 15 TPS.
DEFAULT_HALF_LIFE_SEC = 2.3
# Issuer approval rates move slowly and are noisy, so their evidence is kept much longer.
DEFAULT_APPROVAL_HALF_LIFE_SEC = 60.0
# Technical prior Beta(4, 1): an acquirer we know nothing about is assumed up ~80% of the
# time. With wall-clock decay a low-traffic acquirer's technical belief sits near this prior,
# so a Beta(1, 1) prior would undervalue recovered acquirers (see docs/WHITEPAPER_L1.md 5.6).
DEFAULT_ALPHA_PRIOR = 4.0


class Outcome(StrEnum):
    """What a payment attempt says about the acquirer (AUDIT F-02).

    AUTHORIZED: the acquirer answered and the issuer approved.
    ISSUER_DECLINE: the acquirer answered; the issuer declined (e.g. DO_NOT_HONOR).
    TECHNICAL_FAILURE: the acquirer did not process the payment (outage, 5xx, timeout).
    """

    AUTHORIZED = "AUTHORIZED"
    ISSUER_DECLINE = "ISSUER_DECLINE"
    TECHNICAL_FAILURE = "TECHNICAL_FAILURE"

    @classmethod
    def from_success(cls, success: bool, outcome: Outcome | None) -> Outcome:
        """Resolve an outcome kind; a bare boolean means authorized or technical failure."""
        if outcome is None:
            return cls.AUTHORIZED if success else cls.TECHNICAL_FAILURE
        if success != (outcome == cls.AUTHORIZED):
            raise ValueError(f"success={success} contradicts outcome={outcome}")
        return outcome


@dataclass(frozen=True, slots=True)
class AcquirerStateConfig:
    """Immutable configuration for an acquirer's bandit and health model.

    Each acquirer has two Beta beliefs. The technical belief (``alpha``/``beta``) counts
    whether the acquirer processed the payment; the approval belief counts whether the
    issuer approved it, given that the acquirer answered.

    Beliefs decay on the wall clock by default: an observation's weight halves every
    ``half_life_sec`` seconds (technical) or ``approval_half_life_sec`` seconds (approval),
    whether or not the arm receives traffic. Passing ``decay_factor`` instead selects the
    original per-observation decay for the technical belief, where weights shrink by that
    factor only when the arm itself records an outcome.
    """

    alpha_prior: float = DEFAULT_ALPHA_PRIOR
    beta_prior: float = 1.0
    decay_factor: float | None = None
    initial_health: float = 1.0
    half_life_sec: float | None = None
    approval_half_life_sec: float = DEFAULT_APPROVAL_HALF_LIFE_SEC
    approval_alpha_prior: float = 1.0
    approval_beta_prior: float = 1.0

    def __post_init__(self) -> None:
        """Validate invariant constraints on configuration parameters."""
        if self.alpha_prior <= 0.0:
            raise ValueError(f"alpha_prior must be > 0.0, got {self.alpha_prior}")
        if self.beta_prior <= 0.0:
            raise ValueError(f"beta_prior must be > 0.0, got {self.beta_prior}")
        if self.approval_alpha_prior <= 0.0 or self.approval_beta_prior <= 0.0:
            raise ValueError("approval priors must be > 0.0")
        if self.decay_factor is not None and self.half_life_sec is not None:
            raise ValueError("set either half_life_sec or decay_factor, not both")
        if self.decay_factor is not None and not (0.0 < self.decay_factor < 1.0):
            raise ValueError(f"decay_factor must be in (0.0, 1.0), got {self.decay_factor}")
        if self.half_life_sec is not None and not (
            math.isfinite(self.half_life_sec) and self.half_life_sec > 0.0
        ):
            raise ValueError(f"half_life_sec must be finite and > 0.0, got {self.half_life_sec}")
        if not (0.0 <= self.initial_health <= 1.0):
            raise ValueError(f"initial_health must be in [0.0, 1.0], got {self.initial_health}")
        if not (math.isfinite(self.approval_half_life_sec) and self.approval_half_life_sec > 0.0):
            raise ValueError(
                "approval_half_life_sec must be finite and > 0.0, "
                f"got {self.approval_half_life_sec}"
            )
        if self.decay_factor is None and self.half_life_sec is None:
            object.__setattr__(self, "half_life_sec", DEFAULT_HALF_LIFE_SEC)

    @property
    def uses_wall_clock(self) -> bool:
        """True when beliefs decay with elapsed seconds rather than per observation."""
        return self.half_life_sec is not None

    def decay_multiplier(self, elapsed_sec: float) -> float:
        """Weight left on existing observations after ``elapsed_sec`` (wall-clock mode)."""
        assert self.half_life_sec is not None
        return math.pow(0.5, max(0.0, elapsed_sec) / self.half_life_sec)

    def approval_multiplier(self, elapsed_sec: float) -> float:
        """Weight left on existing approval observations after ``elapsed_sec``."""
        return math.pow(0.5, max(0.0, elapsed_sec) / self.approval_half_life_sec)

    def wall_clock_health(self, alpha: float, beta: float) -> float:
        """Decayed success fraction, with initial_health counted as one pseudo-observation."""
        successes = max(0.0, alpha - self.alpha_prior)
        failures = max(0.0, beta - self.beta_prior)
        return (successes + self.initial_health) / (successes + failures + 1.0)


def decay_beliefs(
    config: AcquirerStateConfig, alpha: float, beta: float, elapsed_sec: float
) -> tuple[float, float]:
    """Decay Beta parameters toward the prior by ``elapsed_sec`` of wall-clock time."""
    if not config.uses_wall_clock:
        return alpha, beta
    f = config.decay_multiplier(elapsed_sec)
    a0, b0 = config.alpha_prior, config.beta_prior
    return max(a0, a0 + f * (alpha - a0)), max(b0, b0 + f * (beta - b0))


def step_beliefs(
    config: AcquirerStateConfig,
    alpha: float,
    beta: float,
    health: float,
    success: bool,
    elapsed_sec: float,
) -> tuple[float, float, float]:
    """Apply one outcome; return the new (alpha, beta, health).

    Wall-clock mode decays by ``elapsed_sec`` since the last decay, then adds the outcome.
    Per-observation mode ignores ``elapsed_sec`` and decays by ``decay_factor``.
    """
    x = 1.0 if success else 0.0
    if config.uses_wall_clock:
        alpha, beta = decay_beliefs(config, alpha, beta, elapsed_sec)
        alpha += x
        beta += 1.0 - x
        return alpha, beta, config.wall_clock_health(alpha, beta)

    gamma = config.decay_factor
    assert gamma is not None
    a0, b0 = config.alpha_prior, config.beta_prior
    # Mean-reverting decayed Beta parameters, clamped above prior against drift
    new_alpha = max(a0, a0 + gamma * (alpha - a0) + x)
    new_beta = max(b0, b0 + gamma * (beta - b0) + (1.0 - x))
    # EWMA health score update, strictly clamped in [0.0, 1.0]
    new_health = max(0.0, min(1.0, gamma * health + (1.0 - gamma) * x))
    return new_alpha, new_beta, new_health


def step_approval(
    config: AcquirerStateConfig,
    alpha: float,
    beta: float,
    approved: bool | None,
    elapsed_sec: float,
) -> tuple[float, float]:
    """Decay the approval belief by ``elapsed_sec``; add an issuer outcome unless None."""
    f = config.approval_multiplier(elapsed_sec)
    a0, b0 = config.approval_alpha_prior, config.approval_beta_prior
    alpha = max(a0, a0 + f * (alpha - a0))
    beta = max(b0, b0 + f * (beta - b0))
    if approved is not None:
        alpha += 1.0 if approved else 0.0
        beta += 0.0 if approved else 1.0
    return alpha, beta


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
    approval_alpha: float = 1.0
    approval_beta: float = 1.0

    @property
    def expected_success_rate(self) -> float:
        """Technical posterior mean: chance the acquirer processes a payment."""
        return self.alpha / (self.alpha + self.beta)

    @property
    def expected_approval_rate(self) -> float:
        """Approval posterior mean: chance the issuer approves, given the acquirer answered."""
        return self.approval_alpha / (self.approval_alpha + self.approval_beta)

    @property
    def expected_psr(self) -> float:
        """Expected payment success rate through this acquirer: technical times approval."""
        return self.expected_success_rate * self.expected_approval_rate

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
    """Encapsulates the decaying Beta belief and health score for a single acquirer."""

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
        # Time the wall-clock decay has been applied up to.
        self._decayed_at: float = self._last_updated_at
        self._approval_alpha: float = self._config.approval_alpha_prior
        self._approval_beta: float = self._config.approval_beta_prior
        self._approval_decayed_at: float = self._last_updated_at

    @property
    def acquirer_id(self) -> str:
        """Return the unique identifier for this acquirer."""
        return self._acquirer_id

    @property
    def config(self) -> AcquirerStateConfig:
        """Return the configuration parameters for this acquirer."""
        return self._config

    def _decay_to(self, now: float) -> None:
        """Apply wall-clock decay up to ``now``; a clock step backwards counts as zero."""
        if not self._config.uses_wall_clock or now <= self._decayed_at:
            return
        self._alpha, self._beta = decay_beliefs(
            self._config, self._alpha, self._beta, now - self._decayed_at
        )
        self._health_score = self._config.wall_clock_health(self._alpha, self._beta)
        self._decayed_at = now

    def _approval_to(self, now: float, approved: bool | None = None) -> None:
        """Decay the approval belief up to ``now`` and optionally add an issuer outcome."""
        elapsed = max(0.0, now - self._approval_decayed_at)
        self._approval_alpha, self._approval_beta = step_approval(
            self._config, self._approval_alpha, self._approval_beta, approved, elapsed
        )
        self._approval_decayed_at = max(self._approval_decayed_at, now)

    def record_outcome(
        self,
        success: bool,
        timestamp: float | None = None,
        outcome: Outcome | None = None,
    ) -> AcquirerStateSnapshot:
        """Decay existing beliefs, then add one transaction outcome.

        ``success`` is whether the payment was authorized. ``outcome`` says which belief
        the result is evidence for; without it, a failure counts as a technical failure.
        """
        kind = Outcome.from_success(success, outcome)
        now = timestamp if timestamp is not None else time.time()
        elapsed = max(0.0, now - self._decayed_at)
        self._alpha, self._beta, self._health_score = step_beliefs(
            self._config,
            self._alpha,
            self._beta,
            self._health_score,
            kind != Outcome.TECHNICAL_FAILURE,
            elapsed,
        )
        self._decayed_at = max(self._decayed_at, now)
        self._approval_to(
            now, None if kind == Outcome.TECHNICAL_FAILURE else kind == Outcome.AUTHORIZED
        )

        # Cumulative unweighted lifetime counters
        if success:
            self._success_count += 1
        else:
            self._failure_count += 1

        self._last_updated_at = now
        return self.get_state()

    def sample(self, rng: np.random.Generator | None = None, now: float | None = None) -> float:
        """Draw a Thompson sample from the technical belief, decayed to ``now`` when given."""
        if now is not None:
            self._decay_to(now)
        generator = rng if rng is not None else np.random.default_rng()
        return float(generator.beta(self._alpha, self._beta))

    def sample_approval(
        self, rng: np.random.Generator | None = None, now: float | None = None
    ) -> float:
        """Draw a Thompson sample from the approval belief, decayed to ``now`` when given."""
        if now is not None:
            self._approval_to(now)
        generator = rng if rng is not None else np.random.default_rng()
        return float(generator.beta(self._approval_alpha, self._approval_beta))

    def export_beliefs(self) -> dict[str, float]:
        """Return the raw belief state, including decay reference times, for persistence."""
        return {
            "alpha": self._alpha,
            "beta": self._beta,
            "health_score": self._health_score,
            "success_count": float(self._success_count),
            "failure_count": float(self._failure_count),
            "last_updated_at": self._last_updated_at,
            "decayed_at": self._decayed_at,
            "approval_alpha": self._approval_alpha,
            "approval_beta": self._approval_beta,
            "approval_decayed_at": self._approval_decayed_at,
        }

    def restore_beliefs(self, data: dict[str, float]) -> None:
        """Load beliefs saved by :meth:`export_beliefs`.

        Decay reference times are kept, so the next read or update applies decay for the
        whole time since the snapshot, including any downtime.
        """
        self._alpha = float(data["alpha"])
        self._beta = float(data["beta"])
        self._health_score = float(data["health_score"])
        self._success_count = int(data["success_count"])
        self._failure_count = int(data["failure_count"])
        self._last_updated_at = float(data["last_updated_at"])
        self._decayed_at = float(data["decayed_at"])
        self._approval_alpha = float(data["approval_alpha"])
        self._approval_beta = float(data["approval_beta"])
        self._approval_decayed_at = float(data["approval_decayed_at"])

    def get_state(self, now: float | None = None) -> AcquirerStateSnapshot:
        """Return a snapshot, decayed to ``now`` when given, else as of the last event."""
        if now is not None:
            self._decay_to(now)
            self._approval_to(now)
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
            approval_alpha=self._approval_alpha,
            approval_beta=self._approval_beta,
        )
