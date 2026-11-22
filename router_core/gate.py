"""Technical gate: per-acquirer CUSUM breaker with wall-clock half-open probes (Stage 1).

Each acquirer is CLOSED (eligible), OPEN (excluded) or HALF_OPEN (one probe in flight). A
Bernoulli CUSUM on technical outcomes opens the gate: every technical failure adds
ln(p1/p0), every processed answer (approved or issuer-declined) adds ln((1-p1)/(1-p0)), the
sum is floored at 0 and the gate opens when it reaches ``threshold``. With the defaults
(p0 = 2%, p1 = 50%, threshold 6) two failures in a row open it, and a 40% brownout opens it
after about seven attempts. Issuer declines are evidence that the acquirer works.

An attempt still in flight long after the acquirer usually answers counts as a failure
before its timeout fires ("overdue": older than ``overdue_latency_multiple`` times the
acquirer's recent p95 latency). While OPEN the acquirer gets no traffic except one probe at a
time, after a wall-clock backoff that starts at ``probe_backoff_initial_sec`` and doubles
after each failed probe, up to ``probe_backoff_safe_max_sec`` when the probe failed safely
(the cascade retried it, so it cost a hop) and ``probe_backoff_max_sec`` when it lost its
payment (a timeout); so the cost of an outage depends on its duration, not on TPS. A
successful probe closes the gate. When every acquirer is open, the acquirers that processed
a payment most recently stay in the allocation.

The gate is sync and I/O-free; time is always passed in.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from router_core.state import Outcome


class GateState(StrEnum):
    """Gate state of one acquirer."""

    CLOSED = "CLOSED"
    OPEN = "OPEN"
    HALF_OPEN = "HALF_OPEN"


class GateConfig(BaseModel):
    """Settings of the technical gate (fixed in the Stage 1 decisions-log entry)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    baseline_failure_rate: float = Field(
        default=0.02, gt=0.0, lt=1.0, description="p0: technical failure rate of a healthy route."
    )
    outage_failure_rate: float = Field(
        default=0.5, gt=0.0, lt=1.0, description="p1: failure rate the CUSUM is tuned to detect."
    )
    threshold: float = Field(default=6.0, gt=0.0, description="CUSUM level that opens the gate.")
    count_throttled: bool = Field(
        default=False,
        description=(
            "Whether a 429 counts as gate evidence. Off: a 429 means busy, not broken; the "
            "cascade moves the payment on and capacity limits are left to Stage 3."
        ),
    )
    probe_backoff_initial_sec: float = Field(default=1.0, gt=0.0)
    probe_backoff_factor: float = Field(default=2.0, ge=1.0)
    probe_backoff_max_sec: float = Field(
        default=30.0, gt=0.0, description="Backoff cap after a probe that lost its payment."
    )
    probe_backoff_safe_max_sec: float = Field(
        default=2.0,
        gt=0.0,
        description=(
            "Backoff cap after a probe that failed safely (not processed, so the cascade "
            "retried it): such a probe costs one extra hop, not a payment."
        ),
    )
    all_open_window_sec: float = Field(
        default=2.0,
        ge=0.0,
        description=(
            "When every gate is open, acquirers that processed a payment within this many "
            "seconds of the most recent one stay in the allocation."
        ),
    )
    overdue_latency_multiple: float = Field(
        default=3.0, gt=0.0, description="k: in flight longer than k x p95 latency is overdue."
    )
    overdue_min_sec: float = Field(default=0.25, ge=0.0)
    latency_window: int = Field(default=100, ge=1, description="Answers kept for the p95.")
    min_latency_samples: int = Field(
        default=10,
        ge=1,
        description="Below this many answers, nothing is overdue before 90% of the timeout.",
    )
    probe_max_shrinkage: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description=(
            "With value scaling on, only payments whose shrinkage lambda(V) is at most this "
            "may be used as probes."
        ),
    )

    @model_validator(mode="after")
    def _check(self) -> GateConfig:
        if self.outage_failure_rate <= self.baseline_failure_rate:
            raise ValueError("outage_failure_rate must exceed baseline_failure_rate")
        if self.probe_backoff_max_sec < self.probe_backoff_initial_sec:
            raise ValueError("probe_backoff_max_sec must be >= probe_backoff_initial_sec")
        return self

    @property
    def failure_step(self) -> float:
        """CUSUM increment of a technical failure."""
        return math.log(self.outage_failure_rate / self.baseline_failure_rate)

    @property
    def success_step(self) -> float:
        """CUSUM increment (negative) of a processed answer."""
        return math.log((1.0 - self.outage_failure_rate) / (1.0 - self.baseline_failure_rate))


@dataclass(slots=True)
class _Pending:
    acquirer_id: str
    decided_at: float
    probe: bool
    counted: bool = False


class _Route:
    __slots__ = (
        "backoff",
        "cusum",
        "last_ok",
        "latencies",
        "opened_at",
        "p95",
        "probe_in_flight",
        "since_p95",
        "state",
        "trips",
    )

    def __init__(self, config: GateConfig) -> None:
        self.state = GateState.CLOSED
        self.cusum = 0.0
        self.opened_at: float | None = None
        self.last_ok: float | None = None
        self.backoff = config.probe_backoff_initial_sec
        self.probe_in_flight = False
        self.latencies: deque[float] = deque(maxlen=config.latency_window)
        self.p95: float | None = None
        self.since_p95 = 0
        self.trips = 0


class TechnicalGate:
    """Per-acquirer CUSUM gate with probes; all methods take the current time."""

    def __init__(
        self,
        acquirer_ids: Sequence[str],
        config: GateConfig,
        timeouts: Mapping[str, float],
    ) -> None:
        """Start every acquirer CLOSED; ``timeouts`` are the router's per-acquirer timeouts."""
        self._ids = tuple(acquirer_ids)
        self._config = config
        self._timeouts = {aid: float(timeouts[aid]) for aid in self._ids}
        self._routes = {aid: _Route(config) for aid in self._ids}
        # Attempts dispatched and not yet answered, in decision order.
        self._pending: dict[tuple[str, int], _Pending] = {}
        self._overdue_queue: dict[str, deque[tuple[str, int]]] = {a: deque() for a in self._ids}
        self._fail = config.failure_step
        self._ok = config.success_step

    @property
    def config(self) -> GateConfig:
        """The gate's settings."""
        return self._config

    def state(self, acquirer_id: str) -> GateState:
        """Current state of one acquirer."""
        return self._routes[acquirer_id].state

    def is_closed(self, acquirer_id: str) -> bool:
        """True when the acquirer may receive ordinary traffic."""
        return self._routes[acquirer_id].state == GateState.CLOSED

    def blocked(self) -> frozenset[str]:
        """Acquirers that may not receive ordinary traffic (OPEN or HALF_OPEN)."""
        return frozenset(a for a in self._ids if self._routes[a].state != GateState.CLOSED)

    def overdue_after(self, acquirer_id: str) -> float:
        """Seconds after which an unanswered attempt at this acquirer counts as a failure."""
        route = self._routes[acquirer_id]
        cap = 0.9 * self._timeouts[acquirer_id]
        if route.p95 is None or len(route.latencies) < self._config.min_latency_samples:
            return cap
        k_p95 = self._config.overdue_latency_multiple * route.p95
        return min(cap, max(self._config.overdue_min_sec, k_p95))

    def due_probe(self, now: float) -> str | None:
        """The OPEN acquirer whose probe is due, opened longest ago first (no state change)."""
        due = [
            a
            for a in self._ids
            if self._routes[a].state == GateState.OPEN
            and now - (self._routes[a].opened_at or 0.0) >= self._routes[a].backoff
        ]
        if not due:
            return None
        return min(due, key=lambda a: (self._routes[a].opened_at or 0.0, self._ids.index(a)))

    def fallback(self, exclude: frozenset[str] = frozenset()) -> str:
        """Where to send a payment when no acquirer is eligible (no state change).

        Among acquirers not excluded: the one that most recently processed a payment, then
        the one whose gate opened longest ago. A hard-down acquirer stops processing, so it
        is never preferred over one that is merely busy or flaky.
        """
        rest = [a for a in self._ids if a not in exclude]
        if not rest:
            raise ValueError(f"every acquirer is excluded: {sorted(exclude)}")

        def key(a: str) -> tuple[float, float, int]:
            r = self._routes[a]
            last_ok = r.last_ok if r.last_ok is not None else -math.inf
            opened = r.opened_at if r.opened_at is not None else -math.inf
            return (-last_ok, opened, self._ids.index(a))

        return min(rest, key=key)

    def recently_working(self) -> frozenset[str]:
        """For when every gate is open: acquirers that processed a payment recently.

        Those within ``all_open_window_sec`` of the most recent processed answer; if none
        ever processed one, the single ``fallback()`` acquirer.
        """
        last = {a: r.last_ok for a, r in self._routes.items() if r.last_ok is not None}
        if not last:
            return frozenset({self.fallback()})
        newest = max(last.values())
        window = self._config.all_open_window_sec
        return frozenset(a for a, t in last.items() if t >= newest - window)

    def record_dispatch(
        self, transaction_id: str, attempt: int, acquirer_id: str, now: float, probe: bool
    ) -> None:
        """Track a dispatched attempt (mutates); a probe moves its acquirer to HALF_OPEN."""
        key = (transaction_id, attempt)
        self._pending[key] = _Pending(acquirer_id, now, probe)
        self._overdue_queue[acquirer_id].append(key)
        if probe:
            route = self._routes[acquirer_id]
            route.state = GateState.HALF_OPEN
            route.probe_in_flight = True

    def scan_overdue(self, now: float) -> None:
        """Count overdue attempts at CLOSED acquirers as failures (may open gates; mutates)."""
        for aid, queue in self._overdue_queue.items():
            limit = self.overdue_after(aid)
            while queue:
                key = queue[0]
                pending = self._pending.get(key)
                if pending is None:
                    queue.popleft()
                    continue
                if now - pending.decided_at < limit:
                    break
                queue.popleft()
                route = self._routes[aid]
                if route.state == GateState.CLOSED and not pending.probe:
                    pending.counted = True
                    self._add_evidence(aid, failed=True, now=now)
        # Drop attempts that never got an answer (the caller lost them), so memory is bounded.
        while self._pending:
            key = next(iter(self._pending))
            pending = self._pending[key]
            if now - pending.decided_at <= self._timeouts[pending.acquirer_id] + 1.0:
                break
            del self._pending[key]
            if pending.probe:
                self._probe_failed(pending.acquirer_id, now, safe=False)

    def record_result(
        self,
        transaction_id: str,
        attempt: int,
        outcome: Outcome | None,
        now: float,
        throttled: bool = False,
        safe: bool = False,
    ) -> None:
        """Apply an attempt's answer to its acquirer's gate (mutates).

        ``outcome`` None (never sent) is no evidence; nor is a 429 unless ``count_throttled``.
        ``safe``: a failure that certainly was not processed (sets a failed probe's backoff).
        """
        pending = self._pending.pop((transaction_id, attempt), None)
        if pending is None:
            return
        aid = pending.acquirer_id
        route = self._routes[aid]
        evidence: bool | None  # True: failed, False: processed, None: no evidence
        if outcome is None or (throttled and not self._config.count_throttled):
            evidence = None
        else:
            evidence = outcome == Outcome.TECHNICAL_FAILURE
        if evidence is False:
            route.last_ok = now if route.last_ok is None else max(route.last_ok, now)
            route.latencies.append(max(0.0, now - pending.decided_at))
            route.since_p95 += 1
            if route.p95 is None or route.since_p95 >= 10:
                ordered = sorted(route.latencies)
                route.p95 = ordered[min(len(ordered) - 1, int(0.95 * len(ordered)))]
                route.since_p95 = 0

        if pending.probe and route.state == GateState.HALF_OPEN:
            route.probe_in_flight = False
            if evidence is None:
                route.state = GateState.OPEN  # try again at the next payment
            elif evidence:
                self._probe_failed(aid, now, safe=safe)
            else:
                self._close(aid)
            return
        if pending.probe:
            route.probe_in_flight = False
        if evidence is None:
            return
        if route.state == GateState.CLOSED:
            if evidence and pending.counted:
                return  # already counted when it became overdue
            self._add_evidence(aid, failed=evidence, now=now)
        elif not evidence and route.opened_at is not None and pending.decided_at >= route.opened_at:
            # Traffic sent after the gate opened (all-open fallback) got through.
            self._close(aid)

    def _add_evidence(self, aid: str, failed: bool, now: float) -> None:
        route = self._routes[aid]
        route.cusum = max(0.0, route.cusum + (self._fail if failed else self._ok))
        if route.cusum >= self._config.threshold:
            route.state = GateState.OPEN
            route.opened_at = now
            route.backoff = self._config.probe_backoff_initial_sec
            route.cusum = 0.0
            route.trips += 1

    def _probe_failed(self, aid: str, now: float, safe: bool) -> None:
        route = self._routes[aid]
        route.state = GateState.OPEN
        route.probe_in_flight = False
        route.opened_at = now
        cfg = self._config
        cap = cfg.probe_backoff_safe_max_sec if safe else cfg.probe_backoff_max_sec
        route.backoff = min(route.backoff * cfg.probe_backoff_factor, cap)

    def _close(self, aid: str) -> None:
        route = self._routes[aid]
        route.state = GateState.CLOSED
        route.cusum = 0.0
        route.opened_at = None
        route.backoff = self._config.probe_backoff_initial_sec
        route.probe_in_flight = False

    def status(self, now: float) -> dict[str, dict[str, Any]]:
        """Per-acquirer gate state for /health."""
        out: dict[str, dict[str, Any]] = {}
        for aid in self._ids:
            r = self._routes[aid]
            out[aid] = {
                "state": r.state.value,
                "cusum": round(r.cusum, 4),
                "threshold": self._config.threshold,
                "opened_sec_ago": None if r.opened_at is None else round(now - r.opened_at, 3),
                "probe_backoff_sec": r.backoff,
                "probe_in_flight": r.probe_in_flight,
                "trips": r.trips,
                "p95_latency_ms": None if r.p95 is None else round(r.p95 * 1000.0, 1),
                "overdue_after_ms": round(self.overdue_after(aid) * 1000.0, 1),
            }
        return out
