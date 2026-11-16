"""The Stage 0 scenario suite, pre-registered before any policy is tuned.

Registered in the decisions log ([Audit F-01, F-15, F-30, Stage 0]) and committed before the
first result. Changing a scenario after results exist needs a new decisions-log entry, and
the old definition stays reproducible from git history.

Defaults unless a scenario says otherwise: primary ``acquirer_alpha`` approves 95%, secondary
``acquirer_beta`` 94% (the pair ``compare_psr.py`` uses), latency 250 +/- 50 ms, fast
failures answer in 50 ms, router timeout 2 s, Poisson arrivals at 15 TPS, every payment $40.
"""

from __future__ import annotations

import zlib
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np

from acquirer_sim.world import (
    AcquirerProfile,
    ApprovalRamp,
    ApprovalShift,
    Brownout,
    Event,
    FailureMode,
    Flapping,
    IssuerIncident,
    Outage,
    Segment,
    World,
)
from scripts.suite.des import Arrivals

ALPHA = "acquirer_alpha"
BETA = "acquirer_beta"
GAMMA = "acquirer_gamma"
AMOUNT = 40.0

# Seeds: train run k uses seed k; test run k uses TEST_SEED_BASE + k.
TEST_SEED_BASE = 1_000_000


@dataclass(frozen=True, slots=True)
class Window:
    """A named time window [start, end); ``acquirer`` is the one affected, if any."""

    start: float
    end: float
    acquirer: str | None = None


@dataclass(frozen=True, slots=True)
class Component:
    """A revenue-model component of a soak: windows where it is measured, annual hours."""

    name: str
    windows: tuple[Window, ...]
    incident_seconds: float
    annual_hours: float


@dataclass(frozen=True, slots=True)
class Scenario:
    """One benchmark scenario: acquirers, timeline, traffic and what to measure."""

    name: str
    group: str
    description: str
    profiles: tuple[AcquirerProfile, ...]
    horizon_sec: float
    arrivals: Arrivals = field(default_factory=Arrivals)
    events: tuple[Event, ...] = ()
    segments: tuple[Segment, ...] = ()
    # Priority order (primary first) for priority-based policies; also Loom's route order.
    priority: tuple[str, ...] = ()
    # Single incident window, for divert / restore / share-shift metrics.
    incident: Window | None = None
    timeout_sec: float = 2.0
    components: tuple[Component, ...] = ()

    def __post_init__(self) -> None:
        """Default the priority to profile order and check it names every acquirer."""
        ids = tuple(p.acquirer_id for p in self.profiles)
        if not self.priority:
            object.__setattr__(self, "priority", ids)
        if sorted(self.priority) != sorted(ids):
            raise ValueError(f"{self.name}: priority must list every acquirer once")

    def world_seed(self, run_seed: int) -> int:
        """World seed for a run: depends on the run seed and the scenario name."""
        return _derive(run_seed, self.name, 0)

    def policy_seed(self, run_seed: int) -> int:
        """Seed every policy of this run receives."""
        return _derive(run_seed, self.name, 1)

    def arrival_times(self, run_seed: int) -> list[float]:
        """Arrival times of this run (shared by every policy)."""
        rng = np.random.default_rng(np.random.SeedSequence([run_seed, _crc(self.name), 2]))
        return self.arrivals.times(self.horizon_sec, rng)

    def build_world(self, run_seed: int) -> World:
        """The world of this run."""
        return World(
            profiles=self.profiles,
            events=self.events,
            segments=self.segments,
            seed=self.world_seed(run_seed),
            timeout_sec=self.timeout_sec,
        )


def _crc(name: str) -> int:
    return zlib.crc32(name.encode())


def _derive(run_seed: int, name: str, purpose: int) -> int:
    state = np.random.SeedSequence([run_seed, _crc(name), purpose]).generate_state(1)
    return int(state[0])


def run_seeds(split: str, n: int) -> list[int]:
    """The first ``n`` seeds of the ``train`` or ``test`` split."""
    if split == "train":
        return list(range(n))
    if split == "test":
        return [TEST_SEED_BASE + k for k in range(n)]
    raise ValueError(f"unknown split {split!r}")


def _pair(
    alpha: float = 0.95,
    beta: float = 0.94,
    latency_ms: float = 250.0,
    fail_latency_ms: float = 50.0,
) -> tuple[AcquirerProfile, ...]:
    return (
        AcquirerProfile(
            ALPHA, approval_rate=alpha, latency_ms=latency_ms, fail_latency_ms=fail_latency_ms
        ),
        AcquirerProfile(
            BETA, approval_rate=beta, latency_ms=latency_ms, fail_latency_ms=fail_latency_ms
        ),
    )


# Incident-scenario shape: 10 min at 15 TPS, the primary affected from minute 3 to minute 6.
_H = 600.0
_S, _E = 180.0, 360.0
_INC = Window(_S, _E, ALPHA)


def _outage_group() -> list[Scenario]:
    return [
        Scenario(
            "hard_outage_fast_fail",
            "outage",
            "Primary answers 503 in 50 ms for 3 min.",
            _pair(),
            _H,
            events=(Outage(ALPHA, _S, _E, FailureMode.HTTP_503),),
            incident=_INC,
        ),
        Scenario(
            "hard_outage_timeout",
            "outage",
            "Primary hangs past the 2 s timeout for 3 min (no safe retry).",
            _pair(),
            _H,
            events=(Outage(ALPHA, _S, _E, FailureMode.TIMEOUT),),
            incident=_INC,
        ),
        Scenario(
            "technical_brownout",
            "outage",
            "40% of the primary's requests fail with a fast 503 for 3 min.",
            _pair(),
            _H,
            events=(Brownout(ALPHA, _S, _E, 0.40),),
            incident=_INC,
        ),
        Scenario(
            "approval_gray_failure",
            "outage",
            "Primary processes everything but approves only 60% for 3 min.",
            _pair(),
            _H,
            events=(ApprovalShift(ALPHA, _S, _E, 0.60),),
            incident=_INC,
        ),
        Scenario(
            "gradual_approval_degrade",
            "outage",
            "Primary approval falls linearly 95% -> 60% over 2 min, holds 1 min, then recovers.",
            _pair(),
            _H,
            events=(ApprovalRamp(ALPHA, _S, _S + 120.0, _E, 0.60),),
            incident=_INC,
        ),
        Scenario(
            "flapping",
            "outage",
            "Primary alternates 15 s down (503) / 45 s up from minute 2 to minute 8.",
            _pair(),
            _H,
            events=(Flapping(ALPHA, 120.0, 480.0, 15.0, 45.0, FailureMode.HTTP_503),),
        ),
        Scenario(
            "weak_secondary",
            "outage",
            "Secondary approves 88%; primary 503 for 3 min, so failback speed matters.",
            _pair(beta=0.88),
            _H,
            events=(Outage(ALPHA, _S, _E, FailureMode.HTTP_503),),
            incident=_INC,
        ),
    ]


def _steady_group() -> list[Scenario]:
    out = [
        Scenario(
            "steady_gap_0",
            "steady",
            "30 min, both acquirers approve 95%.",
            _pair(0.95, 0.95),
            1800.0,
        )
    ]
    for label, gap in (("0_5", 0.005), ("1", 0.01), ("3", 0.03)):
        profiles = _pair(0.95, round(0.95 - gap, 4))
        for order, priority in (("right", (ALPHA, BETA)), ("wrong", (BETA, ALPHA))):
            out.append(
                Scenario(
                    f"steady_gap_{label}pp_{order}_priority",
                    "steady",
                    f"30 min, primary better by {gap * 100:g} pp; priority lists the "
                    f"{'better' if order == 'right' else 'worse'} acquirer first.",
                    profiles,
                    1800.0,
                    priority=priority,
                )
            )
    return out


def _capacity_group() -> list[Scenario]:
    # 100 TPS x 250 ms = about 25 payments in flight. The primary holds 60; each backup 15.
    arrivals = Arrivals("poisson", 100.0)
    out = []
    for n_acq in (2, 3):
        for overload in ("reject", "collapse"):
            primary = AcquirerProfile(ALPHA, approval_rate=0.95, capacity=60, overload=overload)
            backups = [
                AcquirerProfile(BETA, approval_rate=0.94, capacity=15, overload=overload),
                AcquirerProfile(GAMMA, approval_rate=0.93, capacity=15, overload=overload),
            ][: n_acq - 1]
            what = "answer 429 above capacity" if overload == "reject" else "collapse above it"
            out.append(
                Scenario(
                    f"capacity_{'429' if overload == 'reject' else 'collapse'}_{n_acq}_acquirers",
                    "capacity",
                    f"100 TPS; primary 503 from 100 s to 200 s; {n_acq - 1} backup(s) hold 15 "
                    f"concurrent requests each and {what}.",
                    (primary, *backups),
                    300.0,
                    arrivals=arrivals,
                    events=(Outage(ALPHA, 100.0, 200.0, FailureMode.HTTP_503),),
                    incident=Window(100.0, 200.0, ALPHA),
                )
            )
    return out


def _context_group() -> list[Scenario]:
    two_issuers = (Segment("issuer_a", 0.6), Segment("issuer_b", 0.4))
    return [
        Scenario(
            "common_mode_issuer_incident",
            "context",
            "Issuer A (60% of payments) halves its approvals on every acquirer for 3 min; "
            "acquirers are unchanged, so the right response is not to move traffic.",
            _pair(),
            _H,
            events=(IssuerIncident("issuer_a", _S, _E, 0.5),),
            segments=two_issuers,
            incident=Window(_S, _E, None),
        ),
        Scenario(
            "segment_heterogeneity",
            "context",
            "30 min; alpha approves issuer A 95% and issuer B 91.2%, beta the reverse; "
            "only a context-aware policy can route each issuer to its better acquirer.",
            (
                AcquirerProfile(ALPHA, approval_rate=0.95, segment_multiplier={"issuer_b": 0.96}),
                AcquirerProfile(BETA, approval_rate=0.95, segment_multiplier={"issuer_a": 0.96}),
            ),
            1800.0,
            segments=(Segment("issuer_a", 0.5), Segment("issuer_b", 0.5)),
        ),
    ]


def _load_group() -> list[Scenario]:
    return [
        Scenario(
            "inflight_concurrency",
            "load",
            "200 TPS with 400 ms latency (about 80 in flight); primary answers 503 after "
            "400 ms from 60 s to 120 s, so many decisions are made before any failure returns.",
            _pair(latency_ms=400.0, fail_latency_ms=400.0),
            180.0,
            arrivals=Arrivals("poisson", 200.0),
            events=(Outage(ALPHA, 60.0, 120.0, FailureMode.HTTP_503),),
            incident=Window(60.0, 120.0, ALPHA),
        ),
        Scenario(
            "low_tps",
            "load",
            "0.5 TPS for 1 h; primary 503 from minute 20 to minute 40.",
            _pair(),
            3600.0,
            arrivals=Arrivals("poisson", 0.5),
            events=(Outage(ALPHA, 1200.0, 2400.0, FailureMode.HTTP_503),),
            incident=Window(1200.0, 2400.0, ALPHA),
        ),
        Scenario(
            "high_tps",
            "load",
            "500 TPS for 2 min; primary 503 from 40 s to 80 s.",
            _pair(),
            120.0,
            arrivals=Arrivals("poisson", 500.0),
            events=(Outage(ALPHA, 40.0, 80.0, FailureMode.HTTP_503),),
            incident=Window(40.0, 80.0, ALPHA),
        ),
        Scenario(
            "bursty_tps",
            "load",
            "2 TPS with 10 s bursts at 60 TPS every 40 s; primary 503 for 3 min.",
            _pair(),
            _H,
            arrivals=Arrivals("bursty", 2.0, burst_tps=60.0, burst_on_sec=10.0, burst_off_sec=30.0),
            events=(Outage(ALPHA, _S, _E, FailureMode.HTTP_503),),
            incident=_INC,
        ),
    ]


# Soak: one day at 1 TPS, primary approving 90% (the audit's revenue-model base case). A
# 30 min fast-fail outage at 06:00, a 30 min timeout outage at 10:00 and a 2 h gray failure
# (-10 pp) at 14:00. Each incident's window includes a 10 min tail, so recovery losses count.
_HOUR = 3600.0
_TAIL = 600.0
_FAST = Window(6 * _HOUR, 6.5 * _HOUR, ALPHA)
_SLOW = Window(10 * _HOUR, 10.5 * _HOUR, ALPHA)
_GRAY = Window(14 * _HOUR, 16 * _HOUR, ALPHA)


def _soak(gap_pp: float) -> Scenario:
    beta = round(0.90 - gap_pp / 100.0, 4)
    incidents = (
        ("fast_fail_outage", _FAST, 2.0),
        ("timeout_outage", _SLOW, 2.0),
        ("gray_failure", _GRAY, 20.0),
    )
    components = [
        Component(name, (Window(w.start, w.end + _TAIL, ALPHA),), w.end - w.start, hours)
        for name, w, hours in incidents
    ]
    covered = sorted((w.start, w.end + _TAIL) for _, w, _ in incidents)
    steady: list[Window] = []
    t = 0.0
    for a, b in covered:
        steady.append(Window(t, a))
        t = b
    steady.append(Window(t, 24 * _HOUR))
    steady_secs = sum(w.end - w.start for w in steady)
    components.append(Component("steady", tuple(steady), steady_secs, 8760.0 - 24.0))
    return Scenario(
        f"soak_day_gap_{gap_pp:g}pp",
        "soak",
        f"24 h at 1 TPS; primary 90%, secondary {beta * 100:g}%; 30 min fast-fail outage, "
        "30 min timeout outage, 2 h gray failure (primary at 80%).",
        _pair(0.90, beta),
        24 * _HOUR,
        arrivals=Arrivals("poisson", 1.0),
        events=(
            Outage(ALPHA, _FAST.start, _FAST.end, FailureMode.HTTP_503),
            Outage(ALPHA, _SLOW.start, _SLOW.end, FailureMode.TIMEOUT),
            ApprovalShift(ALPHA, _GRAY.start, _GRAY.end, 0.80),
        ),
        components=tuple(components),
    )


def all_scenarios() -> list[Scenario]:
    """Every registered scenario, in report order."""
    return [
        *_outage_group(),
        *_steady_group(),
        *_capacity_group(),
        *_context_group(),
        *_load_group(),
        _soak(0.0),
        _soak(1.0),
    ]


SCENARIOS: dict[str, Scenario] = {s.name: s for s in all_scenarios()}


def select(names: Sequence[str] | None) -> list[Scenario]:
    """Scenarios by name (all when ``names`` is empty), keeping registry order."""
    if not names:
        return list(SCENARIOS.values())
    unknown = sorted(set(names) - set(SCENARIOS))
    if unknown:
        raise ValueError(f"unknown scenarios: {unknown}")
    return [s for s in SCENARIOS.values() if s.name in set(names)]
