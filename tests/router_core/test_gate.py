"""Technical gate (Stage 1): CUSUM trips, overdue evidence, probes, backoff, fallback."""

from __future__ import annotations

import pytest

from router_core.gate import GateConfig, GateState, TechnicalGate
from router_core.state import Outcome

A, B, C = "acquirer_alpha", "acquirer_beta", "acquirer_gamma"
OK = Outcome.AUTHORIZED
DECLINE = Outcome.ISSUER_DECLINE
FAIL = Outcome.TECHNICAL_FAILURE


def _gate(config: GateConfig | None = None, ids: tuple[str, ...] = (A, B)) -> TechnicalGate:
    return TechnicalGate(ids, config or GateConfig(), dict.fromkeys(ids, 2.0))


class _Driver:
    """Dispatch-and-answer helper with unique transaction ids."""

    def __init__(self, gate: TechnicalGate) -> None:
        self.gate = gate
        self.n = 0

    def attempt(
        self,
        aid: str,
        outcome: Outcome | None,
        t: float,
        latency: float = 0.05,
        probe: bool = False,
        **kw: bool,
    ) -> None:
        self.n += 1
        tx = f"tx{self.n}"
        self.gate.record_dispatch(tx, 1, aid, t, probe=probe)
        self.gate.record_result(tx, 1, outcome, t + latency, **kw)


def test_defaults_open_after_two_consecutive_failures_not_one() -> None:
    """ln(0.5/0.02) = 3.22 per failure against a threshold of 6: two failures in a row trip."""
    d = _Driver(_gate())
    for i in range(50):
        d.attempt(A, OK, i * 0.1)
    d.attempt(A, FAIL, 5.0)
    assert d.gate.state(A) == GateState.CLOSED
    d.attempt(A, FAIL, 5.1)
    assert d.gate.state(A) == GateState.OPEN
    assert d.gate.blocked() == frozenset({A})
    assert d.gate.status(5.2)[A]["trips"] == 1


def test_issuer_declines_are_evidence_the_acquirer_works() -> None:
    """A decline between two failures pulls the CUSUM down, so the gate stays closed."""
    d = _Driver(_gate())
    d.attempt(A, FAIL, 0.0)
    for k in range(5):
        d.attempt(A, DECLINE, 0.1 + 0.1 * k)
    d.attempt(A, FAIL, 1.0)
    assert d.gate.state(A) == GateState.CLOSED


def test_a_40_percent_brownout_trips_and_a_2_percent_background_does_not() -> None:
    """Deterministic patterns: 2 failures in 5 trips within a few cycles; 1 in 50 never does."""
    brown = _Driver(_gate())
    pattern = [FAIL, OK, OK, FAIL, OK]
    n = 0
    while brown.gate.state(A) == GateState.CLOSED:
        brown.attempt(A, pattern[n % 5], n * 0.07)
        n += 1
    assert n <= 10

    quiet = _Driver(_gate())
    for k in range(2000):
        quiet.attempt(A, FAIL if k % 50 == 49 else OK, k * 0.07)
    assert quiet.gate.state(A) == GateState.CLOSED


def test_429_is_not_gate_evidence_by_default() -> None:
    """A throttled answer means busy, not broken; count_throttled restores the old reading."""
    d = _Driver(_gate())
    for k in range(10):
        d.attempt(A, FAIL, k * 0.1, throttled=True, safe=True)
    assert d.gate.state(A) == GateState.CLOSED

    d2 = _Driver(_gate(GateConfig(count_throttled=True)))
    d2.attempt(A, FAIL, 0.0, throttled=True, safe=True)
    d2.attempt(A, FAIL, 0.1, throttled=True, safe=True)
    assert d2.gate.state(A) == GateState.OPEN


def test_not_sent_is_no_evidence() -> None:
    """Router-side pool exhaustion says nothing about the acquirer."""
    d = _Driver(_gate())
    for k in range(10):
        d.attempt(A, None, k * 0.1)
    assert d.gate.state(A) == GateState.CLOSED


def test_overdue_attempts_open_the_gate_before_the_timeout() -> None:
    """With p95 = 300 ms and k = 3, two attempts unanswered for 0.9 s open the gate at 2 s."""
    g = _gate()
    d = _Driver(g)
    for k in range(40):
        d.attempt(A, OK, k * 0.1, latency=0.2 + 0.1 * (k % 2))
    assert g.overdue_after(A) == pytest.approx(0.9)
    t0 = 10.0
    g.record_dispatch("hang1", 1, A, t0, probe=False)
    g.record_dispatch("hang2", 1, A, t0 + 0.05, probe=False)
    g.scan_overdue(t0 + 0.89)
    assert g.state(A) == GateState.CLOSED
    g.scan_overdue(t0 + 0.96)
    assert g.state(A) == GateState.OPEN  # well before the 2 s timeout
    # The timeouts arriving later are not counted twice.
    g.record_result("hang1", 1, FAIL, t0 + 2.0)
    g.record_result("hang2", 1, FAIL, t0 + 2.05)
    assert g.status(t0 + 3)[A]["trips"] == 1


def test_without_latency_history_nothing_is_overdue_before_90_percent_of_the_timeout() -> None:
    g = _gate()
    assert g.overdue_after(A) == pytest.approx(1.8)


def test_probe_is_due_after_the_backoff_and_a_success_closes_the_gate() -> None:
    g = _gate()
    d = _Driver(g)
    d.attempt(A, FAIL, 0.0)
    d.attempt(A, FAIL, 0.1)  # opens at t = 0.15
    assert g.due_probe(0.5) is None
    assert g.due_probe(1.2) == A
    g.record_dispatch("probe", 1, A, 1.2, probe=True)
    assert g.state(A) == GateState.HALF_OPEN
    assert g.due_probe(5.0) is None  # one probe at a time
    g.record_result("probe", 1, OK, 1.3)
    assert g.state(A) == GateState.CLOSED
    assert g.blocked() == frozenset()


def test_backoff_doubles_with_a_short_cap_for_safe_probes_and_a_long_one_for_lost_ones() -> None:
    """A fast-failing probe costs a hop (the cascade retries it); a timed-out one, a payment."""
    g = _gate()
    d = _Driver(g)
    d.attempt(A, FAIL, 0.0)
    d.attempt(A, FAIL, 0.0)
    t = 0.05
    backoffs = []
    for _ in range(4):
        t += g.status(t)[A]["probe_backoff_sec"]
        assert g.due_probe(t) == A
        d.attempt(A, FAIL, t, probe=True, safe=True)
        t += 0.05
        backoffs.append(g.status(t)[A]["probe_backoff_sec"])
    assert backoffs == [2.0, 2.0, 2.0, 2.0]

    g2 = _gate()
    d2 = _Driver(g2)
    d2.attempt(A, FAIL, 0.0)
    d2.attempt(A, FAIL, 0.0)
    t = 0.05
    backoffs = []
    for _ in range(7):
        t += g2.status(t)[A]["probe_backoff_sec"]
        d2.attempt(A, FAIL, t, latency=2.0, probe=True, safe=False)
        t += 2.0
        backoffs.append(g2.status(t)[A]["probe_backoff_sec"])
    assert backoffs == [2.0, 4.0, 8.0, 16.0, 30.0, 30.0, 30.0]


def test_fallback_prefers_the_acquirer_that_processed_most_recently() -> None:
    """Never the hard-down primary just because its gate opened longest ago."""
    g = _gate(ids=(A, B, C))
    d = _Driver(g)
    d.attempt(A, OK, 0.0)
    d.attempt(A, FAIL, 1.0)
    d.attempt(A, FAIL, 1.1)  # A down since t = 1
    for t in (5.0, 5.1):
        d.attempt(B, OK, t)
        d.attempt(C, OK, t + 0.02)
    for t in (6.0, 6.1):
        d.attempt(B, FAIL, t)
        d.attempt(C, FAIL, t + 0.02)
    assert g.blocked() == frozenset({A, B, C})
    assert g.fallback() == C
    assert g.fallback(frozenset({C})) == B
    assert g.recently_working() == frozenset({B, C})


def test_unanswered_attempts_are_dropped_so_memory_stays_bounded() -> None:
    g = _gate()
    for k in range(100):
        g.record_dispatch(f"lost{k}", 1, B, k * 0.01, probe=False)
    g.scan_overdue(10.0)
    assert g.status(10.0)[B]["state"] == "OPEN"  # they were overdue first
    g.scan_overdue(100.0)
    assert not g._pending


def test_config_validation() -> None:
    with pytest.raises(ValueError, match="outage_failure_rate"):
        GateConfig(baseline_failure_rate=0.5, outage_failure_rate=0.4)
    with pytest.raises(ValueError, match="probe_backoff_max_sec"):
        GateConfig(probe_backoff_initial_sec=5.0, probe_backoff_max_sec=1.0)
