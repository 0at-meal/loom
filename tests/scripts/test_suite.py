"""Stage 0 harness: DES-vs-HTTP parity, config pins, pairing, metrics and the scenario registry."""

from __future__ import annotations

import dataclasses
import itertools
import json
import logging
import math
from pathlib import Path
from typing import Any

import httpx
import numpy as np
import pytest

from acquirer_sim.app import create_app
from acquirer_sim.models import AuthorizeRequest, LatencyConfig, OutageToggleRequest
from acquirer_sim.world import AcquirerProfile, FailureMode, Outage, World
from baseline_router.models import BaselineRouterConfig, FailoverPolicyConfig
from baseline_router.router import StaticBaselineRouter
from router_core.policy import Decision, DecisionContext, Observation
from router_core.router import BanditRouter
from router_core.server import build_router_config, parse_args
from scripts import compare_psr
from scripts.suite import run_suite
from scripts.suite.des import Arrivals, simulate
from scripts.suite.metrics import _first_crossing, annual_lost_gmv
from scripts.suite.policies import (
    BENCH_HALF_LIFE_SEC,
    POLICIES,
    bench_router_config,
    build_policy,
    served_router_config,
)
from scripts.suite.scenarios import SCENARIOS, TEST_SEED_BASE, Scenario, Window, run_seeds

A, B = "acquirer_alpha", "acquirer_beta"
TPS = 15.0
N = 150


def _parity_world(seed: int) -> World:
    """The compare_psr shape: zero latency, Alpha's ACQUIRER_OUTAGE for payments 51-100."""
    profiles = (
        AcquirerProfile(A, approval_rate=0.95, latency_ms=0.0, jitter_ms=0.0, fail_latency_ms=0.0),
        AcquirerProfile(B, approval_rate=0.94, latency_ms=0.0, jitter_ms=0.0, fail_latency_ms=0.0),
    )
    outage = Outage(A, 50.5 / TPS, 100.5 / TPS, FailureMode.DECLINE_CODE)
    return World(profiles, events=(outage,), seed=seed)


async def _http_run(router_kind: str, crn_seed: int, policy_seed: int) -> list[tuple[str, bool]]:
    """The same 150 payments through the async router and the HTTP simulator in CRN mode."""
    sim_app = create_app(
        default_acquirers=[A, B],
        default_latency=LatencyConfig(base_ms=0.0, jitter_ms=0.0),
        crn_seed=crn_seed,
    )
    sim_app.state.registry.get(A).set_success_rate(0.95)
    sim_app.state.registry.get(B).set_success_rate(0.94)
    now = [0.0]
    out: list[tuple[str, bool]] = []
    transport = httpx.ASGITransport(app=sim_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        router: Any
        if router_kind == "loom_bench":
            config = bench_router_config([A, B], policy_seed)
            for r in config.routes:
                r.base_url = "http://testserver"
            router = BanditRouter(config=config, http_client=client, clock=lambda: now[0])
        else:
            router = StaticBaselineRouter(
                BaselineRouterConfig(
                    routes=bench_router_config([A, B], 0).routes,
                    priority_order=[A, B],
                    failover_policy=FailoverPolicyConfig(
                        consecutive_failure_threshold=3, cooldown_transactions=30
                    ),
                ),
                http_client=client,
            )
            for r in router.config.routes:
                r.base_url = "http://testserver"
        for i in range(1, N + 1):
            if i == 51:
                await client.post(
                    f"/acquirers/{A}/admin/outage",
                    json=OutageToggleRequest(active=True).model_dump(),
                )
            if i == 101:
                await client.post(
                    f"/acquirers/{A}/admin/outage",
                    json=OutageToggleRequest(active=False).model_dump(),
                )
            now[0] = i / TPS
            res = await router.route(AuthorizeRequest(transaction_id=f"tx{i - 1}", amount=40.0))
            out.append((res.selected_acquirer, res.authorized))
    return out


@pytest.mark.parametrize("policy_name", ["loom_bench", "static_m3"])
@pytest.mark.parametrize("seed", [42, 7])
async def test_des_matches_http_path(
    policy_name: str, seed: int, caplog: pytest.LogCaptureFixture
) -> None:
    """The DES and the async HTTP router make the same decisions with the same outcomes."""
    caplog.set_level(logging.ERROR)
    world = _parity_world(seed)
    policy = build_policy(policy_name, [A, B], world, 777)
    times = Arrivals("constant", TPS).times((N + 0.5) / TPS, np.random.default_rng(0))
    assert len(times) == N
    des = simulate(world, policy, times, tx_prefix="tx")
    des_trace = [(p.attempts[0].acquirer_id, p.authorized) for p in des.payments]
    http_trace = await _http_run(policy_name, crn_seed=seed, policy_seed=777)
    assert des_trace == http_trace
    # The outage really happened and was routed around.
    assert sum(1 for a, ok in des_trace[50:100] if a == A and not ok) > 0


def test_served_config_matches_the_server_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """The suite's 'loom_served' is what `python -m router_core.server` builds by default."""
    monkeypatch.delenv("DECAY_HALF_LIFE_SEC", raising=False)
    served = build_router_config(parse_args([]))
    mine = served_router_config([r.acquirer_id for r in served.routes], seed=1)
    assert mine.pid_config == served.pid_config
    assert [r.state_config for r in mine.routes] == [r.state_config for r in served.routes]
    assert mine.technical_decline_codes == served.technical_decline_codes
    assert mine.value_scaled_config == served.value_scaled_config is None


def test_bench_config_matches_compare_psr() -> None:
    """'loom_bench' keeps compare_psr.py's half-life and deficit actuation."""
    assert BENCH_HALF_LIFE_SEC == compare_psr.BENCH_HALF_LIFE_SEC
    cfg = bench_router_config([A, B], 1)
    assert cfg.pid_config is not None
    assert cfg.pid_config.actuation_mode == "deficit"
    assert cfg.routes[0].state_config.half_life_sec == compare_psr.BENCH_HALF_LIFE_SEC


def _small(scenario: Scenario, horizon: float) -> Scenario:
    """A copy with a shorter horizon (tests only)."""
    return dataclasses.replace(scenario, horizon_sec=horizon)


@pytest.mark.parametrize("name", sorted(POLICIES))
def test_no_policy_retries_an_ambiguous_attempt(name: str) -> None:
    """Zero retries after a timeout for every registered policy, on the timeout outage."""
    scenario = _small(SCENARIOS["hard_outage_timeout"], 400.0)
    world = scenario.build_world(5)
    policy = build_policy(name, scenario.priority, world, scenario.policy_seed(5))
    result = simulate(world, policy, scenario.arrival_times(5))
    assert result.ambiguous_retries == 0
    if name != "oracle":  # the oracle knows about the outage and never meets a timeout
        assert any(a.kind == "TIMEOUT" for p in result.payments for a in p.attempts)


class _RetriesEverything:
    """A deliberately unsafe policy: retries after any failure, including timeouts."""

    name = "unsafe"
    acquirer_ids = (A, B)
    max_attempts = 2

    def decide(
        self, ctx: DecisionContext, now: float, exclude: frozenset[str] = frozenset()
    ) -> Decision:
        aid = A if A not in exclude else B
        return Decision(ctx.transaction_id, aid, {A: 1.0, B: 0.0}, "x", now, ctx.attempt)

    def feedback(self, decision: Decision, observation: Observation, now: float) -> None:
        pass

    def should_retry(self, decision: Decision, observation: Observation) -> bool:
        return not observation.authorized


def test_harness_counts_ambiguous_retries() -> None:
    """The metric is live: a policy that retries after timeouts is caught."""
    profiles = (AcquirerProfile(A, latency_ms=3000.0, jitter_ms=0.0), AcquirerProfile(B))
    world = World(profiles, seed=1, timeout_sec=2.0)
    result = simulate(world, _RetriesEverything(), [1.0, 2.0, 3.0])
    assert result.ambiguous_retries == 3
    assert result.duplicate_risk_retries == sum(
        1 for p in result.payments if p.attempts[0].issuer_approved
    )


def test_runs_are_deterministic_and_paired() -> None:
    """Same seed, same result; every policy of a run sees the same arrivals and draws."""
    scenario = _small(SCENARIOS["approval_gray_failure"], 300.0)
    a = run_suite.run_unit(scenario.name, 3, ["loom_served", "oracle"], False)
    b = run_suite.run_unit(scenario.name, 3, ["loom_served", "oracle"], False)
    assert a == b
    # Two copies of the oracle are identical; the oracle never loses to a fixed-acquirer run.
    world = scenario.build_world(3)
    times = scenario.arrival_times(3)
    r1 = simulate(world, build_policy("oracle", scenario.priority, world, 1), times)
    r2 = simulate(world, build_policy("oracle", scenario.priority, world, 2), times)
    assert [p.authorized for p in r1.payments] == [p.authorized for p in r2.payments]


def test_arrival_processes() -> None:
    """Constant spacing, Poisson and bursty rates come out at their configured averages."""
    rng = np.random.default_rng(1)
    constant = Arrivals("constant", 10.0).times(1.0, rng)
    assert constant == pytest.approx([i / 10 for i in range(1, 10)])
    poisson = Arrivals("poisson", 50.0).times(200.0, rng)
    assert len(poisson) / 200.0 == pytest.approx(50.0, rel=0.03)
    bursty = Arrivals("bursty", 2.0, burst_tps=60.0, burst_on_sec=10.0, burst_off_sec=30.0)
    times = bursty.times(4000.0, rng)
    assert len(times) / 4000.0 == pytest.approx(bursty.mean_tps, rel=0.03)
    assert times == sorted(times)
    assert bursty.mean_tps == pytest.approx(16.5)


def test_divert_and_restore_crossings() -> None:
    """Crossing times on a hand-made decision stream."""
    decisions = [(t / 10, A if t < 50 or t >= 120 else B) for t in range(200)]
    # Window 1 s = 10 decisions. Share of A falls below 20% once fewer than 2 of 10 are A.
    divert = _first_crossing(decisions, A, 4.0, 1.0, below=0.2)
    assert divert == pytest.approx(0.9)
    restore = _first_crossing(decisions, A, 10.0, 1.0, at_least=0.5)
    assert restore == pytest.approx(1.5)
    assert _first_crossing(decisions, A, 0.0, 1.0, below=0.0) is None


def test_annual_lost_gmv_uses_incident_payments_as_the_base() -> None:
    """Loss per incident-time payment, scaled to the component's annual hours."""
    scenario = SCENARIOS["soak_day_gap_0pp"]
    comp = {c.name: c for c in scenario.components}
    zero = {"payments": 1, "authorized": 0, "incident": 1}
    mine = {f"component:{c}": dict(zero) for c in comp}
    ref = {f"component:{c}": dict(zero) for c in comp}
    mine["component:fast_fail_outage"] = {"payments": 2400, "authorized": 1500, "incident": 1800}
    ref["component:fast_fail_outage"] = {"payments": 2400, "authorized": 1590, "incident": 1800}
    lost = annual_lost_gmv(scenario, mine, ref)
    expected = 1e9 * 2.0 / 8760.0 * (90 / 1800) * 0.5
    assert lost["fast_fail_outage"] == pytest.approx(expected)
    assert lost["total"] == pytest.approx(expected)


def test_registry_is_the_pre_registered_suite() -> None:
    """The suite registered in the decisions log; renaming or dropping one breaks this test."""
    expected = {
        "hard_outage_fast_fail",
        "hard_outage_timeout",
        "technical_brownout",
        "approval_gray_failure",
        "gradual_approval_degrade",
        "flapping",
        "weak_secondary",
        "steady_gap_0",
        *(f"steady_gap_{g}pp_{o}_priority" for g in ("0_5", "1", "3") for o in ("right", "wrong")),
        "capacity_429_2_acquirers",
        "capacity_429_3_acquirers",
        "capacity_collapse_2_acquirers",
        "capacity_collapse_3_acquirers",
        "common_mode_issuer_incident",
        "segment_heterogeneity",
        "inflight_concurrency",
        "low_tps",
        "high_tps",
        "bursty_tps",
        "soak_day_gap_0pp",
        "soak_day_gap_1pp",
    }
    assert set(SCENARIOS) == expected
    for s in SCENARIOS.values():
        s.build_world(0)
        if s.incident is not None:
            assert 0 <= s.incident.start < s.incident.end <= s.horizon_sec
    wrong = SCENARIOS["steady_gap_1pp_wrong_priority"]
    assert wrong.priority == (B, A)
    assert set(run_seeds("train", 5)).isdisjoint(run_seeds("test", 5))
    assert run_seeds("test", 2) == [TEST_SEED_BASE, TEST_SEED_BASE + 1]


def test_soak_components_cover_the_day_once() -> None:
    """Steady and incident windows tile 24 h with no overlap; annual hours sum to a year."""
    for name in ("soak_day_gap_0pp", "soak_day_gap_1pp"):
        s = SCENARIOS[name]
        windows: list[Window] = sorted(
            (w for c in s.components for w in c.windows), key=lambda w: w.start
        )
        assert windows[0].start == 0.0
        assert windows[-1].end == s.horizon_sec
        for prev, nxt in itertools.pairwise(windows):
            assert prev.end == nxt.start
        assert sum(c.annual_hours for c in s.components) == pytest.approx(8760.0)


def test_run_suite_writes_json(tmp_path: Path) -> None:
    """End to end on the cheapest scenario: summary keys, regret and comparisons present."""
    out = tmp_path / "suite.json"
    run_suite.main(
        [
            "--scenarios",
            "low_tps",
            "--seeds",
            "2",
            "--split",
            "train",
            "--workers",
            "1",
            "--policies",
            "loom_served,competent,static_m3",
            "--out-json",
            str(out),
        ]
    )
    data = json.loads(out.read_text(encoding="utf-8"))
    entry = data["scenarios"]["low_tps"]
    assert data["policies"] == ["loom_served", "competent", "static_m3", "oracle"]
    assert entry["n_seeds"] == 2
    assert set(entry["regret_vs_oracle"]) == {"loom_served", "competent", "static_m3"}
    assert "loom_served_vs_competent" in entry["comparisons"]
    psr = entry["policies"]["competent"]["psr"]["mean"]
    assert 0.5 < psr <= 1.0
    assert math.isfinite(entry["policies"]["oracle"]["psr"]["mean"])
    with pytest.raises(SystemExit):
        run_suite.main(["--seeds", "1"])
