"""Discrete-event simulation of payment traffic through a sync routing policy.

Virtual time only: arrivals come from a seeded process, each attempt's answer and latency come
from an ``acquirer_sim.world.World``, and the router's timeout cuts long attempts short. The
policy decides when a payment arrives and learns when its attempt completes, so decisions made
while earlier attempts are in flight do not see their outcomes (AUDIT F-16). A retry, when the
policy asks for one, is decided at the moment the first attempt completes.
"""

from __future__ import annotations

import heapq
import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

import numpy as np

from acquirer_sim.world import ResponseKind, World, WorldResponse
from router_core.policy import (
    Decision,
    DecisionContext,
    FailureKind,
    Observation,
    RoutingPolicy,
)
from router_core.state import Outcome


@dataclass(frozen=True, slots=True)
class Arrivals:
    """Arrival process: constant spacing, Poisson, or Poisson with periodic bursts.

    ``bursty``: rate ``tps`` normally, ``burst_tps`` for ``burst_on_sec`` at the end of every
    ``burst_on_sec + burst_off_sec`` period.
    """

    kind: Literal["constant", "poisson", "bursty"] = "poisson"
    tps: float = 15.0
    burst_tps: float = 0.0
    burst_on_sec: float = 0.0
    burst_off_sec: float = 0.0

    def __post_init__(self) -> None:
        """Validate rates and burst shape."""
        if self.tps <= 0.0:
            raise ValueError("tps must be > 0")
        if self.kind == "bursty" and (
            self.burst_tps <= 0.0 or self.burst_on_sec <= 0.0 or self.burst_off_sec <= 0.0
        ):
            raise ValueError("bursty arrivals need burst_tps, burst_on_sec and burst_off_sec")

    @property
    def mean_tps(self) -> float:
        """Long-run average arrival rate."""
        if self.kind != "bursty":
            return self.tps
        period = self.burst_on_sec + self.burst_off_sec
        return (self.tps * self.burst_off_sec + self.burst_tps * self.burst_on_sec) / period

    def times(self, horizon_sec: float, rng: np.random.Generator) -> list[float]:
        """Arrival times in (0, horizon_sec), sorted."""
        if self.kind == "constant":
            n = math.ceil(horizon_sec * self.tps)
            return [i / self.tps for i in range(1, n) if i / self.tps < horizon_sec]
        if self.kind == "poisson":
            return _poisson(self.tps, 0.0, horizon_sec, rng)
        out: list[float] = []
        period = self.burst_on_sec + self.burst_off_sec
        start = 0.0
        while start < horizon_sec:
            calm_end = min(start + self.burst_off_sec, horizon_sec)
            out.extend(_poisson(self.tps, start, calm_end, rng))
            burst_end = min(start + period, horizon_sec)
            if calm_end < burst_end:
                out.extend(_poisson(self.burst_tps, calm_end, burst_end, rng))
            start += period
        return out


def _poisson(rate: float, start: float, end: float, rng: np.random.Generator) -> list[float]:
    """Poisson arrival times in [start, end)."""
    out: list[float] = []
    t = start
    # Draw gaps in blocks for speed; the sequence is the same as one-at-a-time draws.
    block = max(16, int((end - start) * rate * 1.1) + 16)
    while True:
        for gap in rng.exponential(1.0 / rate, size=block):
            t += float(gap)
            if t >= end:
                return out
            out.append(t)


def observe(resp: WorldResponse) -> Observation:
    """What the router sees of a world answer, classified as the HTTP router does."""
    kind = resp.kind
    if kind == ResponseKind.AUTHORIZED:
        return Observation(Outcome.AUTHORIZED, latency_sec=resp.latency_sec)
    if kind == ResponseKind.ISSUER_DECLINE:
        return Observation(Outcome.ISSUER_DECLINE, latency_sec=resp.latency_sec)
    if kind == ResponseKind.HTTP_429:
        return Observation(Outcome.TECHNICAL_FAILURE, FailureKind.THROTTLED, resp.latency_sec)
    if kind == ResponseKind.TIMEOUT:
        return Observation(Outcome.TECHNICAL_FAILURE, FailureKind.TIMEOUT, resp.latency_sec)
    # HTTP 503 or a 200 with a technical decline code: not processed
    return Observation(Outcome.TECHNICAL_FAILURE, FailureKind.FAST_FAIL, resp.latency_sec)


@dataclass(slots=True)
class Attempt:
    """One dispatch of a payment to an acquirer."""

    acquirer_id: str
    decided_at: float
    completed_at: float
    kind: ResponseKind
    processed: bool
    issuer_approved: bool
    reason: str


@dataclass(slots=True)
class Payment:
    """A payment and every attempt made for it."""

    index: int
    transaction_id: str
    arrival: float
    amount: float
    attempts: list[Attempt] = field(default_factory=list)
    authorized: bool = False
    completed_at: float = math.nan


@dataclass(slots=True)
class RunResult:
    """Everything one policy did on one scenario run."""

    policy: str
    payments: list[Payment]
    ambiguous_retries: int = 0
    duplicate_risk_retries: int = 0
    decide_seconds: list[float] = field(default_factory=list)


# Event ordering at equal times: completions before arrivals, so a zero-latency answer is
# learned before the next payment is routed (as in a serial HTTP run).
_COMPLETION = 0
_ARRIVAL = 1


def simulate(
    world: World,
    policy: RoutingPolicy,
    arrival_times: list[float],
    amount: float | Callable[[int], float] = 40.0,
    tx_prefix: str = "tx",
    timing: bool = False,
) -> RunResult:
    """Run every arrival through ``policy`` against ``world``; return per-payment records.

    Mutates ``policy`` (its state learns from the run). With ``timing``, records the wall
    time of every ``decide`` call (machine-dependent; off for reproducible output).
    """
    timeout = world.timeout_sec
    in_flight = dict.fromkeys(world.acquirer_ids, 0)
    payments: list[Payment] = []
    result = RunResult(policy=policy.name, payments=payments)
    heap: list[tuple[float, int, int, Payment, Decision, WorldResponse]] = []
    seq = 0

    def dispatch(payment: Payment, now: float, exclude: frozenset[str]) -> None:
        nonlocal seq
        ctx = DecisionContext(
            transaction_id=payment.transaction_id,
            amount=payment.amount,
            attempt=len(payment.attempts) + 1,
        )
        if timing:
            t0 = time.perf_counter()
            decision = policy.decide(ctx, now, exclude)
            result.decide_seconds.append(time.perf_counter() - t0)
        else:
            decision = policy.decide(ctx, now, exclude)
        acquirer = decision.acquirer_id
        if acquirer in exclude:
            raise RuntimeError(f"{policy.name} chose excluded acquirer {acquirer}")
        resp = world.respond(payment.transaction_id, acquirer, now, in_flight[acquirer])
        in_flight[acquirer] += 1
        done = now + min(resp.latency_sec, timeout)
        seq += 1
        heapq.heappush(heap, (done, _COMPLETION, seq, payment, decision, resp))

    def complete(now: float, payment: Payment, decision: Decision, resp: WorldResponse) -> None:
        acquirer = decision.acquirer_id
        in_flight[acquirer] -= 1
        obs = observe(resp)
        policy.feedback(decision, obs, now)
        payment.attempts.append(
            Attempt(
                acquirer_id=acquirer,
                decided_at=decision.decided_at,
                completed_at=now,
                kind=resp.kind,
                processed=resp.processed,
                issuer_approved=resp.issuer_approved,
                reason=decision.reason,
            )
        )
        if obs.authorized:
            payment.authorized = True
            payment.completed_at = now
            return
        tried = frozenset(a.acquirer_id for a in payment.attempts)
        if (
            len(payment.attempts) < policy.max_attempts
            and len(tried) < len(in_flight)
            and policy.should_retry(decision, obs)
        ):
            if not obs.safe_to_retry:
                result.ambiguous_retries += 1
                if resp.processed and resp.issuer_approved:
                    result.duplicate_risk_retries += 1
            dispatch(payment, now, tried)
            return
        payment.completed_at = now

    for index, t in enumerate(arrival_times):
        while heap and heap[0][0] <= t:
            done, _, _, p, d, r = heapq.heappop(heap)
            complete(done, p, d, r)
        amt = amount(index) if callable(amount) else amount
        payment = Payment(index=index, transaction_id=f"{tx_prefix}{index}", arrival=t, amount=amt)
        payments.append(payment)
        dispatch(payment, t, frozenset())
    while heap:
        done, _, _, p, d, r = heapq.heappop(heap)
        complete(done, p, d, r)
    return result
