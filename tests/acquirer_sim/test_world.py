"""World model (Stage 0): failure taxonomy, capacity, segments, timelines, common random numbers."""

from __future__ import annotations

import math

import pytest

from acquirer_sim.crn import ISSUER, crn_uniform, technical_stream
from acquirer_sim.world import (
    AcquirerProfile,
    ApprovalRamp,
    ApprovalShift,
    Brownout,
    FailureMode,
    Flapping,
    IssuerIncident,
    Outage,
    ResponseKind,
    Segment,
    World,
)

A, B = "acquirer_alpha", "acquirer_beta"


def _world(**kw: object) -> World:
    profiles = kw.pop("profiles", (AcquirerProfile(A), AcquirerProfile(B, approval_rate=0.94)))
    return World(profiles=profiles, seed=7, **kw)  # type: ignore[arg-type]


def test_crn_depends_only_on_seed_tx_and_stream() -> None:
    """The same key gives the same draw whatever was drawn before; keys differ otherwise."""
    first = crn_uniform(1, "tx1", ISSUER)
    for i in range(50):
        crn_uniform(1, f"tx{i}", "other")
    assert crn_uniform(1, "tx1", ISSUER) == first
    assert crn_uniform(2, "tx1", ISSUER) != first
    assert crn_uniform(1, "tx1", technical_stream(A)) != first
    draws = [crn_uniform(3, f"tx{i}", ISSUER) for i in range(20_000)]
    assert all(0.0 <= u < 1.0 for u in draws)
    assert sum(draws) / len(draws) == pytest.approx(0.5, abs=0.01)


def test_issuer_draw_is_shared_so_a_better_acquirer_approves_a_superset() -> None:
    """Coupling: every payment the 94% acquirer approves, the 95% acquirer approves too."""
    world = _world()
    for i in range(5000):
        tx = f"tx{i}"
        on_b = world.respond(tx, B, 1.0).kind == ResponseKind.AUTHORIZED
        on_a = world.respond(tx, A, 1.0).kind == ResponseKind.AUTHORIZED
        assert on_a or not on_b


def test_default_world_matches_simulator_rule() -> None:
    """Without events or segments, a payment is authorized iff its issuer draw < approval."""
    world = _world()
    for i in range(500):
        tx = f"tx{i}"
        expected = crn_uniform(7, tx, ISSUER) < 0.95
        assert (world.respond(tx, A, 0.5).kind == ResponseKind.AUTHORIZED) == expected


@pytest.mark.parametrize(
    ("mode", "kind"),
    [
        (FailureMode.HTTP_503, ResponseKind.HTTP_503),
        (FailureMode.DECLINE_CODE, ResponseKind.TECH_DECLINE),
        (FailureMode.HTTP_429, ResponseKind.HTTP_429),
        (FailureMode.TIMEOUT, ResponseKind.TIMEOUT),
    ],
)
def test_outage_modes(mode: FailureMode, kind: ResponseKind) -> None:
    """Each outage mode answers with its kind inside the window, and nothing is processed."""
    world = _world(events=(Outage(A, 10.0, 20.0, mode),))
    resp = world.respond("tx", A, 15.0)
    assert resp.kind == kind
    assert not resp.processed
    if mode == FailureMode.TIMEOUT:
        assert math.isinf(resp.latency_sec)
    else:
        assert resp.latency_sec == pytest.approx(0.05)
    assert world.respond("tx", A, 20.0).kind != kind  # window is [start, end)
    assert world.expected_success(A, "default", 15.0) == 0.0


def test_slow_answer_past_timeout_is_processed_but_unseen() -> None:
    """Latency above the router timeout is a TIMEOUT that may still have been approved."""
    world = _world(
        profiles=(AcquirerProfile(A, latency_ms=2500.0, jitter_ms=0.0),),
        timeout_sec=2.0,
    )
    resp = world.respond("tx", A, 1.0)
    assert resp.kind == ResponseKind.TIMEOUT
    assert resp.processed
    assert resp.issuer_approved == (crn_uniform(7, "tx", ISSUER) < 0.95)


def test_flapping_alternates_down_then_up() -> None:
    """Down for down_sec, up for up_sec, repeating inside the window."""
    world = _world(events=(Flapping(A, 100.0, 200.0, 10.0, 20.0),))
    assert world.down_mode(A, 105.0) == FailureMode.HTTP_503
    assert world.down_mode(A, 115.0) is None
    assert world.down_mode(A, 135.0) == FailureMode.HTTP_503
    assert world.down_mode(A, 205.0) is None


def test_brownout_and_background_errors_compose() -> None:
    """Background and brownout 503 rates combine as independent failures."""
    world = _world(
        profiles=(AcquirerProfile(A, technical_error_rate=0.1),),
        events=(Brownout(A, 0.0, 100.0, 0.4),),
    )
    assert world.technical_error_rate(A, 50.0) == pytest.approx(1 - 0.9 * 0.6)
    assert world.technical_error_rate(A, 150.0) == pytest.approx(0.1)
    fails = sum(world.respond(f"tx{i}", A, 50.0).kind == ResponseKind.HTTP_503 for i in range(4000))
    assert fails / 4000 == pytest.approx(0.46, abs=0.03)


def test_capacity_reject_answers_429_at_capacity() -> None:
    """A rejecting acquirer throttles once its in-flight count reaches capacity."""
    world = _world(profiles=(AcquirerProfile(A, capacity=5, overload="reject"),))
    assert world.respond("tx", A, 1.0, in_flight=4).kind != ResponseKind.HTTP_429
    assert world.respond("tx", A, 1.0, in_flight=5).kind == ResponseKind.HTTP_429


def test_capacity_collapse_fails_and_slows_with_load() -> None:
    """Collapse: at load ratio rho the extra 503 share is rho - 1 and latency scales by rho."""
    world = _world(profiles=(AcquirerProfile(A, capacity=10, overload="collapse", jitter_ms=0.0),))
    fast = world.respond("tx0", A, 1.0, in_flight=0)
    assert fast.latency_sec == pytest.approx(0.25)
    fails = sum(
        world.respond(f"tx{i}", A, 1.0, in_flight=14).kind == ResponseKind.HTTP_503
        for i in range(4000)
    )
    assert fails / 4000 == pytest.approx(0.5, abs=0.03)  # rho = 15/10
    ok = next(
        r for i in range(100) if (r := world.respond(f"tx{i}", A, 1.0, in_flight=14)).processed
    )
    assert ok.latency_sec == pytest.approx(0.25 * 1.5)


def test_segments_incidents_and_acquirer_interaction() -> None:
    """Segment multipliers, acquirer x segment multipliers and incidents multiply approval."""
    world = World(
        profiles=(
            AcquirerProfile(A, approval_rate=0.9, segment_multiplier={"s2": 0.5}),
            AcquirerProfile(B, approval_rate=0.9),
        ),
        segments=(Segment("s1", 0.7), Segment("s2", 0.3, approval_multiplier=0.8)),
        events=(IssuerIncident("s1", 10.0, 20.0, 0.5),),
        seed=1,
    )
    assert world.approval_rate(A, "s1", 0.0) == pytest.approx(0.9)
    assert world.approval_rate(A, "s2", 0.0) == pytest.approx(0.9 * 0.8 * 0.5)
    assert world.approval_rate(B, "s2", 0.0) == pytest.approx(0.9 * 0.8)
    assert world.approval_rate(A, "s1", 15.0) == pytest.approx(0.45)
    assert world.approval_rate(B, "s1", 15.0) == pytest.approx(0.45)
    shares = [world.segment_of(f"tx{i}") for i in range(5000)]
    assert shares.count("s1") / 5000 == pytest.approx(0.7, abs=0.02)
    assert world.segment_of("tx3") == world.segment_of("tx3")


def test_gray_failure_and_ramp() -> None:
    """ApprovalShift replaces the rate; ApprovalRamp interpolates, holds, then ends."""
    world = _world(
        events=(
            ApprovalShift(B, 0.0, 10.0, 0.6),
            ApprovalRamp(A, 100.0, 200.0, 300.0, 0.55),
        )
    )
    assert world.approval_rate(B, "default", 5.0) == pytest.approx(0.6)
    assert world.approval_rate(A, "default", 150.0) == pytest.approx(0.75)
    assert world.approval_rate(A, "default", 250.0) == pytest.approx(0.55)
    assert world.approval_rate(A, "default", 300.0) == pytest.approx(0.95)


def test_validation() -> None:
    """Bad shares, unknown ids and inverted windows are rejected."""
    with pytest.raises(ValueError, match="sum to 1"):
        World((AcquirerProfile(A),), segments=(Segment("s", 0.5),))
    with pytest.raises(ValueError, match="unknown acquirer"):
        World((AcquirerProfile(A),), events=(Outage(B, 0.0, 1.0),))
    with pytest.raises(ValueError, match="unknown segment"):
        World((AcquirerProfile(A),), events=(IssuerIncident("x", 0.0, 1.0, 0.5),))
    with pytest.raises(ValueError, match="ends before"):
        World((AcquirerProfile(A),), events=(Outage(A, 2.0, 1.0),))
    with pytest.raises(ValueError, match="approval_rate"):
        AcquirerProfile(A, approval_rate=1.5)
    with pytest.raises(ValueError, match="capacity"):
        AcquirerProfile(A, capacity=0)
