"""A pure, time-dependent model of acquirers and issuers for discrete-event benchmarks.

Stage 0 of docs/001-router_enhancements.md. Everything here is off unless a scenario turns it
on: a ``World`` with only acquirer profiles behaves like the HTTP simulator (each payment is
authorized when its issuer draw falls below the acquirer's approval rate).

What a world can express:

* failure taxonomy: a fast 503, a 200 with the ``ACQUIRER_OUTAGE`` decline code, a 429, or a
  timeout (the acquirer hangs past the router's timeout);
* a background technical error rate per acquirer, and brownouts that add to it;
* capacity per acquirer, in concurrent requests: ``reject`` answers 429 above it; ``collapse``
  serves everyone but, at load ratio rho = in_flight / capacity > 1, fails a further
  min(1, rho - 1) of requests with 503 and multiplies latency by rho;
* issuer segments (share of traffic, approval multiplier) and an acquirer x segment
  approval multiplier;
* timelines: outages, flapping, approval shifts (gray failures), gradual approval ramps and
  common-mode issuer incidents;
* common random numbers: the issuer draw is keyed by ``(seed, transaction_id)`` and shared by
  every acquirer and attempt; technical and latency draws are keyed per acquirer.

The world never sees a router. A harness asks it how one attempt would go, given the time and
the number of requests already in flight at that acquirer.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal

from acquirer_sim.crn import ISSUER, SEGMENT, crn_uniform, latency_stream, technical_stream

DEFAULT_SEGMENT = "default"


class FailureMode(StrEnum):
    """How an acquirer that is down answers."""

    HTTP_503 = "HTTP_503"  # fast 503
    DECLINE_CODE = "DECLINE_CODE"  # HTTP 200, decline code ACQUIRER_OUTAGE
    HTTP_429 = "HTTP_429"  # throttled
    TIMEOUT = "TIMEOUT"  # no answer before the router's timeout; not processed


class ResponseKind(StrEnum):
    """What one attempt returned."""

    AUTHORIZED = "AUTHORIZED"
    ISSUER_DECLINE = "ISSUER_DECLINE"
    TECH_DECLINE = "TECH_DECLINE"  # HTTP 200 with a technical decline code
    HTTP_503 = "HTTP_503"
    HTTP_429 = "HTTP_429"
    TIMEOUT = "TIMEOUT"


_MODE_KIND = {
    FailureMode.HTTP_503: ResponseKind.HTTP_503,
    FailureMode.DECLINE_CODE: ResponseKind.TECH_DECLINE,
    FailureMode.HTTP_429: ResponseKind.HTTP_429,
    FailureMode.TIMEOUT: ResponseKind.TIMEOUT,
}


@dataclass(frozen=True, slots=True)
class AcquirerProfile:
    """Steady-state behaviour of one acquirer."""

    acquirer_id: str
    approval_rate: float = 0.95
    technical_error_rate: float = 0.0
    latency_ms: float = 250.0
    jitter_ms: float = 50.0
    fail_latency_ms: float = 50.0
    capacity: int | None = None
    overload: Literal["reject", "collapse"] = "reject"
    segment_multiplier: Mapping[str, float] = field(default_factory=dict)
    # Fees: fee_fixed per processed attempt, fee_rate x amount per authorization.
    fee_fixed: float = 0.0
    fee_rate: float = 0.0

    def __post_init__(self) -> None:
        """Validate rates, latencies and capacity."""
        if not self.acquirer_id:
            raise ValueError("acquirer_id must be non-empty")
        for name in ("approval_rate", "technical_error_rate"):
            v = getattr(self, name)
            if not (0.0 <= v <= 1.0):
                raise ValueError(f"{name} must be in [0, 1], got {v}")
        if self.latency_ms < 0 or self.jitter_ms < 0 or self.fail_latency_ms < 0:
            raise ValueError("latencies must be >= 0")
        if self.jitter_ms > self.latency_ms:
            raise ValueError("jitter_ms must not exceed latency_ms")
        if self.capacity is not None and self.capacity < 1:
            raise ValueError("capacity must be >= 1")


@dataclass(frozen=True, slots=True)
class Segment:
    """An issuer segment: its share of payments and its approval multiplier."""

    name: str
    share: float
    approval_multiplier: float = 1.0


@dataclass(frozen=True, slots=True)
class Outage:
    """Acquirer down in [start, end), answering per ``mode``."""

    acquirer_id: str
    start: float
    end: float
    mode: FailureMode = FailureMode.HTTP_503


@dataclass(frozen=True, slots=True)
class Flapping:
    """Acquirer alternates down ``down_sec`` / up ``up_sec`` in [start, end), down first."""

    acquirer_id: str
    start: float
    end: float
    down_sec: float
    up_sec: float
    mode: FailureMode = FailureMode.HTTP_503


@dataclass(frozen=True, slots=True)
class Brownout:
    """Extra technical (503) error rate on an acquirer in [start, end)."""

    acquirer_id: str
    start: float
    end: float
    error_rate: float


@dataclass(frozen=True, slots=True)
class ApprovalShift:
    """Acquirer's approval rate replaced by ``approval_rate`` in [start, end) (gray failure)."""

    acquirer_id: str
    start: float
    end: float
    approval_rate: float


@dataclass(frozen=True, slots=True)
class ApprovalRamp:
    """Approval falls linearly to ``to_rate`` over [start, ramp_end], holds until ``end``."""

    acquirer_id: str
    start: float
    ramp_end: float
    end: float
    to_rate: float


@dataclass(frozen=True, slots=True)
class IssuerIncident:
    """A segment's approval multiplied by ``multiplier`` on every acquirer in [start, end)."""

    segment: str
    start: float
    end: float
    multiplier: float


Event = Outage | Flapping | Brownout | ApprovalShift | ApprovalRamp | IssuerIncident


@dataclass(frozen=True, slots=True)
class WorldResponse:
    """The result of one attempt as the world decides it.

    ``latency_sec`` is ``inf`` when the acquirer never answers. ``processed`` is whether the
    acquirer handled the payment; ``issuer_approved`` whether the issuer approved it, which
    for a processed timeout is the outcome the router cannot see.
    """

    kind: ResponseKind
    latency_sec: float
    processed: bool
    issuer_approved: bool


def _active(event: Event, t: float) -> bool:
    return event.start <= t < event.end


class World:
    """Acquirer and issuer behaviour over time, with common random numbers."""

    def __init__(
        self,
        profiles: Sequence[AcquirerProfile],
        events: Sequence[Event] = (),
        segments: Sequence[Segment] = (),
        seed: int = 0,
        timeout_sec: float = 2.0,
    ) -> None:
        """Build a world; segment shares must sum to 1 when segments are given."""
        if not profiles:
            raise ValueError("at least one acquirer profile is required")
        self._profiles = {p.acquirer_id: p for p in profiles}
        if len(self._profiles) != len(profiles):
            raise ValueError("duplicate acquirer ids")
        self._segments = tuple(segments) or (Segment(DEFAULT_SEGMENT, 1.0),)
        if abs(sum(s.share for s in self._segments) - 1.0) > 1e-9:
            raise ValueError("segment shares must sum to 1")
        names = {s.name for s in self._segments}
        for event in events:
            if isinstance(event, IssuerIncident):
                if event.segment not in names:
                    raise ValueError(f"unknown segment {event.segment!r}")
            elif event.acquirer_id not in self._profiles:
                raise ValueError(f"unknown acquirer {event.acquirer_id!r}")
            if event.end <= event.start:
                raise ValueError(f"event ends before it starts: {event}")
        self._events = tuple(events)
        self._by_acquirer: dict[str, tuple[Event, ...]] = {
            aid: tuple(
                e
                for e in self._events
                if not isinstance(e, IssuerIncident) and e.acquirer_id == aid
            )
            for aid in self._profiles
        }
        self._incidents = tuple(e for e in self._events if isinstance(e, IssuerIncident))
        self._segment_by_name = {s.name: s for s in self._segments}
        self.seed = seed
        self.timeout_sec = timeout_sec

    @property
    def acquirer_ids(self) -> tuple[str, ...]:
        """Acquirers in profile order."""
        return tuple(self._profiles)

    @property
    def events(self) -> tuple[Event, ...]:
        """The timeline."""
        return self._events

    def profile(self, acquirer_id: str) -> AcquirerProfile:
        """Steady-state profile of an acquirer."""
        return self._profiles[acquirer_id]

    def segment_of(self, transaction_id: str) -> str:
        """The issuer segment of a payment (a common random number)."""
        if len(self._segments) == 1:
            return self._segments[0].name
        u = crn_uniform(self.seed, transaction_id, SEGMENT)
        acc = 0.0
        for seg in self._segments:
            acc += seg.share
            if u < acc:
                return seg.name
        return self._segments[-1].name

    def down_mode(self, acquirer_id: str, t: float) -> FailureMode | None:
        """How the acquirer fails at ``t`` if it is down, else None."""
        for e in self._by_acquirer[acquirer_id]:
            if isinstance(e, Outage) and _active(e, t):
                return e.mode
            if isinstance(e, Flapping) and _active(e, t):
                phase = (t - e.start) % (e.down_sec + e.up_sec)
                if phase < e.down_sec:
                    return e.mode
        return None

    def technical_error_rate(self, acquirer_id: str, t: float) -> float:
        """Background plus brownout 503 probability at ``t`` (ignores capacity)."""
        ok = 1.0 - self._profiles[acquirer_id].technical_error_rate
        for e in self._by_acquirer[acquirer_id]:
            if isinstance(e, Brownout) and _active(e, t):
                ok *= 1.0 - e.error_rate
        return 1.0 - ok

    def approval_rate(self, acquirer_id: str, segment: str, t: float) -> float:
        """Probability the issuer approves a processed payment at ``t``."""
        p = self._profiles[acquirer_id]
        rate = p.approval_rate
        for e in self._by_acquirer[acquirer_id]:
            if isinstance(e, ApprovalShift) and _active(e, t):
                rate = e.approval_rate
            elif isinstance(e, ApprovalRamp) and _active(e, t):
                span = e.ramp_end - e.start
                frac = 1.0 if span <= 0 else min(1.0, (t - e.start) / span)
                rate = p.approval_rate + frac * (e.to_rate - p.approval_rate)
        rate *= self._segment_by_name[segment].approval_multiplier
        rate *= p.segment_multiplier.get(segment, 1.0)
        for inc in self._incidents:
            if inc.segment == segment and _active(inc, t):
                rate *= inc.multiplier
        return min(1.0, max(0.0, rate))

    def expected_success(self, acquirer_id: str, segment: str, t: float) -> float:
        """P(authorized) for one attempt at ``t``, ignoring capacity and latency timeouts."""
        if self.down_mode(acquirer_id, t) is not None:
            return 0.0
        return (1.0 - self.technical_error_rate(acquirer_id, t)) * self.approval_rate(
            acquirer_id, segment, t
        )

    def respond(
        self, transaction_id: str, acquirer_id: str, t: float, in_flight: int = 0
    ) -> WorldResponse:
        """Decide one attempt at ``t`` with ``in_flight`` requests already at that acquirer."""
        p = self._profiles[acquirer_id]
        mode = self.down_mode(acquirer_id, t)
        if mode is not None:
            if mode == FailureMode.TIMEOUT:
                return WorldResponse(ResponseKind.TIMEOUT, math.inf, False, False)
            return WorldResponse(_MODE_KIND[mode], p.fail_latency_ms / 1000.0, False, False)

        load = 1.0
        overload_error = 0.0
        if p.capacity is not None:
            if p.overload == "reject":
                if in_flight >= p.capacity:
                    return WorldResponse(
                        ResponseKind.HTTP_429, p.fail_latency_ms / 1000.0, False, False
                    )
            else:
                load = max(1.0, (in_flight + 1) / p.capacity)
                overload_error = min(1.0, load - 1.0)

        tech = 1.0 - (1.0 - self.technical_error_rate(acquirer_id, t)) * (1.0 - overload_error)
        if tech > 0.0 and crn_uniform(self.seed, transaction_id, technical_stream(acquirer_id)) < (
            tech
        ):
            return WorldResponse(
                ResponseKind.HTTP_503, load * p.fail_latency_ms / 1000.0, False, False
            )

        latency_ms = p.latency_ms
        if p.jitter_ms > 0.0:
            u = crn_uniform(self.seed, transaction_id, latency_stream(acquirer_id))
            latency_ms += p.jitter_ms * (2.0 * u - 1.0)
        latency = load * latency_ms / 1000.0

        segment = self.segment_of(transaction_id)
        approved = crn_uniform(self.seed, transaction_id, ISSUER) < self.approval_rate(
            acquirer_id, segment, t
        )
        if latency > self.timeout_sec:
            # Processed, but the router stops waiting: it cannot tell what the issuer said.
            return WorldResponse(ResponseKind.TIMEOUT, latency, True, approved)
        kind = ResponseKind.AUTHORIZED if approved else ResponseKind.ISSUER_DECLINE
        return WorldResponse(kind, latency, True, approved)
