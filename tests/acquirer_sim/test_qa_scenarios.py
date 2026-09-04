"""QA verification suite: Scriptability, mid-run outage dynamics, and edge cases."""

import asyncio
import math
from collections.abc import AsyncIterator

import httpx
import numpy as np
import pytest

from acquirer_sim.app import create_app
from acquirer_sim.models import (
    AcquirerConfig,
    AuthorizeRequest,
    LatencyConfig,
)
from acquirer_sim.simulator import AcquirerSimulator


@pytest.fixture
def qa_latency_zero() -> LatencyConfig:
    """Zero latency configuration for high-velocity deterministic QA testing."""
    return LatencyConfig(base_ms=0.0, jitter_ms=0.0, outage_spike_ms=0.0)


@pytest.fixture
async def qa_client() -> AsyncIterator[httpx.AsyncClient]:
    """Test client configured for QA scenario testing with zero delay."""
    app = create_app(
        default_acquirers=["alpha", "beta", "gamma"],
        default_base_rate=0.95,
        default_latency=LatencyConfig(base_ms=0.0, jitter_ms=0.0, outage_spike_ms=0.0),
        seed=10001,
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


class TestQAScriptableSuccessRates:
    """Verify scriptability: setting rates and confirming statistical convergence."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("target_rate", [0.25, 0.50, 0.70, 0.90])
    async def test_statistical_convergence_across_spectrum(
        self, target_rate: float, qa_latency_zero: LatencyConfig
    ) -> None:
        """Verify empirical PSR matches configured rate within 3-sigma binomial confidence."""
        cfg = AcquirerConfig(
            acquirer_id=f"acq_rate_{int(target_rate * 100)}",
            base_success_rate=target_rate,
            latency=qa_latency_zero,
        )
        sim = AcquirerSimulator(config=cfg, rng=np.random.default_rng(seed=42))

        n_trials = 2500
        successes = 0

        for i in range(n_trials):
            req = AuthorizeRequest(transaction_id=f"tx_conv_{i}", amount=10.0)
            res = await sim.execute_authorization(req)
            if res.authorized:
                successes += 1

        empirical_rate = successes / n_trials

        # Binomial distribution standard deviation: sigma = sqrt(p * (1 - p) / N)
        sigma = math.sqrt((target_rate * (1.0 - target_rate)) / n_trials)
        margin = 3.0 * sigma  # 99.73% statistical confidence interval

        lower_bound = max(0.0, target_rate - margin)
        upper_bound = min(1.0, target_rate + margin)

        assert lower_bound <= empirical_rate <= upper_bound, (
            f"Target {target_rate}: Empirical rate {empirical_rate:.4f} outside 3-sigma "
            f"[{lower_bound:.4f}, {upper_bound:.4f}]"
        )

        snapshot = sim.get_telemetry_snapshot()
        assert snapshot.total_requests == n_trials
        assert snapshot.authorized_count == successes
        assert snapshot.declined_count == n_trials - successes

    @pytest.mark.asyncio
    async def test_scriptable_rate_mutation_mid_stream_via_api(
        self, qa_client: httpx.AsyncClient
    ) -> None:
        """Verify modifying success rate via API immediately alters future outcomes."""
        acquirer_id = "alpha"

        # Phase A: Set rate to 1.0 -> 50 calls must all succeed
        await qa_client.post(
            f"/acquirers/{acquirer_id}/admin/success-rate",
            json={"success_rate": 1.0},
        )
        for i in range(50):
            res = await qa_client.post(
                f"/acquirers/{acquirer_id}/authorize",
                json={"transaction_id": f"tx_step_a_{i}", "amount": 10.0},
            )
            assert res.json()["authorized"] is True

        # Phase B: Set rate to 0.0 -> 50 calls must all fail
        await qa_client.post(
            f"/acquirers/{acquirer_id}/admin/success-rate",
            json={"success_rate": 0.0},
        )
        for i in range(50):
            res = await qa_client.post(
                f"/acquirers/{acquirer_id}/authorize",
                json={"transaction_id": f"tx_step_b_{i}", "amount": 10.0},
            )
            assert res.json()["authorized"] is False
            assert res.json()["decline_code"] == "DO_NOT_HONOR"

        # Telemetry should reflect 50 authorized out of 100 total
        state_res = await qa_client.get(f"/acquirers/{acquirer_id}/admin/state")
        state = state_res.json()
        assert state["total_requests"] == 100
        assert state["authorized_count"] == 50
        assert state["declined_count"] == 50
        assert state["empirical_success_rate"] == 0.50


class TestQAMidRunOutageDynamics:
    """Verify outage triggering mid-run alters behavior immediately on the next call."""

    @pytest.mark.asyncio
    async def test_mid_run_outage_step_function_with_zero_delay(
        self, qa_latency_zero: LatencyConfig
    ) -> None:
        """Verify behavior transitions on step N+1 without trailing lag or decay delay."""
        cfg = AcquirerConfig(
            acquirer_id="mid_run_acq",
            base_success_rate=1.0,  # 100% success when healthy
            latency=qa_latency_zero,
        )
        sim = AcquirerSimulator(config=cfg)

        # 1. First 50 calls are healthy
        for i in range(50):
            res = await sim.execute_authorization(
                AuthorizeRequest(transaction_id=f"tx_pre_{i}", amount=10.0)
            )
            assert res.authorized is True

        # 2. Trigger Outage mid-run
        sim.set_outage(active=True)

        # 3. Call 51 MUST DECLINE IMMEDIATELY
        call_51 = await sim.execute_authorization(
            AuthorizeRequest(transaction_id="tx_mid_51", amount=10.0)
        )
        assert call_51.authorized is False, "Outage did not take effect on the very next call!"
        assert call_51.status == "DECLINED"
        assert call_51.decline_code == "ACQUIRER_OUTAGE"

        # 4. Next 49 calls (52 to 100) all fail under outage
        for i in range(52, 101):
            res = await sim.execute_authorization(
                AuthorizeRequest(transaction_id=f"tx_out_{i}", amount=10.0)
            )
            assert res.authorized is False
            assert res.decline_code == "ACQUIRER_OUTAGE"

        # 5. Clear Outage mid-run
        sim.set_outage(active=False)

        # 6. Call 101 MUST SUCCEED IMMEDIATELY
        call_101 = await sim.execute_authorization(
            AuthorizeRequest(transaction_id="tx_post_101", amount=10.0)
        )
        assert call_101.authorized is True, "Recovery did not restore on the very next call!"
        assert call_101.status == "AUTHORIZED"

        # 7. Telemetry validation
        snapshot = sim.get_telemetry_snapshot()
        assert snapshot.total_requests == 101
        assert snapshot.authorized_count == 51  # 50 pre + 1 post
        assert snapshot.declined_count == 50  # 50 during outage
        assert snapshot.outage_declines == 50


class TestQAEdgeCases:
    """Stress testing boundary conditions and edge cases."""

    @pytest.mark.asyncio
    async def test_edge_case_zero_percent_rate(self, qa_latency_zero: LatencyConfig) -> None:
        """Verify success_rate=0.0 produces exactly 0% successes across 100 trials."""
        sim = AcquirerSimulator(
            config=AcquirerConfig(
                acquirer_id="zero_rate",
                base_success_rate=0.0,
                latency=qa_latency_zero,
            )
        )
        for i in range(100):
            res = await sim.execute_authorization(
                AuthorizeRequest(transaction_id=f"tx_zero_{i}", amount=1.0)
            )
            assert res.authorized is False
            assert res.decline_code == "DO_NOT_HONOR"

        snapshot = sim.get_telemetry_snapshot()
        assert snapshot.authorized_count == 0
        assert snapshot.declined_count == 100
        assert snapshot.empirical_success_rate == 0.0

    @pytest.mark.asyncio
    async def test_edge_case_hundred_percent_rate(self, qa_latency_zero: LatencyConfig) -> None:
        """Verify success_rate=1.0 produces exactly 100% successes across 100 trials."""
        sim = AcquirerSimulator(
            config=AcquirerConfig(
                acquirer_id="hundred_rate",
                base_success_rate=1.0,
                latency=qa_latency_zero,
            )
        )
        for i in range(100):
            res = await sim.execute_authorization(
                AuthorizeRequest(transaction_id=f"tx_full_{i}", amount=1.0)
            )
            assert res.authorized is True
            assert res.status == "AUTHORIZED"
            assert res.authorization_code is not None

        snapshot = sim.get_telemetry_snapshot()
        assert snapshot.authorized_count == 100
        assert snapshot.declined_count == 0
        assert snapshot.empirical_success_rate == 1.0

    @pytest.mark.asyncio
    async def test_edge_case_toggle_on_then_immediately_off(
        self, qa_latency_zero: LatencyConfig
    ) -> None:
        """Verify toggling outage ON and immediately OFF results in a healthy route."""
        sim = AcquirerSimulator(
            config=AcquirerConfig(
                acquirer_id="blip_acq",
                base_success_rate=1.0,
                latency=qa_latency_zero,
            )
        )

        # Flip ON and immediately OFF with zero intervening requests
        sim.set_outage(active=True)
        assert bool(sim.is_outage_active) is True
        sim.set_outage(active=False)
        assert bool(sim.is_outage_active) is False

        # Next call must succeed without residual outage penalty
        res = await sim.execute_authorization(
            AuthorizeRequest(transaction_id="tx_blip_1", amount=25.0)
        )
        assert res.authorized is True
        assert res.status == "AUTHORIZED"

        # Reverse flip: OFF and immediately ON
        sim.set_outage(active=False)
        sim.set_outage(active=True)
        assert bool(sim.is_outage_active) is True

        res_declined = await sim.execute_authorization(
            AuthorizeRequest(transaction_id="tx_blip_2", amount=25.0)
        )
        assert res_declined.authorized is False
        assert res_declined.decline_code == "ACQUIRER_OUTAGE"

    @pytest.mark.asyncio
    async def test_edge_case_high_frequency_flapping(self, qa_latency_zero: LatencyConfig) -> None:
        """Verify rapid alternating outage toggle on every single transaction."""
        sim = AcquirerSimulator(
            config=AcquirerConfig(
                acquirer_id="flapping_acq",
                base_success_rate=1.0,
                latency=qa_latency_zero,
            )
        )

        for i in range(40):
            is_outage = i % 2 == 1
            sim.set_outage(active=is_outage)

            res = await sim.execute_authorization(
                AuthorizeRequest(transaction_id=f"tx_flap_{i}", amount=10.0)
            )
            if is_outage:
                assert res.authorized is False
                assert res.decline_code == "ACQUIRER_OUTAGE"
            else:
                assert res.authorized is True

        snapshot = sim.get_telemetry_snapshot()
        assert snapshot.total_requests == 40
        assert snapshot.authorized_count == 20
        assert snapshot.declined_count == 20
        assert snapshot.outage_declines == 20

    @pytest.mark.asyncio
    async def test_concurrent_request_counter_consistency(
        self, qa_latency_zero: LatencyConfig
    ) -> None:
        """Verify counter integrity under concurrent asynchronous authorization execution."""
        sim = AcquirerSimulator(
            config=AcquirerConfig(
                acquirer_id="concurrent_acq",
                base_success_rate=0.75,
                latency=qa_latency_zero,
            ),
            rng=np.random.default_rng(999),
        )

        total_concurrent = 100

        async def worker(index: int) -> None:
            await sim.execute_authorization(
                AuthorizeRequest(transaction_id=f"tx_conc_{index}", amount=15.0)
            )

        await asyncio.gather(*(worker(i) for i in range(total_concurrent)))

        snapshot = sim.get_telemetry_snapshot()
        assert snapshot.total_requests == total_concurrent
        assert snapshot.authorized_count + snapshot.declined_count == total_concurrent
