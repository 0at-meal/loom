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
# Stage 2 multi-timescale approval belief (opt-in through approval_half_lives_sec): the
# member half-lives and the fixed-share switch rate. See the Stage 2 decisions-log entry.
LONG_MEMORY_HALF_LIVES_SEC: tuple[float, ...] = (5.0, 60.0, 1800.0, 43200.0)
DEFAULT_MIXTURE_SWITCH_RATE_PER_SEC = 1.0 / 600.0
# --long-memory's switch rate: one regime change a day (chosen on train seeds).
LONG_MEMORY_SWITCH_RATE_PER_SEC = 1.0 / 86400.0


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

    Stage 2 (opt-in): ``approval_half_lives_sec`` replaces the single approval belief with a
    bank of approval Betas, one per half-life, mixed by how well each predicted the issuer
    outcomes so far (fixed-share weights, switch rate ``mixture_switch_rate_per_sec``), and
    ``technical_half_lives_sec`` does the same for the technical belief. With
    ``warm_approval_rate`` set, every approval belief starts as if ``warm_observations``
    issuer outcomes at that rate had been seen, spread over the last ``warm_window_sec``
    (a warm start, e.g. from ``python -m data_layer.cli export-priors``).
    """

    alpha_prior: float = DEFAULT_ALPHA_PRIOR
    beta_prior: float = 1.0
    decay_factor: float | None = None
    initial_health: float = 1.0
    half_life_sec: float | None = None
    approval_half_life_sec: float = DEFAULT_APPROVAL_HALF_LIFE_SEC
    approval_alpha_prior: float = 1.0
    approval_beta_prior: float = 1.0
    approval_half_lives_sec: tuple[float, ...] | None = None
    technical_half_lives_sec: tuple[float, ...] | None = None
    mixture_switch_rate_per_sec: float = DEFAULT_MIXTURE_SWITCH_RATE_PER_SEC
    warm_approval_rate: float | None = None
    warm_observations: float = 0.0
    warm_window_sec: float = 86400.0

    def __post_init__(self) -> None:
        """Validate invariant constraints on configuration parameters."""
        if self.alpha_prior <= 0.0:
            raise ValueError(f"alpha_prior must be > 0.0, got {self.alpha_prior}")
        if self.beta_prior <= 0.0:
            raise ValueError(f"beta_prior must be > 0.0, got {self.beta_prior}")
        if self.approval_alpha_prior <= 0.0 or self.approval_beta_prior <= 0.0:
            raise ValueError("approval priors must be > 0.0")
        for name in ("approval_half_lives_sec", "technical_half_lives_sec"):
            raw = getattr(self, name)
            if raw is None:
                continue
            lives = tuple(float(h) for h in raw)
            if len(lives) < 2 or any(not (math.isfinite(h) and h > 0.0) for h in lives):
                raise ValueError(f"{name} needs at least two finite half-lives > 0, got {raw}")
            object.__setattr__(self, name, lives)
        if self.technical_half_lives_sec is not None and self.decay_factor is not None:
            raise ValueError("technical_half_lives_sec needs wall-clock decay, not decay_factor")
        if not (
            math.isfinite(self.mixture_switch_rate_per_sec)
            and self.mixture_switch_rate_per_sec > 0.0
        ):
            raise ValueError(
                "mixture_switch_rate_per_sec must be finite and > 0.0, "
                f"got {self.mixture_switch_rate_per_sec}"
            )
        if self.warm_approval_rate is not None and not (0.0 < self.warm_approval_rate < 1.0):
            raise ValueError(f"warm_approval_rate must be in (0, 1), got {self.warm_approval_rate}")
        if not (math.isfinite(self.warm_observations) and self.warm_observations >= 0.0):
            raise ValueError(f"warm_observations must be >= 0, got {self.warm_observations}")
        if not (math.isfinite(self.warm_window_sec) and self.warm_window_sec > 0.0):
            raise ValueError(f"warm_window_sec must be > 0, got {self.warm_window_sec}")
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

    def warm_approval(self, half_life_sec: float | None = None) -> tuple[float, float]:
        """Starting approval Beta for a belief with this half-life: prior plus warm counts.

        The warm-start outcomes are taken as spread evenly over the last ``warm_window_sec``
        seconds, so a belief keeps the share of them its half-life would have kept:
        n * h / (W ln 2) * (1 - 2^(-W/h)). A fast belief starts nearly cold, a slow one warm.
        """
        a0, b0 = self.approval_alpha_prior, self.approval_beta_prior
        if self.warm_approval_rate is None or self.warm_observations <= 0.0:
            return a0, b0
        h = half_life_sec if half_life_sec is not None else self.approval_half_life_sec
        w = self.warm_window_sec
        n = self.warm_observations * h / (w * math.log(2.0)) * -math.expm1(-w * math.log(2.0) / h)
        p = self.warm_approval_rate
        return a0 + n * p, b0 + n * (1.0 - p)


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


class BeliefMixture:
    """A Bernoulli belief as a bank of wall-clock-decayed Betas mixed by predictive likelihood.

    Stage 2 uses one for the approval belief and one for the technical belief. Member k
    forgets with half-life ``half_lives[k]`` toward the shared prior. Each outcome multiplies
    member k's weight by the probability it gave that outcome, and the weights are then mixed
    toward uniform at ``switch_rate`` per second (fixed share), so a member that predicted
    badly can regain weight once it predicts well. After a large shift a fast member wins
    within a few outcomes; in steady state the slow members win and resolve small gaps.
    """

    __slots__ = ("_a", "_a0", "_at", "_b", "_b0", "_lives", "_prefix", "_rate", "_w")

    def __init__(
        self,
        half_lives: tuple[float, ...],
        alpha_prior: float,
        beta_prior: float,
        switch_rate: float,
        now: float,
        starts: list[tuple[float, float]] | None = None,
        prefix: str = "approval",
    ) -> None:
        """Start member k at ``starts[k]`` (default: the prior).

        Weights start equal, or proportional to each member's evidence above the prior when
        ``starts`` carries a warm start (warm history is evidence for the slow members).
        """
        k = len(half_lives)
        if starts is None:
            starts = [(alpha_prior, beta_prior)] * k
        if len(starts) != k:
            raise ValueError(f"need {k} member starts, got {len(starts)}")
        self._lives = half_lives
        self._a0, self._b0 = alpha_prior, beta_prior
        self._a = [a for a, _ in starts]
        self._b = [b for _, b in starts]
        evidence = [a + b - alpha_prior - beta_prior for a, b in starts]
        total = sum(evidence)
        self._w = [e / total for e in evidence] if total > 0.0 else [1.0 / k] * k
        self._w = [max(w, 1e-12) for w in self._w]
        self._rate = switch_rate
        self._at = now
        self._prefix = prefix

    @property
    def weights(self) -> tuple[float, ...]:
        """Current mixture weights, one per member (sum 1)."""
        return tuple(self._w)

    @property
    def members(self) -> tuple[tuple[float, float], ...]:
        """Current (alpha, beta) of every member, as of the last advance."""
        return tuple(zip(self._a, self._b, strict=True))

    def advance(self, now: float) -> None:
        """Decay every member and share weight for the time since the last call (mutates)."""
        dt = now - self._at
        if dt <= 0.0:
            return
        a0, b0 = self._a0, self._b0
        for i, h in enumerate(self._lives):
            f = math.pow(0.5, dt / h)
            self._a[i] = max(a0, a0 + f * (self._a[i] - a0))
            self._b[i] = max(b0, b0 + f * (self._b[i] - b0))
        share = -math.expm1(-self._rate * dt)
        k = len(self._w)
        self._w = [(1.0 - share) * w + share / k for w in self._w]
        self._at = now

    def observe(self, success: bool, now: float) -> None:
        """Advance to ``now``, reweight members by their prediction, add the outcome (mutates)."""
        self.advance(now)
        lik = [(a if success else b) / (a + b) for a, b in zip(self._a, self._b, strict=True)]
        w = [wi * li for wi, li in zip(self._w, lik, strict=True)]
        total = sum(w)
        self._w = [max(wi / total, 1e-12) for wi in w]
        x = 1.0 if success else 0.0
        self._a = [a + x for a in self._a]
        self._b = [b + (1.0 - x) for b in self._b]

    def mean(self) -> float:
        """Mixture posterior mean."""
        return sum(w * a / (a + b) for w, a, b in zip(self._w, self._a, self._b, strict=True))

    def moment_matched(self) -> tuple[float, float]:
        """A single Beta with the mixture's mean and variance, for snapshots and the ledger."""
        m = 0.0
        second = 0.0
        for w, a, b in zip(self._w, self._a, self._b, strict=True):
            n = a + b
            mk = a / n
            m += w * mk
            second += w * (mk * (1.0 - mk) / (n + 1.0) + mk * mk)
        var = max(second - m * m, 1e-18)
        n_eff = max(m * (1.0 - m) / var - 1.0, 1e-9)
        return m * n_eff, (1.0 - m) * n_eff

    def sample(self, rng: np.random.Generator) -> float:
        """One Thompson draw: pick a member by weight, then draw from its Beta."""
        u = float(rng.random())
        k = len(self._w) - 1
        acc = 0.0
        for i, w in enumerate(self._w):
            acc += w
            if u < acc:
                k = i
                break
        return float(rng.beta(self._a[k], self._b[k]))

    def sample_many(self, rng: np.random.Generator, n: int) -> np.ndarray:
        """``n`` independent Thompson draws from the mixture."""
        cdf = np.cumsum(self._w)
        idx = np.searchsorted(cdf, rng.random(n) * cdf[-1], side="right")
        np.minimum(idx, len(self._w) - 1, out=idx)
        draws: np.ndarray = rng.beta(np.asarray(self._a)[idx], np.asarray(self._b)[idx])
        return draws

    def export(self) -> dict[str, float]:
        """Flat state for persistence: ``{prefix}_m{k}_alpha|beta|weight`` and ``_mixture_at``."""
        p = self._prefix
        out: dict[str, float] = {f"{p}_mixture_at": self._at}
        for i, (a, b, w) in enumerate(zip(self._a, self._b, self._w, strict=True)):
            out[f"{p}_m{i}_alpha"] = a
            out[f"{p}_m{i}_beta"] = b
            out[f"{p}_m{i}_weight"] = w
        return out

    def restore(self, data: dict[str, float]) -> bool:
        """Load state saved by :meth:`export`; False (unchanged) if the member count differs."""
        p, k = self._prefix, len(self._lives)
        keys = [f"{p}_m{i}_{f}" for i in range(k) for f in ("alpha", "beta", "weight")]
        if f"{p}_mixture_at" not in data or any(key not in data for key in keys):
            return False
        if f"{p}_m{k}_alpha" in data:
            return False
        self._a = [float(data[f"{p}_m{i}_alpha"]) for i in range(k)]
        self._b = [float(data[f"{p}_m{i}_beta"]) for i in range(k)]
        w = [float(data[f"{p}_m{i}_weight"]) for i in range(k)]
        total = sum(w)
        self._w = [wi / total for wi in w] if total > 0.0 else [1.0 / k] * k
        self._at = float(data[f"{p}_mixture_at"])
        return True


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
    # Stage 2 multi-timescale approval: member weights, fastest first (empty when off). The
    # approval_alpha/approval_beta above are then the mixture's moment-matched Beta.
    approval_weights: tuple[float, ...] = ()
    technical_weights: tuple[float, ...] = ()

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
        self._approval_alpha, self._approval_beta = self._config.warm_approval()
        self._approval_decayed_at: float = self._last_updated_at
        cfg = self._config
        lives = cfg.approval_half_lives_sec
        self._mixture: BeliefMixture | None = (
            BeliefMixture(
                lives,
                cfg.approval_alpha_prior,
                cfg.approval_beta_prior,
                cfg.mixture_switch_rate_per_sec,
                self._last_updated_at,
                starts=[cfg.warm_approval(h) for h in lives],
            )
            if lives is not None
            else None
        )
        tech = cfg.technical_half_lives_sec
        self._tech_mixture: BeliefMixture | None = (
            BeliefMixture(
                tech,
                cfg.alpha_prior,
                cfg.beta_prior,
                cfg.mixture_switch_rate_per_sec,
                self._last_updated_at,
                prefix="technical",
            )
            if tech is not None
            else None
        )

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
        if self._tech_mixture is not None:
            self._tech_mixture.advance(now)
            self._sync_technical()
            self._decayed_at = now
            return
        self._alpha, self._beta = decay_beliefs(
            self._config, self._alpha, self._beta, now - self._decayed_at
        )
        self._health_score = self._config.wall_clock_health(self._alpha, self._beta)
        self._decayed_at = now

    def _sync_technical(self) -> None:
        """Mirror the technical mixture into alpha/beta (moment-matched) and the health score."""
        assert self._tech_mixture is not None
        self._alpha, self._beta = self._tech_mixture.moment_matched()
        self._health_score = self._config.wall_clock_health(self._alpha, self._beta)

    def _approval_to(self, now: float, approved: bool | None = None) -> None:
        """Decay the approval belief up to ``now`` and optionally add an issuer outcome."""
        if self._mixture is not None:
            if approved is None:
                self._mixture.advance(now)
            else:
                self._mixture.observe(approved, now)
            self._approval_decayed_at = max(self._approval_decayed_at, now)
            return
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
        processed = kind != Outcome.TECHNICAL_FAILURE
        if self._tech_mixture is not None:
            self._tech_mixture.observe(processed, now)
            self._sync_technical()
        else:
            elapsed = max(0.0, now - self._decayed_at)
            self._alpha, self._beta, self._health_score = step_beliefs(
                self._config, self._alpha, self._beta, self._health_score, processed, elapsed
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
        if self._tech_mixture is not None:
            return self._tech_mixture.sample(generator)
        return float(generator.beta(self._alpha, self._beta))

    def sample_approval(
        self, rng: np.random.Generator | None = None, now: float | None = None
    ) -> float:
        """Draw a Thompson sample from the approval belief, decayed to ``now`` when given."""
        if now is not None:
            self._approval_to(now)
        generator = rng if rng is not None else np.random.default_rng()
        if self._mixture is not None:
            return self._mixture.sample(generator)
        return float(generator.beta(self._approval_alpha, self._approval_beta))

    def sample_psr_many(self, rng: np.random.Generator, n: int, now: float) -> np.ndarray:
        """``n`` independent Thompson draws of technical x approval, decayed to ``now``."""
        self._decay_to(now)
        self._approval_to(now)
        technical: np.ndarray = (
            self._tech_mixture.sample_many(rng, n)
            if self._tech_mixture is not None
            else rng.beta(self._alpha, self._beta, size=n)
        )
        approval: np.ndarray = (
            self._mixture.sample_many(rng, n)
            if self._mixture is not None
            else rng.beta(self._approval_alpha, self._approval_beta, size=n)
        )
        draws: np.ndarray = technical * approval
        return draws

    def _approval_params(self) -> tuple[float, float]:
        """Approval Beta for snapshots: the single belief, or the mixture moment-matched."""
        if self._mixture is not None:
            return self._mixture.moment_matched()
        return self._approval_alpha, self._approval_beta

    def export_beliefs(self) -> dict[str, float]:
        """Return the raw belief state, including decay reference times, for persistence."""
        mixture = self._mixture.export() if self._mixture is not None else {}
        if self._tech_mixture is not None:
            mixture.update(self._tech_mixture.export())
        return {
            **mixture,
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
        if self._mixture is not None:
            # A snapshot without a matching mixture (older format, other member count) leaves
            # the mixture at its starting state rather than guessing member counts.
            self._mixture.restore(data)
        if self._tech_mixture is not None and self._tech_mixture.restore(data):
            self._sync_technical()

    def get_state(self, now: float | None = None) -> AcquirerStateSnapshot:
        """Return a snapshot, decayed to ``now`` when given, else as of the last event."""
        if now is not None:
            self._decay_to(now)
            self._approval_to(now)
        approval_alpha, approval_beta = self._approval_params()
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
            approval_alpha=approval_alpha,
            approval_beta=approval_beta,
            approval_weights=self._mixture.weights if self._mixture is not None else (),
            technical_weights=(
                self._tech_mixture.weights if self._tech_mixture is not None else ()
            ),
        )
