"""Unit tests for AcquirerSimulator state machine and multi-acquirer registry."""

import numpy as np
import pytest

from acquirer_sim.models import (
    AcquirerConfig,
    AuthorizeRequest,
    LatencyConfig,
    OutageBehavior,
)
from acquirer_sim.simulator import (
    AcquirerOutageHttpException,
    AcquirerSimulator,
    MultiAcquirerSimulator,
)


@pytest.fixture
def fast_latency_config() -> LatencyConfig:
    """Zero-delay latency configuration for rapid test execution."""
    return LatencyConfig(base_ms=0.0, jitter_ms=0.0, outage_spike_ms=0.0)


@pytest.fixture
def default_simulator(fast_latency_config: LatencyConfig) -> AcquirerSimulator:
    """Acquirer simulator with deterministic seed and zero latency."""
    cfg = AcquirerConfig(
        acquirer_id="test_acquirer",
        base_success_rate=0.80,
        latency=fast_latency_config,
    )
    rng = np.random.default_rng(seed=42)
    return AcquirerSimulator(config=cfg, rng=rng)


class TestAcquirerSimulatorSuccessRateTracking:
    """Verify authorize endpoint tracks configured success rate over many calls."""

    @pytest.mark.asyncio
    async def test_tracks_eighty_percent_rate_over_many_calls(
        self, default_simulator: AcquirerSimulator
    ) -> None:
        """Verify empirical success rate converges to configured 80% over 2000 trials."""
        trials = 2000
        successes = 0

        for i in range(trials):
            req = AuthorizeRequest(
                transaction_id=f"tx_{i}",
                amount=25.0,
            )
            res = await default_simulator.execute_authorization(req)
            if res.authorized:
                successes += 1
                assert res.status == "AUTHORIZED"
                assert res.authorization_code is not None
            else:
                assert res.status == "DECLINED"
                assert res.decline_code == "DO_NOT_HONOR"

        empirical_rate = successes / trials
        # 80% with 2000 trials has standard deviation ~0.0089. Within 3 sigma is [0.77, 0.83].
        assert 0.77 <= empirical_rate <= 0.83

        snapshot = default_simulator.get_telemetry_snapshot()
        assert snapshot.total_requests == trials
        assert snapshot.authorized_count == successes
        assert snapshot.declined_count == trials - successes
        assert snapshot.empirical_success_rate == round(empirical_rate, 4)

    @pytest.mark.asyncio
    async def test_tracks_ninety_five_percent_rate(
        self, fast_latency_config: LatencyConfig
    ) -> None:
        """Verify empirical success rate converges to configured 95%."""
        cfg = AcquirerConfig(
            acquirer_id="high_psr",
            base_success_rate=0.95,
            latency=fast_latency_config,
        )
        sim = AcquirerSimulator(config=cfg, rng=np.random.default_rng(1234))

        trials = 1500
        successes = 0
        for i in range(trials):
            req = AuthorizeRequest(transaction_id=f"tx_{i}", amount=10.0)
            res = await sim.execute_authorization(req)
            if res.authorized:
                successes += 1

        empirical_rate = successes / trials
        assert 0.93 <= empirical_rate <= 0.97

    @pytest.mark.asyncio
    async def test_deterministic_extremes_zero_and_one(
        self, fast_latency_config: LatencyConfig
    ) -> None:
        """Verify 100% and 0% configurations behave with absolute determinism."""
        sim_100 = AcquirerSimulator(
            config=AcquirerConfig(
                acquirer_id="perfect", base_success_rate=1.0, latency=fast_latency_config
            )
        )
        for i in range(50):
            res = await sim_100.execute_authorization(
                AuthorizeRequest(transaction_id=f"tx_{i}", amount=5.0)
            )
            assert res.authorized is True

        sim_0 = AcquirerSimulator(
            config=AcquirerConfig(
                acquirer_id="failing", base_success_rate=0.0, latency=fast_latency_config
            )
        )
        for i in range(50):
            res = await sim_0.execute_authorization(
                AuthorizeRequest(transaction_id=f"tx_{i}", amount=5.0)
            )
            assert res.authorized is False
            assert res.decline_code == "DO_NOT_HONOR"


class TestAcquirerSimulatorOutageDynamics:
    """Verify toggling outage changes authorize's behavior immediately."""

    @pytest.mark.asyncio
    async def test_toggling_outage_changes_behavior_immediately(
        self, fast_latency_config: LatencyConfig
    ) -> None:
        """Verify immediate drop to 0% on outage toggle and immediate restoration on clear."""
        # 100% success rate initially
        cfg = AcquirerConfig(
            acquirer_id="flapping_acquirer",
            base_success_rate=1.0,
            latency=fast_latency_config,
        )
        sim = AcquirerSimulator(config=cfg)

        # 1. Healthy operation
        res_before = await sim.execute_authorization(
            AuthorizeRequest(transaction_id="tx_healthy_1", amount=100.0)
        )
        assert res_before.authorized is True
        assert sim.effective_success_rate == 1.0

        # 2. Engage outage immediately
        sim.set_outage(active=True)
        assert bool(sim.is_outage_active) is True
        assert sim.effective_success_rate == 0.0

        # Immediate next request must be declined due to outage
        res_outage_1 = await sim.execute_authorization(
            AuthorizeRequest(transaction_id="tx_outage_1", amount=100.0)
        )
        assert res_outage_1.authorized is False
        assert res_outage_1.status == "DECLINED"
        assert res_outage_1.decline_code == "ACQUIRER_OUTAGE"

        # Subsequent requests during outage all decline with ACQUIRER_OUTAGE
        for i in range(25):
            res = await sim.execute_authorization(
                AuthorizeRequest(transaction_id=f"tx_during_outage_{i}", amount=10.0)
            )
            assert res.authorized is False
            assert res.decline_code == "ACQUIRER_OUTAGE"

        # 3. Disengage outage immediately
        sim.set_outage(active=False)
        assert bool(sim.is_outage_active) is False
        assert sim.effective_success_rate == 1.0

        # Immediate next request is authorized again
        res_recovered = await sim.execute_authorization(
            AuthorizeRequest(transaction_id="tx_recovered_1", amount=100.0)
        )
        assert res_recovered.authorized is True
        assert res_recovered.status == "AUTHORIZED"

    @pytest.mark.asyncio
    async def test_outage_with_http_503_behavior_raises_exception(
        self, fast_latency_config: LatencyConfig
    ) -> None:
        """Verify HTTP 503 outage behavior raises AcquirerOutageHttpException."""
        cfg = AcquirerConfig(acquirer_id="server_error_acquirer", latency=fast_latency_config)
        sim = AcquirerSimulator(config=cfg)

        sim.set_outage(active=True, behavior=OutageBehavior.HTTP_503)

        with pytest.raises(AcquirerOutageHttpException) as exc_info:
            await sim.execute_authorization(AuthorizeRequest(transaction_id="tx_err", amount=10.0))

        assert exc_info.value.acquirer_id == "server_error_acquirer"
        assert "operational outage (HTTP 503)" in exc_info.value.message

    def test_gradual_curve_in_v1_raises_value_error(
        self, default_simulator: AcquirerSimulator
    ) -> None:
        """Verify gradual transition_seconds > 0 raises ValueError in v1."""
        with pytest.raises(ValueError, match="Gradual transition curves not supported in v1"):
            default_simulator.set_outage(active=True, transition_seconds=10.0)


class TestMultiAcquirerRegistryIsolation:
    """Verify multiple acquirer instances remain strictly isolated."""

    @pytest.mark.asyncio
    async def test_outage_on_one_acquirer_does_not_affect_others(
        self, fast_latency_config: LatencyConfig
    ) -> None:
        """Verify engaging an outage on Acquirer Alpha leaves Acquirer Beta healthy."""
        registry = MultiAcquirerSimulator(
            default_acquirers=["alpha", "beta"],
            default_base_rate=1.0,
            default_latency=fast_latency_config,
        )

        sim_alpha = registry.get("alpha")
        sim_beta = registry.get("beta")
        assert sim_alpha is not None
        assert sim_beta is not None

        # Engage outage only on Alpha
        sim_alpha.set_outage(active=True)

        res_alpha = await sim_alpha.execute_authorization(
            AuthorizeRequest(transaction_id="tx_a", amount=10.0)
        )
        assert res_alpha.authorized is False
        assert res_alpha.decline_code == "ACQUIRER_OUTAGE"

        # Beta must remain 100% authorized
        res_beta = await sim_beta.execute_authorization(
            AuthorizeRequest(transaction_id="tx_b", amount=10.0)
        )
        assert res_beta.authorized is True
        assert res_beta.status == "AUTHORIZED"

    def test_reset_clears_counters(self, default_simulator: AcquirerSimulator) -> None:
        """Verify reset clears counters and restores default state."""
        default_simulator.set_outage(active=True)
        default_simulator.set_success_rate(0.50)

        reset_rep = default_simulator.reset()
        assert reset_rep.acquirer_id == "test_acquirer"

        snapshot = default_simulator.get_telemetry_snapshot()
        assert snapshot.total_requests == 0
        assert snapshot.outage_active is False
        assert snapshot.base_success_rate == 0.80
