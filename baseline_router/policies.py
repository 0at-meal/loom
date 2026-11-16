"""Baseline routing policies: the repo's static breaker and three honest comparators.

All implement ``router_core.policy.RoutingPolicy`` (sync, no I/O). Parameter defaults are
fixed in the Stage 0 decisions-log entry and are not tuned on any benchmark seed.

* ``StaticBreakerPolicy``: the existing ``StaticBaselineRouter`` logic, unchanged (counts issuer
  declines as failures, transaction-counted cooldown, all-tripped falls back to the primary).
* ``CompetentBreakerPolicy``: port of the audit's throwaway ``CompetentRouter``
  (docs/AUDIT.md, appendix ``common.py``), with a wall-clock half-open timer.
* ``SRWindowPolicy``: re-implementation of bucketed success-rate routing as Juspay/Hyperswitch
  describe it publicly; parameter values are ours.
* ``SlidingWindowUCBPolicy``: Dream11's sliding-window UCB as printed in arXiv 2308.01028v2.
"""

from __future__ import annotations

import logging
import math
from collections import deque
from collections.abc import Sequence

import numpy as np

from baseline_router.models import (
    FailoverPolicyConfig,
    FailoverThresholdType,
    RouteHealthStatus,
    StaticRouteStateSnapshot,
)
from router_core.policy import Decision, DecisionContext, Observation, one_hot
from router_core.state import Outcome

logger = logging.getLogger("loom.baseline_router")


def _check_ids(acquirer_ids: Sequence[str]) -> tuple[str, ...]:
    """Validate a non-empty, duplicate-free acquirer list."""
    ids = tuple(acquirer_ids)
    if not ids:
        raise ValueError("at least one acquirer is required")
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate acquirer ids: {list(ids)}")
    return ids


def _remaining(ids: tuple[str, ...], exclude: frozenset[str]) -> list[str]:
    """Acquirers not excluded, in configured order; raises if none remain."""
    rest = [a for a in ids if a not in exclude]
    if not rest:
        raise ValueError(f"every acquirer is excluded: {sorted(exclude)}")
    return rest


# ---------------------------------------------------------------------------------------------
# Static breaker (the repo's baseline, unchanged)
# ---------------------------------------------------------------------------------------------


class _MutableRouteState:
    """Internal mutable tracking for a static route's counters and circuit breaker."""

    __slots__ = (
        "acquirer_id",
        "consecutive_failures",
        "failure_count",
        "history",
        "last_updated_at",
        "priority",
        "status",
        "success_count",
        "total_count",
        "tripped_at_tx",
    )

    def __init__(
        self, acquirer_id: str, priority: int, window_size: int, initial_timestamp: float
    ) -> None:
        self.acquirer_id = acquirer_id
        self.priority = priority
        self.status = RouteHealthStatus.HEALTHY
        self.consecutive_failures = 0
        self.tripped_at_tx: int | None = None
        self.success_count = 0
        self.failure_count = 0
        self.total_count = 0
        self.last_updated_at = initial_timestamp
        self.history: deque[float] = deque(maxlen=window_size)

    def to_snapshot(self) -> StaticRouteStateSnapshot:
        """Export immutable snapshot of this route's operational state."""
        return StaticRouteStateSnapshot(
            acquirer_id=self.acquirer_id,
            priority=self.priority,
            status=self.status,
            consecutive_failures=self.consecutive_failures,
            tripped_at_tx=self.tripped_at_tx,
            success_count=self.success_count,
            failure_count=self.failure_count,
            total_count=self.total_count,
            last_updated_at=self.last_updated_at,
        )


class StaticBreakerPolicy:
    """Active-passive priority list with a circuit breaker (Phase 6 baseline, unchanged).

    A failure is any payment that was not authorized, including issuer declines (the audit's
    F-01 strawman), and the cooldown is counted in transactions. When every route is
    tripped, traffic falls back to the primary.
    """

    name = "static"

    def __init__(
        self,
        priority_order: Sequence[str],
        failover_policy: FailoverPolicyConfig | None = None,
        initial_timestamp: float = 0.0,
    ) -> None:
        """Create HEALTHY route states in priority order."""
        self._priority_order: tuple[str, ...] = _check_ids(priority_order)
        self._policy = failover_policy if failover_policy is not None else FailoverPolicyConfig()
        self._route_states: dict[str, _MutableRouteState] = {
            aid: _MutableRouteState(
                acquirer_id=aid,
                priority=idx + 1,
                window_size=self._policy.window_size,
                initial_timestamp=initial_timestamp,
            )
            for idx, aid in enumerate(self._priority_order)
        }
        self._global_tx_counter = 0

    @property
    def acquirer_ids(self) -> tuple[str, ...]:
        """Acquirers in priority order."""
        return self._priority_order

    @property
    def max_attempts(self) -> int:
        """The static breaker never retries."""
        return 1

    @property
    def global_tx_counter(self) -> int:
        """Payments routed so far (the breaker's clock)."""
        return self._global_tx_counter

    @property
    def failover_policy(self) -> FailoverPolicyConfig:
        """Thresholds and cooldown."""
        return self._policy

    def _alloc(self, chosen: str) -> dict[str, float]:
        return {k: 1.0 if k == chosen else 0.0 for k in self._priority_order}

    def peek(self) -> tuple[str, dict[str, float]]:
        """The route the next payment would take, without changing any state."""
        cooldown_limit = self._policy.cooldown_transactions
        for aid in self._priority_order:
            state = self._route_states[aid]
            if state.status == RouteHealthStatus.TRIPPED and state.tripped_at_tx is not None:
                if (self._global_tx_counter - state.tripped_at_tx) >= cooldown_limit:
                    return aid, self._alloc(aid)
        for aid in self._priority_order:
            state = self._route_states[aid]
            if state.status in (RouteHealthStatus.HEALTHY, RouteHealthStatus.PROBATION):
                return aid, self._alloc(aid)
        primary_id = self._priority_order[0]
        return primary_id, self._alloc(primary_id)

    def _select(self) -> tuple[str, str]:
        """Evaluate route states (may move a route to PROBATION or HEALTHY); return id, reason."""
        cooldown_limit = self._policy.cooldown_transactions

        # 1. Check if any higher-priority TRIPPED route has completed cooldown
        for aid in self._priority_order:
            state = self._route_states[aid]
            if state.status == RouteHealthStatus.TRIPPED and state.tripped_at_tx is not None:
                elapsed = self._global_tx_counter - state.tripped_at_tx
                if elapsed >= cooldown_limit:
                    if self._policy.failback_mode == "snapback":
                        logger.info(
                            "Cooldown elapsed (%d >= %d) on %s; snapback to HEALTHY",
                            elapsed,
                            cooldown_limit,
                            aid,
                        )
                        state.status = RouteHealthStatus.HEALTHY
                        state.consecutive_failures = 0
                        state.tripped_at_tx = None
                        return aid, "snapback"
                    logger.info(
                        "Cooldown elapsed (%d >= %d) on %s; dispatching PROBATION probe",
                        elapsed,
                        cooldown_limit,
                        aid,
                    )
                    state.status = RouteHealthStatus.PROBATION
                    return aid, "probation_probe"

        # 2. Select first available route in priority order (PROBATION probe or HEALTHY)
        for aid in self._priority_order:
            state = self._route_states[aid]
            if state.status in (RouteHealthStatus.HEALTHY, RouteHealthStatus.PROBATION):
                return aid, "priority"

        # 3. Exhaustion Fallback: If all routes are TRIPPED, fall back to Primary with warning
        primary_id = self._priority_order[0]
        logger.warning(
            "All static routes are TRIPPED; activating exhaustion fallback to primary: %s",
            primary_id,
        )
        return primary_id, "exhaustion_fallback"

    def decide(
        self, ctx: DecisionContext, now: float, exclude: frozenset[str] = frozenset()
    ) -> Decision:
        """Advance the transaction counter and pick a route (may change breaker states)."""
        if exclude:
            chosen = _remaining(self._priority_order, exclude)[0]
            reason = "excluded_fallback"
        else:
            self._global_tx_counter += 1
            chosen, reason = self._select()
        return Decision(
            transaction_id=ctx.transaction_id,
            acquirer_id=chosen,
            propensities=self._alloc(chosen),
            reason=reason,
            decided_at=now,
            attempt=ctx.attempt,
        )

    def feedback(self, decision: Decision, observation: Observation, now: float) -> None:
        """Update counters and trip or restore the route (mutates breaker state)."""
        selected_id = decision.acquirer_id
        success = observation.authorized
        state = self._route_states[selected_id]
        state.total_count += 1
        state.last_updated_at = now
        state.history.append(1.0 if success else 0.0)

        if success:
            state.success_count += 1
            state.consecutive_failures = 0
            if state.status == RouteHealthStatus.PROBATION:
                logger.info(
                    "Canary probe succeeded on %s; promoting route to HEALTHY",
                    selected_id,
                )
                state.status = RouteHealthStatus.HEALTHY
                state.tripped_at_tx = None
            return

        state.failure_count += 1
        state.consecutive_failures += 1
        if state.status == RouteHealthStatus.PROBATION:
            logger.warning(
                "Canary probe failed on %s; re-tripping circuit breaker and resetting cooldown",
                selected_id,
            )
            state.status = RouteHealthStatus.TRIPPED
            state.tripped_at_tx = self._global_tx_counter
        elif state.status == RouteHealthStatus.HEALTHY:
            tripped = False
            policy = self._policy
            if policy.threshold_type == FailoverThresholdType.CONSECUTIVE_FAILURES:
                if state.consecutive_failures >= policy.consecutive_failure_threshold:
                    tripped = True
            elif policy.threshold_type == FailoverThresholdType.WINDOW_FAILURE_RATE:
                if len(state.history) >= policy.window_size:
                    err_rate = state.history.count(0.0) / len(state.history)
                    if err_rate >= policy.window_failure_rate_threshold:
                        tripped = True

            if tripped:
                logger.warning(
                    "Circuit breaker TRIPPED on %s (consecutive_failures=%d, total=%d); "
                    "failing over to next priority tier",
                    selected_id,
                    state.consecutive_failures,
                    state.total_count,
                )
                state.status = RouteHealthStatus.TRIPPED
                state.tripped_at_tx = self._global_tx_counter

    def should_retry(self, decision: Decision, observation: Observation) -> bool:
        """The static breaker never retries."""
        return False

    def route_state(self, acquirer_id: str) -> StaticRouteStateSnapshot:
        """Snapshot of one route."""
        if acquirer_id not in self._route_states:
            raise KeyError(f"Acquirer route {acquirer_id} not configured in baseline router")
        return self._route_states[acquirer_id].to_snapshot()

    def all_states(self) -> dict[str, StaticRouteStateSnapshot]:
        """Snapshots of every route in priority order."""
        return {aid: self._route_states[aid].to_snapshot() for aid in self._priority_order}


# ---------------------------------------------------------------------------------------------
# Competent breaker (audit's CompetentRouter, wall-clock half-open)
# ---------------------------------------------------------------------------------------------


class CompetentBreakerPolicy:
    """Approval-window routing with hysteresis, a technical-only breaker and one safe retry.

    Ported from the audit's throwaway ``CompetentRouter`` (docs/AUDIT.md appendix):

    * estimate per acquirer = (approvals + 9) / (attempts + 10) over its last ``window``
      attempts (a technical failure counts as a non-approval);
    * route to the best estimate, but keep the current preferred acquirer unless another
      beats it by ``margin``; with probability ``eps`` explore a random other eligible one;
    * the breaker opens after ``k`` consecutive technical failures (issuer declines never
      count); a 429 counts as technical, as in the audit's classifier;
    * after one safe technical failure (not processed), retry once on the best other
      eligible acquirer; never after a timeout or an ambiguous answer.

    Changes from the audit code (decisions log, Stage 0): the half-open timer runs on the
    wall clock (``cooldown_sec``, doubling after each failed probe up to ``max_cooldown_sec``)
    instead of a transaction count; while a probe is in flight nothing else is routed to
    that acquirer; when every acquirer is open, traffic goes to the least recently opened.
    """

    name = "competent"

    def __init__(
        self,
        acquirer_ids: Sequence[str],
        seed: int | np.random.SeedSequence | None = None,
        retry: bool = True,
        k: int = 3,
        cooldown_sec: float = 2.0,
        cooldown_backoff: float = 2.0,
        max_cooldown_sec: float = 30.0,
        window: int = 50,
        prior_approvals: float = 9.0,
        prior_attempts: float = 10.0,
        eps: float = 0.02,
        margin: float = 0.03,
    ) -> None:
        """Set up per-acquirer windows and breaker state; acquirer order is the priority."""
        if k < 1 or window < 1:
            raise ValueError("k and window must be >= 1")
        if not (0.0 <= eps < 1.0) or margin < 0.0:
            raise ValueError("eps must be in [0, 1) and margin >= 0")
        if cooldown_sec <= 0.0 or cooldown_backoff < 1.0 or max_cooldown_sec < cooldown_sec:
            raise ValueError("invalid cooldown settings")
        self._ids = _check_ids(acquirer_ids)
        self._rng = np.random.default_rng(seed)
        self._retry = retry
        self._k = k
        self._cooldown_sec = cooldown_sec
        self._backoff = cooldown_backoff
        self._max_cooldown = max_cooldown_sec
        self._prior_a = prior_approvals
        self._prior_n = prior_attempts
        self._eps = eps
        self._margin = margin
        self._win: dict[str, deque[float]] = {a: deque(maxlen=window) for a in self._ids}
        self._win_sum: dict[str, float] = {a: 0.0 for a in self._ids}
        self._consec_tech: dict[str, int] = {a: 0 for a in self._ids}
        self._open_at: dict[str, float | None] = {a: None for a in self._ids}
        self._cooldown: dict[str, float] = {a: cooldown_sec for a in self._ids}
        self._probe_in_flight: dict[str, bool] = {a: False for a in self._ids}
        self._preferred = self._ids[0]
        if not retry:
            self.name = "competent_noretry"

    @property
    def acquirer_ids(self) -> tuple[str, ...]:
        """Acquirers in priority order."""
        return self._ids

    @property
    def max_attempts(self) -> int:
        """Two attempts with retry enabled, else one."""
        return 2 if self._retry else 1

    def estimate(self, acquirer_id: str) -> float:
        """Smoothed approval rate over the acquirer's window."""
        win = self._win[acquirer_id]
        return (self._win_sum[acquirer_id] + self._prior_a) / (len(win) + self._prior_n)

    def is_open(self, acquirer_id: str) -> bool:
        """True while the breaker is open or half-open."""
        return self._open_at[acquirer_id] is not None

    def _rank_key(self, a: str) -> tuple[float, int]:
        return (self.estimate(a), -self._ids.index(a))

    def decide(
        self, ctx: DecisionContext, now: float, exclude: frozenset[str] = frozenset()
    ) -> Decision:
        """Probe a due half-open acquirer, else route by estimate (may move the preference)."""
        rest = _remaining(self._ids, exclude)
        if not exclude:
            for a in self._ids:
                opened = self._open_at[a]
                if (
                    opened is not None
                    and not self._probe_in_flight[a]
                    and now - opened >= self._cooldown[a]
                ):
                    self._probe_in_flight[a] = True
                    return self._decision(ctx, now, a, one_hot(self._ids, a), "half_open_probe")

        cands = [a for a in rest if not self.is_open(a)]
        if not cands:
            # Everything open: the acquirer opened longest ago has had the most time to heal.
            chosen = min(rest, key=lambda a: (self._open_at[a] or 0.0, self._ids.index(a)))
            return self._decision(ctx, now, chosen, one_hot(self._ids, chosen), "all_open")

        best = max(cands, key=self._rank_key)
        if self._preferred in cands and self.estimate(best) < (
            self.estimate(self._preferred) + self._margin
        ):
            best = self._preferred
        if exclude:
            return self._decision(ctx, now, best, one_hot(self._ids, best), "retry")

        self._preferred = best
        others = [a for a in cands if a != best]
        if not others or self._eps == 0.0:
            return self._decision(ctx, now, best, one_hot(self._ids, best), "preferred")
        props = {a: 0.0 for a in self._ids}
        props[best] = 1.0 - self._eps
        for a in others:
            props[a] = self._eps / len(others)
        if self._rng.random() < self._eps:
            chosen = others[int(self._rng.integers(len(others)))]
            return self._decision(ctx, now, chosen, props, "explore")
        return self._decision(ctx, now, best, props, "preferred")

    @staticmethod
    def _decision(
        ctx: DecisionContext, now: float, chosen: str, props: dict[str, float], reason: str
    ) -> Decision:
        return Decision(
            transaction_id=ctx.transaction_id,
            acquirer_id=chosen,
            propensities=props,
            reason=reason,
            decided_at=now,
            attempt=ctx.attempt,
        )

    def feedback(self, decision: Decision, observation: Observation, now: float) -> None:
        """Update the approval window and breaker of the attempted acquirer (mutates state)."""
        a = decision.acquirer_id
        probe = decision.reason == "half_open_probe"
        if probe:
            self._probe_in_flight[a] = False
        if observation.outcome is None:
            return
        win = self._win[a]
        if len(win) == win.maxlen:
            self._win_sum[a] -= win[0]
        x = 1.0 if observation.authorized else 0.0
        win.append(x)
        self._win_sum[a] += x

        if observation.outcome == Outcome.TECHNICAL_FAILURE:
            self._consec_tech[a] += 1
            if probe:
                self._open_at[a] = now
                self._cooldown[a] = min(self._cooldown[a] * self._backoff, self._max_cooldown)
            elif self._open_at[a] is None and self._consec_tech[a] >= self._k:
                self._open_at[a] = now
                self._cooldown[a] = self._cooldown_sec
        else:
            self._consec_tech[a] = 0
            if self._open_at[a] is not None:
                self._open_at[a] = None
                self._cooldown[a] = self._cooldown_sec

    def should_retry(self, decision: Decision, observation: Observation) -> bool:
        """Retry once, only after a failure that certainly was not processed."""
        return (
            self._retry
            and decision.attempt == 1
            and not observation.authorized
            and observation.safe_to_retry
            and len(self._ids) > 1
        )


# ---------------------------------------------------------------------------------------------
# Success-rate window (Juspay / Hyperswitch description)
# ---------------------------------------------------------------------------------------------


class SRWindowPolicy:
    """Bucketed sliding-window success-rate routing with exploration and elimination.

    Re-implements the approach Juspay/Hyperswitch describe publicly ("auth-rate based
    routing": a sliding window of payment-result buckets, a share of traffic sent to other
    eligible processors to keep scores fresh, an elimination threshold that deprioritises a
    poorly performing processor). The Juspay paper itself (arXiv 2510.16735) was not read,
    and the public docs give no values, so every parameter here is our own choice:

    * score = approvals / attempts over the acquirer's last ``max_buckets`` buckets of
      ``bucket_size`` attempts (count-based); an acquirer with fewer than
      ``min_buckets`` full buckets is unscored and ranked first so it gets tried;
    * an acquirer whose latest full bucket is below ``elimination_threshold`` is eliminated
      (ranked after every other acquirer) until a newer bucket is above it;
    * route to the best-ranked acquirer; with probability ``exploration`` send to a uniformly
      random other acquirer instead. No retries.
    """

    name = "sr_window"

    def __init__(
        self,
        acquirer_ids: Sequence[str],
        seed: int | np.random.SeedSequence | None = None,
        bucket_size: int = 20,
        max_buckets: int = 10,
        min_buckets: int = 1,
        exploration: float = 0.05,
        elimination_threshold: float = 0.5,
    ) -> None:
        """Create empty bucket windows; acquirer order breaks ties."""
        if bucket_size < 1 or max_buckets < 1 or not (1 <= min_buckets <= max_buckets):
            raise ValueError("invalid bucket settings")
        if not (0.0 <= exploration < 1.0) or not (0.0 <= elimination_threshold <= 1.0):
            raise ValueError("exploration must be in [0, 1), elimination in [0, 1]")
        self._ids = _check_ids(acquirer_ids)
        self._rng = np.random.default_rng(seed)
        self._bucket_size = bucket_size
        self._min_buckets = min_buckets
        self._exploration = exploration
        self._elimination = elimination_threshold
        # Full buckets as (approvals, attempts); the open bucket separately.
        self._full: dict[str, deque[tuple[int, int]]] = {
            a: deque(maxlen=max_buckets) for a in self._ids
        }
        self._open: dict[str, list[int]] = {a: [0, 0] for a in self._ids}

    @property
    def acquirer_ids(self) -> tuple[str, ...]:
        """Acquirers in configured order."""
        return self._ids

    @property
    def max_attempts(self) -> int:
        """No retries."""
        return 1

    def score(self, acquirer_id: str) -> float | None:
        """Window success rate, or None while fewer than ``min_buckets`` buckets are full."""
        full = self._full[acquirer_id]
        if len(full) < self._min_buckets:
            return None
        ok = sum(b[0] for b in full) + self._open[acquirer_id][0]
        n = sum(b[1] for b in full) + self._open[acquirer_id][1]
        return ok / n

    def eliminated(self, acquirer_id: str) -> bool:
        """True when the latest full bucket's success rate is below the threshold."""
        full = self._full[acquirer_id]
        if not full:
            return False
        ok, n = full[-1]
        return ok / n < self._elimination

    def _rank_key(self, a: str) -> tuple[int, float, int]:
        s = self.score(a)
        return (0 if self.eliminated(a) else 1, 2.0 if s is None else s, -self._ids.index(a))

    def decide(
        self, ctx: DecisionContext, now: float, exclude: frozenset[str] = frozenset()
    ) -> Decision:
        """Route to the best-ranked acquirer, or explore (draws from the policy's generator)."""
        rest = _remaining(self._ids, exclude)
        best = max(rest, key=self._rank_key)
        others = [a for a in rest if a != best]
        props = one_hot(self._ids, best)
        chosen, reason = best, "best_score"
        if others and self._exploration > 0.0:
            props[best] = 1.0 - self._exploration
            for a in others:
                props[a] = self._exploration / len(others)
            if self._rng.random() < self._exploration:
                chosen = others[int(self._rng.integers(len(others)))]
                reason = "explore"
        return Decision(
            transaction_id=ctx.transaction_id,
            acquirer_id=chosen,
            propensities=props,
            reason=reason,
            decided_at=now,
            attempt=ctx.attempt,
        )

    def feedback(self, decision: Decision, observation: Observation, now: float) -> None:
        """Add the attempt to the acquirer's open bucket, closing it when full (mutates)."""
        if observation.outcome is None:
            return
        bucket = self._open[decision.acquirer_id]
        bucket[0] += 1 if observation.authorized else 0
        bucket[1] += 1
        if bucket[1] >= self._bucket_size:
            self._full[decision.acquirer_id].append((bucket[0], bucket[1]))
            self._open[decision.acquirer_id] = [0, 0]

    def should_retry(self, decision: Decision, observation: Observation) -> bool:
        """No retries."""
        return False


# ---------------------------------------------------------------------------------------------
# Sliding-window UCB (Dream11)
# ---------------------------------------------------------------------------------------------


class SlidingWindowUCBPolicy:
    """Dream11's SW-UCB as printed in arXiv 2308.01028v2 (Sec. 3).

    index(g) = y_g + c1 * sqrt(1 / N_g), where y_g is the success rate of gateway g's last
    ``window`` attempts (W = 200 in their live test), N_g the attempts in that window, and
    the reward is 1 for an authorized payment, else 0 (the paper does not separate issuer
    declines from technical failures). The paper does not report c1; we use 1.0, untuned. An
    untried gateway (N = 0) is tried first; ties go to the configured order.

    As printed, an arm with a full window keeps a constant bonus, so between full windows the
    policy is greedy and does not re-try an arm that has lost the lead. No retries.
    """

    name = "sw_ucb"

    def __init__(self, acquirer_ids: Sequence[str], window: int = 200, c1: float = 1.0) -> None:
        """Create empty reward windows."""
        if window < 1 or c1 < 0.0:
            raise ValueError("window must be >= 1 and c1 >= 0")
        self._ids = _check_ids(acquirer_ids)
        self._c1 = c1
        self._win: dict[str, deque[float]] = {a: deque(maxlen=window) for a in self._ids}
        self._sum: dict[str, float] = {a: 0.0 for a in self._ids}

    @property
    def acquirer_ids(self) -> tuple[str, ...]:
        """Acquirers in configured order."""
        return self._ids

    @property
    def max_attempts(self) -> int:
        """No retries."""
        return 1

    def index(self, acquirer_id: str) -> float:
        """UCB index; infinite for an untried acquirer."""
        n = len(self._win[acquirer_id])
        if n == 0:
            return math.inf
        return self._sum[acquirer_id] / n + self._c1 * math.sqrt(1.0 / n)

    def decide(
        self, ctx: DecisionContext, now: float, exclude: frozenset[str] = frozenset()
    ) -> Decision:
        """Route to the highest index (no state change)."""
        rest = _remaining(self._ids, exclude)
        chosen = max(rest, key=lambda a: (self.index(a), -self._ids.index(a)))
        return Decision(
            transaction_id=ctx.transaction_id,
            acquirer_id=chosen,
            propensities=one_hot(self._ids, chosen),
            reason="ucb",
            decided_at=now,
            attempt=ctx.attempt,
        )

    def feedback(self, decision: Decision, observation: Observation, now: float) -> None:
        """Append the reward to the acquirer's window (mutates)."""
        if observation.outcome is None:
            return
        win = self._win[decision.acquirer_id]
        if len(win) == win.maxlen:
            self._sum[decision.acquirer_id] -= win[0]
        x = 1.0 if observation.authorized else 0.0
        win.append(x)
        self._sum[decision.acquirer_id] += x

    def should_retry(self, decision: Decision, observation: Observation) -> bool:
        """No retries."""
        return False
