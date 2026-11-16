"""Sync, I/O-free routing policies: decide where a payment goes, learn from what came back.

A policy never touches the network or the clock. The async router (``BanditRouter``) and the
discrete-event harness (``scripts/suite``) both drive the same policy objects, passing the
current time in, so a scenario measured in the harness runs the served decision code.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol, runtime_checkable

import numpy as np

from router_core.bandit import BanditStateRegistry
from router_core.models import RouterConfig
from router_core.pid import PIDConfig, PIDDiagnostics, PIDState, calculate_pid_step
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


class LoomPolicy:
    """Thompson Sampling perception, optional value protection, PID smoothing and actuation.

    This is the decision and belief-update code of ``BanditRouter`` (Phases 3, 4, 8 and
    Phase 4b's dispatch-time protection), moved without changes. Loom never retries in
    Stage 0.
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

    @property
    def acquirer_ids(self) -> tuple[str, ...]:
        """Acquirers in route-config order."""
        return self._acquirer_ids

    @property
    def max_attempts(self) -> int:
        """Loom makes one attempt per payment."""
        return 1

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
        ``now``; unless the payment is value-protected or ``exclude`` is non-empty, the PID
        steps once and the deficit counters advance.
        """
        # 1. Perception, Value-Scaled Policy, PID Smoothing & Selection
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
                protected_id = best_posterior_mean(means)

        target_allocation: dict[str, float] | None = None
        smoothed_allocation: dict[str, float] | None = None
        diagnostics: PIDDiagnostics | None = None
        propensities: dict[str, float]

        if exclude:
            # Another attempt for the same payment (unused by Loom in Stage 0): best remaining
            # sample, leaving the PID and the deficit counters untouched.
            remaining = [aid for aid in effective_samples if aid not in exclude]
            if not remaining:
                raise ValueError(f"every acquirer is excluded: {sorted(exclude)}")
            selected_id = max(remaining, key=lambda aid: (effective_samples[aid], aid))
            reason = "excluded_fallback"
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
            # Thompson sampling target allocation using effective samples
            win_id = max(effective_samples.keys(), key=lambda aid: (effective_samples[aid], aid))
            target_allocation = {aid: 1.0 if aid == win_id else 0.0 for aid in effective_samples}

            # PID smoothing step
            step_result = calculate_pid_step(
                target_allocation=target_allocation,
                current_allocation=self._current_allocation,
                state=self._pid_state,
                config=self._pid_config,
                dt=1.0,
            )
            self._current_allocation = step_result.smoothed_allocation
            self._pid_state = step_result.next_state
            self._last_diagnostics = step_result.diagnostics
            smoothed_allocation = dict(self._current_allocation)
            diagnostics = step_result.diagnostics

            # Discrete Actuation (Stochastic or Deficit Round-Robin)
            if self._pid_config.actuation_mode == "deficit":
                for aid in self._acquirer_ids:
                    self._cumulative_target[aid] += self._current_allocation[aid]
                selected_id = max(
                    sorted(self._acquirer_ids),
                    key=lambda aid: (
                        self._cumulative_target[aid] - self._dispatched_count[aid],
                        aid,
                    ),
                )
                self._dispatched_count[selected_id] += 1
                reason = "pid_deficit"
                propensities = one_hot(self._acquirer_ids, selected_id)
            else:
                keys = sorted(self._current_allocation.keys())
                probs = [self._current_allocation[k] for k in keys]
                selected_id = str(self._rng.choice(keys, p=probs))
                reason = "pid_stochastic"
                propensities = {aid: self._current_allocation[aid] for aid in self._acquirer_ids}
        else:
            # Winner-take-all argmax hard-switch (Phase 3 baseline using effective samples)
            selected_id = max(
                effective_samples.keys(),
                key=lambda aid: (effective_samples[aid], aid),
            )
            smoothed_allocation = {
                aid: 1.0 if aid == selected_id else 0.0 for aid in effective_samples
            }
            target_allocation = dict(smoothed_allocation)
            reason = "thompson_argmax"
            propensities = one_hot(self._acquirer_ids, selected_id)

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
            value_protected=protected_id is not None,
        )
        return decision, trace

    def feedback(self, decision: Decision, observation: Observation, now: float) -> None:
        """Record the attempt's outcome in the chosen acquirer's beliefs (mutates the registry).

        An observation without an outcome (router-side failure) is not evidence and is
        ignored.
        """
        if observation.outcome is None:
            return
        self._registry.record_outcome(
            acquirer_id=decision.acquirer_id,
            success=observation.authorized,
            timestamp=now,
            outcome=observation.outcome,
        )

    def should_retry(self, decision: Decision, observation: Observation) -> bool:
        """Loom does not retry in Stage 0 (cascade is Stage 1)."""
        return False

    def snapshot(self, acquirer_id: str) -> AcquirerStateSnapshot:
        """Belief snapshot of one acquirer as of its last event."""
        return self._registry.get_state(acquirer_id)
