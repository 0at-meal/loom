"""Unit tests for BanditRouter logic, outcome classification, and state feedback."""

from __future__ import annotations

import time

import httpx
import numpy as np
import pytest
from starlette.testclient import TestClient

from acquirer_sim.models import AuthorizeRequest, AuthorizeResponse
from router_core.app import create_router_app
from router_core.models import AcquirerRouteConfig, RouterConfig
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig


def make_test_router_config() -> RouterConfig:
    """Helper to create a standard dual-acquirer router config for unit tests."""
    return RouterConfig(
        routes=[
            AcquirerRouteConfig(
                acquirer_id="acquirer_alpha",
                base_url="http://mock-acquirer:8001",
                state_config=AcquirerStateConfig(
                    alpha_prior=1.0, beta_prior=1.0, decay_factor=0.90
                ),
            ),
            AcquirerRouteConfig(
                acquirer_id="acquirer_beta",
                base_url="http://mock-acquirer:8001",
                state_config=AcquirerStateConfig(
                    alpha_prior=1.0, beta_prior=1.0, decay_factor=0.90
                ),
            ),
        ],
        seed=42,
    )


class TestBanditRouterSelection:
    """Unit tests verifying Thompson Sampling argmax selection and tie-breaking."""

    def test_select_route_picks_highest_sample(self) -> None:
        """Verify select_route picks the arm with the maximum drawn sample."""
        config = make_test_router_config()
        router = BanditRouter(config=config, rng=np.random.default_rng(123))

        selected, samples = router.select_route()
        assert selected in ("acquirer_alpha", "acquirer_beta")
        assert samples[selected] == max(samples.values())

    def test_deterministic_tie_breaking(self) -> None:
        """Verify deterministic tie-breaking by acquirer_id when samples match."""
        config = make_test_router_config()
        router = BanditRouter(config=config)

        # Mock sample_all to return identical values
        router._registry.sample_all = lambda rng=None: {  # type: ignore[method-assign]
            "acquirer_beta": 0.85,
            "acquirer_alpha": 0.85,
        }

        selected, _samples = router.select_route()
        # In sorted/max tie-break with key (sample, id): 'acquirer_beta' > 'acquirer_alpha'
        assert selected == "acquirer_beta"


@pytest.mark.asyncio
class TestBanditRouterExecutionPipeline:
    """Unit tests verifying HTTP outcome handling and state feedback loops."""

    async def test_successful_authorization_updates_alpha(self) -> None:
        """Verify HTTP 200 authorized updates alpha on the selected acquirer."""
        config = make_test_router_config()

        # Mock HTTP transport returning 200 AUTHORIZED
        def handler(request: httpx.Request) -> httpx.Response:
            resp_payload = AuthorizeResponse(
                transaction_id="tx_test_1",
                acquirer_id="acquirer_alpha",
                status="AUTHORIZED",
                authorized=True,
                authorization_code="AUTH_123",
                simulated_latency_ms=10.0,
                timestamp=time.time(),
            )
            return httpx.Response(200, json=resp_payload.model_dump())

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        router = BanditRouter(config=config, http_client=mock_client)

        # Force alpha selection
        router._registry.sample_all = lambda rng=None: {  # type: ignore[method-assign]
            "acquirer_alpha": 0.99,
            "acquirer_beta": 0.10,
        }

        req = AuthorizeRequest(transaction_id="tx_test_1", amount=50.0)
        result = await router.route(req)

        assert result.selected_acquirer == "acquirer_alpha"
        assert result.status == "AUTHORIZED"
        assert result.authorized is True
        assert result.success is True
        assert result.error_message is None

        # Verify state update on alpha: alpha = 1.0 + 0.9*(1.0-1.0) + 1.0 = 2.0
        alpha_snap = router.get_state("acquirer_alpha")
        assert alpha_snap.alpha == pytest.approx(2.0)
        assert alpha_snap.beta == pytest.approx(1.0)
        assert alpha_snap.success_count == 1
        assert alpha_snap.total_count == 1

        # Verify untouched beta
        beta_snap = router.get_state("acquirer_beta")
        assert beta_snap.alpha == pytest.approx(1.0)
        assert beta_snap.beta == pytest.approx(1.0)
        assert beta_snap.total_count == 0

        await router.close()

    async def test_declined_authorization_updates_beta(self) -> None:
        """Verify HTTP 200 declined updates beta on the selected acquirer."""
        config = make_test_router_config()

        def handler(request: httpx.Request) -> httpx.Response:
            resp_payload = AuthorizeResponse(
                transaction_id="tx_test_2",
                acquirer_id="acquirer_alpha",
                status="DECLINED",
                authorized=False,
                decline_code="DO_NOT_HONOR",
                simulated_latency_ms=10.0,
                timestamp=time.time(),
            )
            return httpx.Response(200, json=resp_payload.model_dump())

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        router = BanditRouter(config=config, http_client=mock_client)

        router._registry.sample_all = lambda rng=None: {  # type: ignore[method-assign]
            "acquirer_alpha": 0.99,
            "acquirer_beta": 0.10,
        }

        req = AuthorizeRequest(transaction_id="tx_test_2", amount=75.0)
        result = await router.route(req)

        assert result.selected_acquirer == "acquirer_alpha"
        assert result.status == "DECLINED"
        assert result.authorized is False
        assert result.success is False

        # Verify state update: beta = 1.0 + 0.9*(1.0-1.0) + 1.0 = 2.0
        alpha_snap = router.get_state("acquirer_alpha")
        assert alpha_snap.alpha == pytest.approx(1.0)
        assert alpha_snap.beta == pytest.approx(2.0)
        assert alpha_snap.failure_count == 1
        assert alpha_snap.health_score == pytest.approx(0.90)  # 0.90 * 1.0 + 0.10 * 0.0

        await router.close()

    async def test_http_503_records_failure(self) -> None:
        """Verify HTTP 503 gateway outage records failure and updates beta."""
        config = make_test_router_config()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(503, text="Service Unavailable")

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        router = BanditRouter(config=config, http_client=mock_client)

        router._registry.sample_all = lambda rng=None: {  # type: ignore[method-assign]
            "acquirer_alpha": 0.95,
            "acquirer_beta": 0.05,
        }

        req = AuthorizeRequest(transaction_id="tx_test_3", amount=100.0)
        result = await router.route(req)

        assert result.status == "ERROR"
        assert result.authorized is False
        assert result.success is False
        assert "HTTP 503" in (result.error_message or "")

        alpha_snap = router.get_state("acquirer_alpha")
        assert alpha_snap.beta == pytest.approx(2.0)
        assert alpha_snap.failure_count == 1

        await router.close()

    async def test_transport_timeout_records_failure(self) -> None:
        """Verify network timeout records failure without crashing the router."""
        config = make_test_router_config()

        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.TimeoutException("Connection timed out")

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        router = BanditRouter(config=config, http_client=mock_client)

        router._registry.sample_all = lambda rng=None: {  # type: ignore[method-assign]
            "acquirer_alpha": 0.95,
            "acquirer_beta": 0.05,
        }

        req = AuthorizeRequest(transaction_id="tx_test_4", amount=25.0)
        result = await router.route(req)

        assert result.status == "ERROR"
        assert result.authorized is False
        assert result.success is False
        assert "TimeoutException" in (result.error_message or "")

        alpha_snap = router.get_state("acquirer_alpha")
        assert alpha_snap.beta == pytest.approx(2.0)
        assert alpha_snap.failure_count == 1

        await router.close()

    async def test_http_422_raises_without_penalizing_state(self) -> None:
        """Verify HTTP 422 schema rejection raises ValueError without mutating bandit belief."""
        config = make_test_router_config()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(422, text="Unprocessable Entity")

        mock_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        router = BanditRouter(config=config, http_client=mock_client)

        router._registry.sample_all = lambda rng=None: {  # type: ignore[method-assign]
            "acquirer_alpha": 0.95,
            "acquirer_beta": 0.05,
        }

        req = AuthorizeRequest(transaction_id="tx_test_5", amount=10.0)
        with pytest.raises(ValueError, match="HTTP 422"):
            await router.route(req)

        # Verify acquirer state was NOT penalized
        alpha_snap = router.get_state("acquirer_alpha")
        assert alpha_snap.alpha == pytest.approx(1.0)
        assert alpha_snap.beta == pytest.approx(1.0)
        assert alpha_snap.total_count == 0

        await router.close()

    async def test_async_context_manager_lifecycle(self) -> None:
        """Verify BanditRouter works cleanly within async with block."""
        config = make_test_router_config()
        async with BanditRouter(config=config) as router:
            assert router._client is not None
            assert router.list_acquirer_ids() == ["acquirer_alpha", "acquirer_beta"]
        assert router._client is None


class TestRouterFastAPIApp:
    """Integration tests for the router's FastAPI service daemon."""

    def test_app_health_and_state_endpoints(self) -> None:
        """Verify /health and /state endpoints return expected metadata."""
        config = make_test_router_config()
        router = BanditRouter(config=config)
        app = create_router_app(router=router)

        with TestClient(app) as client:
            resp = client.get("/health")
            assert resp.status_code == 200
            assert resp.json()["status"] == "healthy"
            assert "acquirer_alpha" in resp.json()["registered_acquirers"]

            resp = client.get("/state")
            assert resp.status_code == 200
            states = resp.json()
            assert "acquirer_alpha" in states
            assert states["acquirer_alpha"]["health_score"] == 1.0

            resp = client.get("/state/acquirer_alpha")
            assert resp.status_code == 200
            assert resp.json()["acquirer_id"] == "acquirer_alpha"

            resp = client.get("/state/unknown_route")
            assert resp.status_code == 404
