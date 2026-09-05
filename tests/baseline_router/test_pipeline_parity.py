"""Pipeline and contract parity tests for static baseline router.

Confirms:
1. HTTP Contract Parity: Interacts with Phase 2 acquirer_sim via AuthorizeRequest/AuthorizeResponse.
2. Logging Parity: Emits RoutingResult envelopes that write cleanly to Phase 5 SQLite schema.
3. Metric Query Parity: SQLiteMetricsStore.get_psr_metrics() computes exact matching PSR
   from transactions table.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import httpx
import pytest

from acquirer_sim.app import create_app
from acquirer_sim.models import AuthorizeRequest, LatencyConfig
from baseline_router.models import (
    BaselineRouterConfig,
    FailoverPolicyConfig,
)
from baseline_router.router import StaticBaselineRouter
from data_layer.sqlite_logger import SQLiteMetricsStore
from router_core.models import AcquirerRouteConfig


@pytest.mark.asyncio
async def test_baseline_router_sqlite_logging_parity() -> None:
    """Verify StaticBaselineRouter logs to SQLite schema with exact contract parity."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        db_path = str(Path(tmp_dir) / "test_baseline.db")
        with SQLiteMetricsStore(db_path=db_path) as metrics_store:
            sim_app = create_app(
                default_acquirers=["acquirer_alpha", "acquirer_beta"],
                default_base_rate=1.0,
                default_latency=LatencyConfig(base_ms=0.0, jitter_ms=0.0),
                seed=42,
            )

            transport = httpx.ASGITransport(app=sim_app)
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                routes = [
                    AcquirerRouteConfig(acquirer_id="acquirer_alpha", base_url="http://testserver"),
                    AcquirerRouteConfig(acquirer_id="acquirer_beta", base_url="http://testserver"),
                ]
                config = BaselineRouterConfig(
                    routes=routes,
                    priority_order=["acquirer_alpha", "acquirer_beta"],
                    failover_policy=FailoverPolicyConfig(consecutive_failure_threshold=3),
                )
                router = StaticBaselineRouter(
                    config=config,
                    http_client=client,
                    metrics_logger=metrics_store,
                )

                # Route 10 transactions
                for i in range(1, 11):
                    req = AuthorizeRequest(transaction_id=f"tx_parity_{i}", amount=100.0)
                    res = await router.route(req)
                    assert res.authorized is True
                    assert res.selected_acquirer == "acquirer_alpha"

            # Verify SQLite transactions table contents
            psr_metrics = metrics_store.get_psr_metrics()
            assert psr_metrics["total_transactions"] == 10
            assert psr_metrics["authorized_count"] == 10
            assert psr_metrics["declined_count"] == 0
            assert psr_metrics["error_count"] == 0
            assert psr_metrics["psr"] == 1.0

            # Verify acquirer_outcomes table
            alpha_metrics = metrics_store.get_psr_metrics(acquirer_id="acquirer_alpha")
            assert alpha_metrics["total_transactions"] == 10
            assert alpha_metrics["authorized_count"] == 10
            assert alpha_metrics["psr"] == 1.0
