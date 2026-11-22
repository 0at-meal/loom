"""Sync, I/O-free routing policies: decide where a payment goes, learn from what came back.

A policy never touches the network or the clock. The async router (``BanditRouter``) and the
discrete-event harness (``scripts/suite``) both drive the same policy objects, passing the
current time in, so a scenario measured in the harness runs the served decision code.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

import numpy as np

from router_core.bandit import BanditStateRegistry
from router_core.gate import TechnicalGate
from router_core.models import RouterConfig
from router_core.pid import (
    PIDConfig,
    PIDDiagnostics,
    PIDState,
    calculate_pid_step,
    project_to_bounded_simplex,
)
from router_core.state import AcquirerStateSnapshot, Outcome
from router_core.value_policy import (
    ValueScaledExplorationConfig,
    apply_value_scaled_policy,
    best_posterior_mean,
)


class FailureKind(StrEnum):
    """Why an attempt did not reach an issuer decision, and whether a retry is safe."""

    FAST_FAIL = "FAST_FAIL"  # 503 or a technical decline code: not processed, safe to retry
    THROTTLED = "THROTTLED"  # 429: not processed, safe to retry
    TIMEOUT = "TIMEOUT"  # no answer in time: may have been authorized, never retry
    AMBIGUOUS = "AMBIGUOUS"  # other non-200, unreadable body, transport error: never retry
    NOT_SENT = "NOT_SENT"  # router-side (no free connection): never reached the acquirer


SAFE_TO_RETRY = frozenset({FailureKind.FAST_FAIL, FailureKind.THROTTLED, FailureKind.NOT_SENT})


@dataclass(frozen=True, slots=True)
class Observation:
    """What one attempt said about its acquirer.

    ``outcome`` is the belief it is evidence for (None: not evidence about the acquirer).
    ``failure`` is set whenever the acquirer did not return an issuer decision.
    """

    outcome: Outcome | None
    failure: FailureKind | None = None
    latency_sec: float = 0.0

    @property
    def authorized(self) -> bool:
        """True when the issuer approved the payment."""
        return self.outcome == Outcome.AUTHORIZED

    @property
    def safe_to_retry(self) -> bool:
        """True when the payment certainly was not processed, so another attempt is safe."""
        return self.failure in SAFE_TO_RETRY


@dataclass(frozen=True, slots=True)
class DecisionContext:
    """What a policy may know about a payment when routing it."""

    transaction_id: str
    amount: float
    attempt: int = 1


@dataclass(frozen=True, slots=True)
class Decision:
    """A routing choice and the distribution it was drawn from.

    ``propensities`` covers every acquirer the policy routes over. It is the distribution of
    the last random step that picked the acquirer, given the policy's earlier random draws
    (one-hot when that step is deterministic).
    """

    transaction_id: str
    acquirer_id: str
    propensities: Mapping[str, float]
    reason: str
    decided_at: float
    attempt: int = 1


@runtime_checkable
class RoutingPolicy(Protocol):
    """A routing policy: sync, deterministic given its seed, no I/O."""

    name: str

    @property
    def acquirer_ids(self) -> tuple[str, ...]:
        """Acquirers this policy routes over, in its configured order."""
        ...

    @property
    def max_attempts(self) -> int:
        """Most attempts this policy makes for one payment (1 = never retries)."""
        ...

    def decide(
        self, ctx: DecisionContext, now: float, exclude: frozenset[str] = frozenset()
    ) -> Decision:
        """Choose an acquirer for ``ctx``, never one in ``exclude``; may update policy state."""
        ...

    def feedback(self, decision: Decision, observation: Observation, now: float) -> None:
        """Update the policy's state with the result of ``decision``."""
        ...

    def should_retry(self, decision: Decision, observation: Observation) -> bool:
        """Whether to make another attempt for this payment on a different acquirer."""
        ...


def one_hot(acquirer_ids: tuple[str, ...] | list[str], chosen: str) -> dict[str, float]:
    """Propensity vector that puts all mass on ``chosen``."""
    return {aid: 1.0 if aid == chosen else 0.0 for aid in acquirer_ids}


def classify_outcome(
    authorized: bool, decline_code: str | None, technical_codes: frozenset[str]
) -> Observation:
    """Classify an HTTP 200 authorization answer by its decline code."""
    if authorized:
        return Observation(Outcome.AUTHORIZED)
    if decline_code in technical_codes:
        return Observation(Outcome.TECHNICAL_FAILURE, FailureKind.FAST_FAIL)
    # Issuer declines (e.g. DO_NOT_HONOR) say nothing about the acquirer
    return Observation(Outcome.ISSUER_DECLINE)


def failure_for_status(status_code: int) -> FailureKind:
    """Failure kind of a non-200 acquirer answer: only 503, 422 and 429 are known safe."""
    if status_code == 429:
        return FailureKind.THROTTLED
    if status_code in (503, 422):
        # 503: unavailable, not processed. 422: the acquirer rejected the request schema.
        return FailureKind.FAST_FAIL
    return FailureKind.AMBIGUOUS


@dataclass(frozen=True, slots=True)
class LoomTrace:
    """Diagnostics of one Loom decision, as the router reports them in ``RoutingResult``."""

    raw_samples: dict[str, float]
    adjusted_samples: dict[str, float] | None
    shrinkage: float | None
    target_allocation: dict[str, float] | None
    smoothed_allocation: dict[str, float] | None
    pid_diagnostics: PIDDiagnostics | None
    value_protected: bool


class ArrivalRate:
    """Exponentially weighted count of first attempts per second (time constant ``tau``)."""

    __slots__ = ("_last", "_n", "_tau")

    def __init__(self, tau_sec: float = 10.0) -> None:
        """Start with no arrivals seen."""
        self._tau = tau_sec
        self._n = 0.0
        self._last: float | None = None

    def observe(self, now: float) -> None:
        """Count one arrival at ``now`` (mutates)."""
        if self._last is not None and now > self._last:
            self._n *= math.exp(-(now - self._last) / self._tau)
        self._last = now if self._last is None else max(self._last, now)
        self._n += 1.0

    @property
    def per_sec(self) -> float:
        """Estimated arrivals per second as of the last arrival."""
        return self._n / self._tau


class LoomPolicy:
    """Thompson Sampling perception, optional value protection, PID smoothing and actuation.

    This is the decision and belief-update code of ``BanditRouter`` (Phases 3, 4, 8 and
    Phase 4b's dispatch-time protection), moved without changes in Stage 0. Stage 1 adds,
    each off unless configured: a technical gate (``RouterConfig.gate_config``) that takes
    acquirers out of the allocation and probes them on the wall clock, one cascade retry
    (``RouterConfig.cascade``) and an exploration floor expressed as a rate
    (``PIDConfig.floor_rate_per_sec``). With all three off, decisions are bit-for-bit the
    Stage 0 ones. Stage 2 adds, also off unless configured: a win-probability PID setpoint
    from ``RouterConfig.allocation_draws`` joint draws, multi-timescale beliefs (in the
    registry's ``AcquirerStateConfig``) and ``withhold_gated_failures``.
    """

    name = "loom"

    def __init__(
        self,
        config: RouterConfig,
        registry: BanditStateRegistry | None = None,
        rng: np.random.Generator | None = None,
        initial_timestamp: float = 0.0,
    ) -> None:
        """Register every configured acquirer and initialise the PID and actuation state."""
        self._config = config
        self._acquirer_ids: tuple[str, ...] = tuple(r.acquirer_id for r in config.routes)
        self._registry = registry if registry is not None else BanditStateRegistry()
        for r in config.routes:
            if r.acquirer_id not in self._registry.list_acquirer_ids():
                self._registry.register_acquirer(
                    acquirer_id=r.acquirer_id,
                    config=r.state_config,
                    initial_timestamp=initial_timestamp,
                )
        self._rng = rng if rng is not None else np.random.default_rng(config.seed)

        self._pid_config: PIDConfig | None = config.pid_config
        self._pid_state: PIDState | None = None
        self._current_allocation: dict[str, float] = {}
        self._cumulative_target: dict[str, float] = {}
        self._dispatched_count: dict[str, int] = {}
        self._last_diagnostics: PIDDiagnostics | None = None
        self._value_scaled_config: ValueScaledExplorationConfig | None = config.value_scaled_config

        if self._pid_config is not None:
            acquirer_ids = list(self._acquirer_ids)
            self._pid_state = PIDState.initialize(acquirer_ids)
            self._current_allocation = dict(self._pid_state.previous_allocation)
            self._cumulative_target = {aid: 0.0 for aid in acquirer_ids}
            self._dispatched_count = {aid: 0 for aid in acquirer_ids}

        self._gate: TechnicalGate | None = (
            TechnicalGate(
                self._acquirer_ids,
                config.gate_config,
                {r.acquirer_id: r.timeout_sec for r in config.routes},
            )
            if config.gate_config is not None
            else None
        )
        self._cascade = config.cascade
        self._arrivals = ArrivalRate()

    @property
    def acquirer_ids(self) -> tuple[str, ...]:
        """Acquirers in route-config order."""
        return self._acquirer_ids

    @property
    def max_attempts(self) -> int:
        """Two attempts with the cascade on, else one."""
        return 2 if self._cascade else 1

    @property
    def gate(self) -> TechnicalGate | None:
        """The technical gate, if configured."""
        return self._gate

    def gate_status(self, now: float) -> dict[str, dict[str, Any]] | None:
        """Per-acquirer gate state for /health (None without a gate)."""
        return None if self._gate is None else self._gate.status(now)

    @property
    def registry(self) -> BanditStateRegistry:
        """The belief registry this policy samples and updates."""
        return self._registry

    @property
    def rng(self) -> np.random.Generator:
        """The generator used for Thompson draws and categorical actuation."""
        return self._rng

    @property
    def value_scaled_config(self) -> ValueScaledExplorationConfig | None:
        """Value-scaled exploration configuration, if any."""
        return self._value_scaled_config

    @property
    def current_allocation(self) -> dict[str, float]:
        """Current smoothed allocation (empty without a PID)."""
        return dict(self._current_allocation)

    @property
    def pid_state(self) -> PIDState | None:
        """PID state, if a PID is configured."""
        return self._pid_state

    @property
    def last_diagnostics(self) -> PIDDiagnostics | None:
        """Diagnostics of the most recent PID step."""
        return self._last_diagnostics

    @property
    def cumulative_target(self) -> dict[str, float]:
        """Deficit actuation: cumulative allocated share per acquirer."""
        return dict(self._cumulative_target)

    @property
    def dispatched_count(self) -> dict[str, int]:
        """Deficit actuation: payments dispatched per acquirer by the allocation stream."""
        return dict(self._dispatched_count)

    def select_route(self, now: float, amount: float = 0.0) -> tuple[str, dict[str, float]]:
        """Sample beliefs at ``now`` and return the argmax arm, without the PID or actuation."""
        raw_samples = self._registry.sample_all(rng=self._rng, now=now)
        if (
            self._value_scaled_config is not None
            and self._value_scaled_config.enabled
            and amount > 0.0
        ):
            states = self._registry.get_all_states()
            means = {aid: s.expected_psr for aid, s in states.items()}
            adjusted_samples, _ = apply_value_scaled_policy(
                samples=raw_samples,
                posterior_means=means,
                amount=amount,
                config=self._value_scaled_config,
            )
            if self._value_scaled_config.protects(amount):
                return best_posterior_mean(means), adjusted_samples
            selected_id = max(
                adjusted_samples.keys(),
                key=lambda aid: (adjusted_samples[aid], aid),
            )
            return selected_id, adjusted_samples

        # Deterministic tie-breaking: max by sample value, then by acquirer_id
        selected_id = max(raw_samples.keys(), key=lambda aid: (raw_samples[aid], aid))
        return selected_id, raw_samples

    def decide(
        self, ctx: DecisionContext, now: float, exclude: frozenset[str] = frozenset()
    ) -> Decision:
        """Choose an acquirer; steps the PID and the actuation counters (see decide_traced)."""
        decision, _trace = self.decide_traced(ctx, now, exclude)
        return decision

    def decide_traced(
        self, ctx: DecisionContext, now: float, exclude: frozenset[str] = frozenset()
    ) -> tuple[Decision, LoomTrace]:
        """Choose an acquirer and return the diagnostics the router reports.

        Mutates the policy: Thompson draws advance the generator and decay beliefs to
        ``now``; unless the payment is value-protected, a gate probe, sent while every gate
        is open, or a retry (``exclude`` non-empty), the PID steps once and the deficit
        counters advance. With a gate, overdue in-flight attempts are counted first (which
        may open a gate) and the chosen attempt is tracked until its feedback.
        """
        gate = self._gate
        probe_id: str | None = None
        if gate is not None:
            gate.scan_overdue(now)
        if not exclude:
            self._arrivals.observe(now)
            if gate is not None and self._may_probe(ctx.amount):
                probe_id = gate.due_probe(now)
        blocked = self._blocked()

        # 1. Perception, Value-Scaled Policy, PID Smoothing & Selection
        draws: dict[str, np.ndarray] | None = None
        if self._config.allocation_draws is not None:
            # Stage 2: one batch of joint draws; its first row is the reported sample.
            draws = self._registry.sample_psr_draws(self._rng, now, self._config.allocation_draws)
            raw_samples = {aid: float(d[0]) for aid, d in draws.items()}
        else:
            raw_samples = self._registry.sample_all(rng=self._rng, now=now)
        effective_samples = raw_samples
        adjusted_samples: dict[str, float] | None = None
        shrinkage: float | None = None
        protected_id: str | None = None

        if (
            self._value_scaled_config is not None
            and self._value_scaled_config.enabled
            and ctx.amount > 0.0
        ):
            states = self._registry.get_all_states()
            means = {aid: s.expected_psr for aid, s in states.items()}
            adjusted_samples, shrinkage = apply_value_scaled_policy(
                samples=raw_samples,
                posterior_means=means,
                amount=ctx.amount,
                config=self._value_scaled_config,
            )
            effective_samples = adjusted_samples
            if self._value_scaled_config.protects(ctx.amount):
                # High-value payment (AUDIT F-10): send it to the best posterior mean. It
                # stays out of the allocation stream, so the PID state and the deficit
                # counters are left exactly as the other payments set them.
                eligible_means = {a: m for a, m in means.items() if a not in blocked}
                protected_id = best_posterior_mean(eligible_means or means)

        target_allocation: dict[str, float] | None = None
        smoothed_allocation: dict[str, float] | None = None
        diagnostics: PIDDiagnostics | None = None
        propensities: dict[str, float]

        if exclude:
            # Another attempt for the same payment: never an excluded acquirer, never a
            # gated one unless nothing else is left. The PID and deficit counters stay put.
            remaining = [aid for aid in self._acquirer_ids if aid not in exclude]
            if not remaining:
                raise ValueError(f"every acquirer is excluded: {sorted(exclude)}")
            eligible = [aid for aid in remaining if aid not in blocked]
            if not eligible:
                assert gate is not None
                selected_id = gate.fallback(exclude)
                reason = "gate_all_open"
            elif self._cascade:
                # A retry is a payment that already failed once: exploit, best posterior mean.
                states = self._registry.get_all_states()
                selected_id = max(
                    eligible,
                    key=lambda aid: (states[aid].expected_psr, -self._acquirer_ids.index(aid)),
                )
                reason = "cascade_retry"
            else:
                selected_id = max(eligible, key=lambda aid: (effective_samples[aid], aid))
                reason = "excluded_fallback"
            propensities = one_hot(self._acquirer_ids, selected_id)
        elif probe_id is not None:
            # Wall-clock probe of an open gate; outside the allocation stream like a
            # protected payment. A fast failure is retried by the cascade, so it costs a hop.
            selected_id = probe_id
            reason = "gate_probe"
            propensities = one_hot(self._acquirer_ids, selected_id)
        elif protected_id is not None:
            selected_id = protected_id
            reason = "value_protected"
            target_allocation = {aid: 1.0 if aid == selected_id else 0.0 for aid in raw_samples}
            if self._pid_config is not None:
                smoothed_allocation = dict(self._current_allocation)
            else:
                smoothed_allocation = dict(target_allocation)
            propensities = one_hot(self._acquirer_ids, selected_id)
        elif self._pid_config is not None and self._pid_state is not None:
            eligible = [aid for aid in self._acquirer_ids if aid not in blocked]
            floors = self._floors(blocked)
            if draws is not None:
                # Stage 2: win probabilities over many joint draws, made feasible (floors)
                # before the PID, so the PID tracks a reachable setpoint (AUDIT F-25).
                target_allocation = project_to_bounded_simplex(
                    self._win_probabilities(draws, eligible, ctx.amount),
                    min_floor=self._pid_config.min_allocation,
                    floors=floors,
                )
            else:
                # Thompson sampling target allocation using effective samples
                if blocked:
                    win_id = max(eligible, key=lambda aid: (effective_samples[aid], aid))
                else:
                    win_id = max(
                        effective_samples.keys(), key=lambda aid: (effective_samples[aid], aid)
                    )
                target_allocation = {
                    aid: 1.0 if aid == win_id else 0.0 for aid in effective_samples
                }

            # PID smoothing step
            step_result = calculate_pid_step(
                target_allocation=target_allocation,
                current_allocation=self._current_allocation,
                state=self._pid_state,
                config=self._pid_config,
                dt=1.0,
                floors=floors,
            )
            self._current_allocation = step_result.smoothed_allocation
            self._pid_state = step_result.next_state
            self._last_diagnostics = step_result.diagnostics
            smoothed_allocation = dict(self._current_allocation)
            diagnostics = step_result.diagnostics

            # Discrete Actuation (Stochastic or Deficit Round-Robin), over the acquirers
            # whose gate is closed; the allocation is renormalised over them.
            if not blocked:
                act = self._current_allocation
            else:
                total = sum(self._current_allocation[aid] for aid in eligible)
                act = {
                    aid: (
                        self._current_allocation[aid] / total if total > 0.0 else 1 / len(eligible)
                    )
                    if aid not in blocked
                    else 0.0
                    for aid in self._acquirer_ids
                }
            if self._pid_config.actuation_mode == "deficit":
                for aid in self._acquirer_ids:
                    self._cumulative_target[aid] += act[aid]
                selected_id = max(
                    sorted(eligible),
                    key=lambda aid: (
                        self._cumulative_target[aid] - self._dispatched_count[aid],
                        aid,
                    ),
                )
                self._dispatched_count[selected_id] += 1
                reason = "pid_deficit"
                propensities = one_hot(self._acquirer_ids, selected_id)
            else:
                keys = sorted(eligible)
                probs = [act[k] for k in keys]
                selected_id = str(self._rng.choice(keys, p=probs))
                reason = "pid_stochastic"
                propensities = {aid: act[aid] for aid in self._acquirer_ids}
        else:
            # Winner-take-all argmax hard-switch (Phase 3 baseline using effective samples)
            candidates = [a for a in effective_samples if a not in blocked]
            selected_id = max(candidates, key=lambda aid: (effective_samples[aid], aid))
            smoothed_allocation = {
                aid: 1.0 if aid == selected_id else 0.0 for aid in effective_samples
            }
            target_allocation = dict(smoothed_allocation)
            reason = "thompson_argmax"
            propensities = one_hot(self._acquirer_ids, selected_id)

        if gate is not None:
            gate.record_dispatch(
                ctx.transaction_id, ctx.attempt, selected_id, now, probe=reason == "gate_probe"
            )
        decision = Decision(
            transaction_id=ctx.transaction_id,
            acquirer_id=selected_id,
            propensities=propensities,
            reason=reason,
            decided_at=now,
            attempt=ctx.attempt,
        )
        trace = LoomTrace(
            raw_samples=raw_samples,
            adjusted_samples=adjusted_samples,
            shrinkage=shrinkage,
            target_allocation=target_allocation,
            smoothed_allocation=smoothed_allocation,
            pid_diagnostics=diagnostics,
            value_protected=reason == "value_protected",
        )
        return decision, trace

    def _win_probabilities(
        self, draws: Mapping[str, np.ndarray], eligible: list[str], amount: float
    ) -> dict[str, float]:
        """Share of the joint Thompson draws each eligible acquirer wins.

        With value scaling on, every draw is blended toward the posterior means by
        lambda(amount) first, as the single-draw setpoint does.
        """
        matrix = np.stack([draws[aid] for aid in eligible], axis=1)
        n = matrix.shape[0]
        cfg = self._value_scaled_config
        if cfg is not None and cfg.enabled and amount > 0.0:
            lam = cfg.calculate_shrinkage(amount)
            states = self._registry.get_all_states()
            means = np.array([states[aid].expected_psr for aid in eligible])
            matrix = (1.0 - lam) * matrix + lam * means
        wins = np.bincount(np.argmax(matrix, axis=1), minlength=len(eligible))
        shares = {aid: float(wins[i]) / n for i, aid in enumerate(eligible)}
        return {aid: shares.get(aid, 0.0) for aid in self._acquirer_ids}

    def _blocked(self) -> frozenset[str]:
        """Acquirers that may not get ordinary traffic now: gated ones, unless all are gated.

        When every gate is open, the acquirers still processing payments stay in the
        allocation (and may take retries), rather than everything piling onto one of them.
        """
        gate = self._gate
        if gate is None:
            return frozenset()
        blocked = gate.blocked()
        if len(blocked) == len(self._acquirer_ids):
            blocked = frozenset(self._acquirer_ids) - gate.recently_working()
        return blocked

    def _may_probe(self, amount: float) -> bool:
        """Whether this payment is cheap enough to be a gate probe (value scaling on)."""
        cfg = self._value_scaled_config
        if self._gate is None or cfg is None or not cfg.enabled:
            return True
        return cfg.calculate_shrinkage(amount) <= self._gate.config.probe_max_shrinkage

    def _floors(self, blocked: frozenset[str]) -> dict[str, float] | None:
        """Per-acquirer PID floors, or None for the plain ``min_allocation`` floor."""
        cfg = self._pid_config
        assert cfg is not None
        if cfg.floor_rate_per_sec is None and not blocked:
            return None
        base = cfg.min_allocation
        rate = self._arrivals.per_sec
        if cfg.floor_rate_per_sec is not None and rate > 0.0:
            base = min(base, cfg.floor_rate_per_sec / rate)
        return {aid: 0.0 if aid in blocked else base for aid in self._acquirer_ids}

    def feedback(self, decision: Decision, observation: Observation, now: float) -> None:
        """Record the attempt's outcome in the chosen acquirer's beliefs (mutates the registry).

        An observation without an outcome (router-side failure) is not evidence and is
        ignored by the beliefs. With a gate, the attempt is also applied to its CUSUM, probe
        or overdue tracking; with ``withhold_gated_failures``, a technical failure of a probe
        or of an acquirer whose gate is not closed goes to the gate only.
        """
        gate = self._gate
        withheld = (
            self._config.withhold_gated_failures
            and gate is not None
            and observation.outcome == Outcome.TECHNICAL_FAILURE
            and (decision.reason == "gate_probe" or not gate.is_closed(decision.acquirer_id))
        )
        if observation.outcome is not None and not withheld:
            self._registry.record_outcome(
                acquirer_id=decision.acquirer_id,
                success=observation.authorized,
                timestamp=now,
                outcome=observation.outcome,
            )
        if self._gate is not None:
            self._gate.record_result(
                decision.transaction_id,
                decision.attempt,
                observation.outcome,
                now,
                throttled=observation.failure == FailureKind.THROTTLED,
                safe=observation.safe_to_retry,
            )

    def should_retry(self, decision: Decision, observation: Observation) -> bool:
        """With the cascade on, retry once after a failure that certainly was not processed.

        Only if another acquirer is eligible (its gate closed); never after a timeout or an
        ambiguous answer.
        """
        if (
            not self._cascade
            or decision.attempt != 1
            or observation.authorized
            or not observation.safe_to_retry
        ):
            return False
        others = [a for a in self._acquirer_ids if a != decision.acquirer_id]
        if self._gate is not None:
            blocked = self._blocked()
            others = [a for a in others if a not in blocked]
        return bool(others)

    def snapshot(self, acquirer_id: str) -> AcquirerStateSnapshot:
        """Belief snapshot of one acquirer as of its last event."""
        return self._registry.get_state(acquirer_id)
