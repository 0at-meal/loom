"""Wall-clock belief decay (AUDIT F-23): memory is measured in seconds, not observations."""

from __future__ import annotations

import httpx
import numpy as np
import pytest

from acquirer_sim.models import AuthorizeRequest
from router_core.bandit import BanditStateRegistry
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.router import BanditRouter
from router_core.server import build_router_config, parse_args
from router_core.state import DEFAULT_HALF_LIFE_SEC, AcquirerState, AcquirerStateConfig


def test_default_config_decays_on_the_wall_clock() -> None:
    """The default config uses a half-life in seconds, not a per-observation factor."""
    config = AcquirerStateConfig()
    assert config.half_life_sec == DEFAULT_HALF_LIFE_SEC
    assert config.decay_factor is None
    assert config.uses_wall_clock


def test_half_life_and_decay_factor_are_exclusive() -> None:
    """Setting both decay modes is ambiguous and rejected."""
    with pytest.raises(ValueError, match="either half_life_sec or decay_factor"):
        AcquirerStateConfig(alpha_prior=1.0, half_life_sec=1.0, decay_factor=0.98)


@pytest.mark.parametrize("bad", [0.0, -1.0, float("nan"), float("inf")])
def test_invalid_half_life_rejected(bad: float) -> None:
    """The half-life must be a positive finite number of seconds."""
    with pytest.raises(ValueError, match="half_life_sec"):
        AcquirerStateConfig(alpha_prior=1.0, half_life_sec=bad)


def test_observation_weight_halves_after_one_half_life() -> None:
    """An observation's weight is 0.5 after one half-life, however much traffic arrived."""
    busy = AcquirerState(
        "busy", AcquirerStateConfig(alpha_prior=1.0, half_life_sec=1.0), initial_timestamp=0.0
    )
    quiet = AcquirerState(
        "quiet", AcquirerStateConfig(alpha_prior=1.0, half_life_sec=1.0), initial_timestamp=0.0
    )
    for _ in range(10):
        busy.record_outcome(False, timestamp=0.0)
        quiet.record_outcome(False, timestamp=0.0)

    # The busy arm sees 100 successes during the half-life, the quiet arm one at its end.
    for i in range(1, 101):
        busy.record_outcome(True, timestamp=i / 100)
    quiet.record_outcome(True, timestamp=1.0)

    # The 10 failures at t=0 keep weight 10 * 0.5 = 5 on both arms.
    assert busy.get_state().beta - 1.0 == pytest.approx(5.0)
    assert quiet.get_state().beta - 1.0 == pytest.approx(5.0)


def test_idle_arm_returns_to_its_prior() -> None:
    """An arm that receives no traffic forgets its failures as time passes."""
    state = AcquirerState(
        "idle", AcquirerStateConfig(alpha_prior=1.0, half_life_sec=1.0), initial_timestamp=0.0
    )
    for _ in range(20):
        state.record_outcome(False, timestamp=0.0)
    assert state.get_state().expected_success_rate < 0.1

    snap = state.get_state(now=10.0)
    assert snap.beta - 1.0 == pytest.approx(20 * 0.5**10)
    assert snap.expected_success_rate == pytest.approx(0.5, abs=0.01)


def test_sampling_decays_an_idle_arm() -> None:
    """Thompson draws for an idle arm come from its decayed belief."""
    registry = BanditStateRegistry(AcquirerStateConfig(alpha_prior=1.0, half_life_sec=1.0))
    registry.register_acquirer("dead", initial_timestamp=0.0)
    for _ in range(50):
        registry.record_outcome("dead", False, timestamp=0.0)

    rng = np.random.default_rng(0)
    early = [registry.sample_all(rng=rng, now=0.0)["dead"] for _ in range(200)]
    late = [registry.sample_all(rng=rng, now=30.0)["dead"] for _ in range(200)]
    assert np.mean(early) < 0.05
    # Back at the prior: technical and approval draws are both uniform, and the Thompson
    # score is their product, whose mean is 0.25.
    assert np.mean(late) == pytest.approx(0.25, abs=0.05)


def test_reading_state_without_a_time_does_not_decay() -> None:
    """get_state() and sample() with no time leave the belief as of the last event."""
    state = AcquirerState(
        "a", AcquirerStateConfig(alpha_prior=1.0, half_life_sec=1.0), initial_timestamp=0.0
    )
    state.record_outcome(False, timestamp=0.0)
    assert state.get_state().beta == pytest.approx(2.0)
    state.sample(rng=np.random.default_rng(0))
    assert state.get_state().beta == pytest.approx(2.0)


def test_time_going_backwards_does_not_grow_beliefs() -> None:
    """A clock step backwards is treated as zero elapsed time."""
    state = AcquirerState(
        "a", AcquirerStateConfig(alpha_prior=1.0, half_life_sec=1.0), initial_timestamp=10.0
    )
    state.record_outcome(False, timestamp=10.0)
    snap = state.get_state(now=5.0)
    assert snap.beta == pytest.approx(2.0)


def test_wall_clock_health_is_the_decayed_success_fraction() -> None:
    """Health counts initial_health as one pseudo-observation plus decayed outcomes."""
    state = AcquirerState(
        "a", AcquirerStateConfig(alpha_prior=1.0, half_life_sec=1.0), initial_timestamp=0.0
    )
    assert state.get_state().health_score == 1.0
    for _ in range(3):
        state.record_outcome(False, timestamp=0.0)
    assert state.get_state().health_score == pytest.approx(1.0 / 4.0)


def test_per_observation_mode_is_unchanged() -> None:
    """An explicit decay_factor keeps the original per-observation update."""
    state = AcquirerState(
        "a", AcquirerStateConfig(alpha_prior=1.0, decay_factor=0.5), initial_timestamp=0.0
    )
    state.record_outcome(False, timestamp=0.0)
    state.record_outcome(False, timestamp=100.0)
    assert state.get_state(now=1000.0).beta == pytest.approx(1.0 + 0.5 + 1.0)


@pytest.mark.asyncio
async def test_router_decays_idle_arms_with_its_clock() -> None:
    """The router samples at its own clock's time, so an idle arm recovers."""
    now = [0.0]

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "transaction_id": "t",
                "acquirer_id": "a",
                "status": "AUTHORIZED",
                "authorized": True,
                "authorization_code": "AUTH_X",
                "decline_code": None,
                "decline_message": None,
                "simulated_latency_ms": 0.0,
                "timestamp": 0.0,
            },
        )

    cfg = AcquirerStateConfig(alpha_prior=1.0, half_life_sec=1.0)
    routes = [
        AcquirerRouteConfig(acquirer_id="a", base_url="http://a", state_config=cfg),
        AcquirerRouteConfig(acquirer_id="b", base_url="http://b", state_config=cfg),
    ]
    registry = BanditStateRegistry(cfg)
    registry.register_acquirer("a", initial_timestamp=0.0)
    registry.register_acquirer("b", initial_timestamp=0.0)
    for _ in range(30):
        registry.record_outcome("b", False, timestamp=0.0)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        router = BanditRouter(
            config=RouterConfig(routes=routes, seed=1),
            http_client=client,
            registry=registry,
            clock=lambda: now[0],
        )
        now[0] = 60.0
        result = await router.route(AuthorizeRequest(transaction_id="t1", amount=10.0))

    # Sixty half-lives later, b's 30 failures have weight ~0 and its belief is the prior.
    assert router.get_state("b").beta == pytest.approx(1.0, abs=1e-9)
    assert result.state_snapshot.last_updated_at == 60.0


def test_server_reads_half_life_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """DECAY_HALF_LIFE_SEC sets the default half-life; --decay-factor opts into the old mode."""
    monkeypatch.setenv("DECAY_HALF_LIFE_SEC", "4.5")
    cfg = build_router_config(parse_args([]))
    assert cfg.routes[0].state_config.half_life_sec == 4.5

    cfg = build_router_config(parse_args(["--half-life-sec", "1.5"]))
    assert cfg.routes[0].state_config.half_life_sec == 1.5

    cfg = build_router_config(parse_args(["--decay-factor", "0.98"]))
    assert cfg.routes[0].state_config.decay_factor == 0.98
    assert cfg.routes[0].state_config.half_life_sec is None


def test_redis_registry_decays_on_the_wall_clock() -> None:
    """The Redis-backed state applies the same wall-clock decay as the in-memory one."""
    import fakeredis

    from data_layer.redis_state import RedisBanditStateRegistry

    registry = RedisBanditStateRegistry(
        redis_client=fakeredis.FakeRedis(decode_responses=True),
        default_config=AcquirerStateConfig(alpha_prior=1.0, half_life_sec=1.0),
    )
    registry.register_acquirer("a", initial_timestamp=0.0)
    for _ in range(10):
        registry.record_outcome("a", False, timestamp=0.0)

    assert registry.get_state("a").beta - 1.0 == pytest.approx(10.0)
    assert registry.get_state("a", now=1.0).beta - 1.0 == pytest.approx(5.0)
    snap = registry.record_outcome("a", True, timestamp=2.0)
    assert snap.beta - 1.0 == pytest.approx(2.5)
    assert snap.alpha - 1.0 == pytest.approx(1.0)
