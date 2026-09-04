"""Unit tests for synthetic transaction generator and orchestration utilities."""

from __future__ import annotations

import time

import pytest

from acquirer_sim.models import AuthorizeRequest
from router_core.models import RoutingResult
from router_core.state import AcquirerStateSnapshot
from scripts.generate_transactions import GeneratorMetrics, TransactionGenerator


class DummyRouter:
    """Mock router implementing route() for generator tests without HTTP overhead."""

    def __init__(self) -> None:
        self.call_count = 0

    async def route(self, request: AuthorizeRequest) -> RoutingResult:
        self.call_count += 1
        return RoutingResult(
            transaction_id=request.transaction_id,
            selected_acquirer="acquirer_alpha",
            thompson_samples={"acquirer_alpha": 0.90},
            status="AUTHORIZED",
            authorized=True,
            success=True,
            routing_latency_ms=0.05,
            acquirer_latency_ms=1.0,
            total_latency_ms=1.05,
            state_snapshot=AcquirerStateSnapshot(
                acquirer_id="acquirer_alpha",
                alpha=2.0,
                beta=1.0,
                health_score=1.0,
                success_count=1,
                failure_count=0,
                total_count=1,
                last_updated_at=time.time(),
            ),
            timestamp=time.time(),
        )


class TestTransactionGenerator:
    """Unit test suite for transaction generator configuration and pacing."""

    def test_invalid_parameters_raise(self) -> None:
        """Verify invalid tps or distribution raises ValueError."""
        with pytest.raises(ValueError, match=r"tps must be > 0.0"):
            TransactionGenerator(tps=0.0, target_url="http://localhost:8000/route")

        with pytest.raises(ValueError, match="distribution must be 'fixed' or 'poisson'"):
            TransactionGenerator(
                tps=10.0, distribution="invalid", target_url="http://localhost:8000/route"
            )

        with pytest.raises(
            ValueError, match="Must provide either target_url or an in-process router"
        ):
            TransactionGenerator(tps=10.0)

    def test_payload_generation_conforms_to_schema(self) -> None:
        """Verify generated payloads adhere strictly to AuthorizeRequest."""
        gen = TransactionGenerator(tps=10.0, target_url="http://localhost:8000/route", seed=42)
        payload = gen.generate_payload()

        assert payload.transaction_id.startswith("tx_")
        assert payload.amount > 0.0
        assert payload.currency == "USD"
        assert payload.payment_method == "card"
        assert payload.merchant_id == "merchant_loom_default"

    def test_delay_calculation(self) -> None:
        """Verify fixed and Poisson inter-arrival delays."""
        gen_fixed = TransactionGenerator(tps=50.0, distribution="fixed", target_url="http://mock")
        assert gen_fixed._next_delay() == pytest.approx(0.02)

        gen_poisson = TransactionGenerator(
            tps=50.0, distribution="poisson", target_url="http://mock", seed=10
        )
        delays = [gen_poisson._next_delay() for _ in range(100)]
        assert all(d > 0.0 for d in delays)
        avg_delay = sum(delays) / len(delays)
        # Expected average delay for Exponential(50) is 1/50 = 0.02
        assert avg_delay == pytest.approx(0.02, rel=0.30)

    @pytest.mark.asyncio
    async def test_in_process_execution_with_count(self) -> None:
        """Verify in-process generator runs for exact count specified."""
        dummy = DummyRouter()
        gen = TransactionGenerator(
            tps=200.0,  # Fast pacing for test speed
            router=dummy,  # type: ignore[arg-type]
            max_count=25,
            concurrency=5,
        )

        metrics = await gen.run(report_interval_sec=0.1)
        assert metrics.total_emitted == 25
        assert metrics.total_authorized == 25
        assert metrics.total_declined == 0
        assert dummy.call_count == 25

        summary = metrics.get_summary()
        assert summary["lifetime_psr"] == 100.0
        assert summary["allocations"]["acquirer_alpha"] == 100.0
        assert summary["p50_ms"] > 0.0


class TestGeneratorMetrics:
    """Unit test suite for telemetry calculations and rolling window metrics."""

    def test_metrics_recording_and_percentiles(self) -> None:
        """Verify metrics correctly aggregates outcomes and computes percentiles."""
        metrics = GeneratorMetrics(rolling_window_size=10)

        for i in range(10):
            res = RoutingResult(
                transaction_id=f"tx_{i}",
                selected_acquirer="acquirer_alpha" if i < 8 else "acquirer_beta",
                thompson_samples={"acquirer_alpha": 0.9},
                status="AUTHORIZED" if i % 2 == 0 else "DECLINED",
                authorized=(i % 2 == 0),
                success=(i % 2 == 0),
                routing_latency_ms=0.05,
                acquirer_latency_ms=float(i * 10),
                total_latency_ms=float(i * 10),
                state_snapshot=AcquirerStateSnapshot(
                    acquirer_id="acquirer_alpha",
                    alpha=1.0,
                    beta=1.0,
                    health_score=1.0,
                    success_count=1,
                    failure_count=0,
                    total_count=1,
                    last_updated_at=time.time(),
                ),
                timestamp=time.time(),
            )
            metrics.record_result(res)

        assert metrics.total_emitted == 10
        assert metrics.total_authorized == 5
        assert metrics.total_declined == 5
        assert metrics.route_counts["acquirer_alpha"] == 8
        assert metrics.route_counts["acquirer_beta"] == 2

        summary = metrics.get_summary()
        assert summary["lifetime_psr"] == 50.0
        assert summary["rolling_psr"] == 50.0
        assert summary["allocations"]["acquirer_alpha"] == 80.0
        assert summary["allocations"]["acquirer_beta"] == 20.0
