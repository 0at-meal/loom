"""Integration tests for simulated acquirer FastAPI application using httpx.AsyncClient."""

from collections.abc import AsyncIterator

import httpx
import pytest

from acquirer_sim.app import create_app
from acquirer_sim.models import LatencyConfig


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    """Create test client with zero-latency simulation and deterministic seed."""
    app = create_app(
        default_acquirers=["alpha", "beta", "gamma"],
        default_base_rate=0.85,
        default_latency=LatencyConfig(base_ms=0.0, jitter_ms=0.0, outage_spike_ms=0.0),
        seed=999,
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as http_client:
        yield http_client


class TestApiAuthorizationAndTracking:
    """Test authorize endpoint behaviors through HTTP API."""

    @pytest.mark.asyncio
    async def test_authorize_tracks_configured_success_rate_over_many_calls(
        self, client: httpx.AsyncClient
    ) -> None:
        """Verify API authorizations converge to the configured 85% rate."""
        trials = 500
        successes = 0

        for i in range(trials):
            payload = {
                "transaction_id": f"tx_api_{i}",
                "amount": 50.0,
                "currency": "USD",
            }
            res = await client.post("/acquirers/alpha/authorize", json=payload)
            assert res.status_code == 200
            data = res.json()
            assert data["transaction_id"] == f"tx_api_{i}"
            assert data["acquirer_id"] == "alpha"
            if data["authorized"] is True:
                successes += 1
                assert data["status"] == "AUTHORIZED"
            else:
                assert data["status"] == "DECLINED"
                assert data["decline_code"] == "DO_NOT_HONOR"

        empirical_rate = successes / trials
        # 85% over 500 trials is expected within [0.80, 0.90]
        assert 0.80 <= empirical_rate <= 0.90

        # Check telemetry endpoint confirms the exact counts
        state_res = await client.get("/acquirers/alpha/admin/state")
        assert state_res.status_code == 200
        state = state_res.json()
        assert state["total_requests"] == trials
        assert state["authorized_count"] == successes
        assert state["declined_count"] == trials - successes

    @pytest.mark.asyncio
    async def test_shared_authorize_endpoint_with_header_and_body(
        self, client: httpx.AsyncClient
    ) -> None:
        """Verify shared /authorize routes correctly via request body and header."""
        # Route via body
        res_body = await client.post(
            "/authorize",
            json={
                "transaction_id": "tx_shared_1",
                "amount": 10.0,
                "acquirer_id": "beta",
            },
        )
        assert res_body.status_code == 200
        assert res_body.json()["acquirer_id"] == "beta"

        # Route via header
        res_header = await client.post(
            "/authorize",
            headers={"X-Acquirer-Id": "gamma"},
            json={
                "transaction_id": "tx_shared_2",
                "amount": 15.0,
            },
        )
        assert res_header.status_code == 200
        assert res_header.json()["acquirer_id"] == "gamma"


class TestApiOutageDynamics:
    """Test outage toggling dynamics via HTTP API."""

    @pytest.mark.asyncio
    async def test_outage_toggle_changes_authorize_behavior_immediately(
        self, client: httpx.AsyncClient
    ) -> None:
        """Verify immediate decline upon outage toggle, and immediate recovery on clear."""
        # 1. Ensure 100% rate initially for deterministic check
        await client.post(
            "/acquirers/alpha/admin/success-rate",
            json={"success_rate": 1.0},
        )

        res_before = await client.post(
            "/acquirers/alpha/authorize",
            json={"transaction_id": "tx_pre_outage", "amount": 100.0},
        )
        assert res_before.status_code == 200
        assert res_before.json()["authorized"] is True

        # 2. Toggle outage ON
        outage_res = await client.post(
            "/acquirers/alpha/admin/outage",
            json={"active": True, "behavior": "RETURN_DECLINE"},
        )
        assert outage_res.status_code == 200
        assert outage_res.json()["outage_active"] is True
        assert outage_res.json()["effective_success_rate"] == 0.0

        # Immediate next authorization must be declined due to outage
        res_during = await client.post(
            "/acquirers/alpha/authorize",
            json={"transaction_id": "tx_during_outage", "amount": 100.0},
        )
        assert res_during.status_code == 200
        data_during = res_during.json()
        assert data_during["authorized"] is False
        assert data_during["status"] == "DECLINED"
        assert data_during["decline_code"] == "ACQUIRER_OUTAGE"

        # 3. Toggle outage OFF
        clear_res = await client.post(
            "/acquirers/alpha/admin/outage",
            json={"active": False},
        )
        assert clear_res.status_code == 200
        assert clear_res.json()["outage_active"] is False
        assert clear_res.json()["effective_success_rate"] == 1.0

        # Immediate next authorization is successful again
        res_post = await client.post(
            "/acquirers/alpha/authorize",
            json={"transaction_id": "tx_post_outage", "amount": 100.0},
        )
        assert res_post.status_code == 200
        assert res_post.json()["authorized"] is True

    @pytest.mark.asyncio
    async def test_outage_http_503_behavior(self, client: httpx.AsyncClient) -> None:
        """Verify outage with HTTP_503 behavior returns HTTP 503 status."""
        await client.post(
            "/acquirers/alpha/admin/outage",
            json={"active": True, "behavior": "HTTP_503"},
        )

        res = await client.post(
            "/acquirers/alpha/authorize",
            json={"transaction_id": "tx_503", "amount": 50.0},
        )
        assert res.status_code == 503
        assert "operational outage (HTTP 503)" in res.json()["detail"]

        # Restore
        await client.post(
            "/acquirers/alpha/admin/outage",
            json={"active": False},
        )

    @pytest.mark.asyncio
    async def test_multi_acquirer_isolation_via_api(self, client: httpx.AsyncClient) -> None:
        """Verify outage on Alpha leaves Beta completely unaffected."""
        await client.post(
            "/acquirers/alpha/admin/success-rate",
            json={"success_rate": 1.0},
        )
        await client.post(
            "/acquirers/beta/admin/success-rate",
            json={"success_rate": 1.0},
        )

        # Outage on Alpha
        await client.post(
            "/acquirers/alpha/admin/outage",
            json={"active": True},
        )

        # Alpha declines
        res_alpha = await client.post(
            "/acquirers/alpha/authorize",
            json={"transaction_id": "tx_iso_a", "amount": 20.0},
        )
        assert res_alpha.json()["authorized"] is False

        # Beta succeeds
        res_beta = await client.post(
            "/acquirers/beta/authorize",
            json={"transaction_id": "tx_iso_b", "amount": 20.0},
        )
        assert res_beta.json()["authorized"] is True


class TestApiValidationAndAdminEndpoints:
    """Test validation and administrative endpoints."""

    @pytest.mark.asyncio
    async def test_invalid_payloads_return_422(self, client: httpx.AsyncClient) -> None:
        """Verify invalid inbound payloads return HTTP 422."""
        # Negative amount
        res_neg = await client.post(
            "/acquirers/alpha/authorize",
            json={"transaction_id": "tx_bad", "amount": -10.0},
        )
        assert res_neg.status_code == 422

        # Bad currency
        res_curr = await client.post(
            "/acquirers/alpha/authorize",
            json={"transaction_id": "tx_bad", "amount": 10.0, "currency": "invalid"},
        )
        assert res_curr.status_code == 422

        # Success rate out of bounds
        res_rate = await client.post(
            "/acquirers/alpha/admin/success-rate",
            json={"success_rate": 1.5},
        )
        assert res_rate.status_code == 422

        # Transition seconds > 0 rejected in v1
        res_gradual = await client.post(
            "/acquirers/alpha/admin/outage",
            json={"active": True, "transition_seconds": 5.0},
        )
        assert res_gradual.status_code == 422
        assert "Gradual transition curves not supported in v1" in res_gradual.json()["detail"]

    @pytest.mark.asyncio
    async def test_discovery_and_reset(self, client: httpx.AsyncClient) -> None:
        """Verify health, acquirer listing, and reset endpoints."""
        health = await client.get("/health")
        assert health.status_code == 200
        assert health.json()["status"] == "healthy"

        acquirers = await client.get("/acquirers")
        assert acquirers.status_code == 200
        assert "alpha" in acquirers.json()
        assert "beta" in acquirers.json()

        all_states = await client.get("/admin/states")
        assert all_states.status_code == 200
        assert all_states.json()["total_acquirers"] >= 3

        reset_res = await client.post("/admin/reset")
        assert reset_res.status_code == 200
        assert reset_res.json()["acquirer_id"] == "all"

    @pytest.mark.asyncio
    async def test_dynamic_registration_and_keyed_reset(self, client: httpx.AsyncClient) -> None:
        """Verify dynamic acquirer registration and keyed reset endpoints."""
        reg_res = await client.post(
            "/acquirers",
            json={
                "acquirer_id": "delta",
                "base_success_rate": 0.92,
            },
        )
        assert reg_res.status_code == 201
        assert reg_res.json()["acquirer_id"] == "delta"
        assert reg_res.json()["base_success_rate"] == 0.92

        # Duplicate registration raises 409
        dup_res = await client.post(
            "/acquirers",
            json={
                "acquirer_id": "delta",
                "base_success_rate": 0.92,
            },
        )
        assert dup_res.status_code == 409

        # Keyed reset
        reset_res = await client.post("/acquirers/delta/admin/reset")
        assert reset_res.status_code == 200
        assert reset_res.json()["acquirer_id"] == "delta"


class TestCliServerParsing:
    """Test CLI argument parsing for simulated acquirer server."""

    def test_parse_args_defaults(self) -> None:
        """Verify default CLI arguments."""
        from acquirer_sim.server import parse_args

        args = parse_args([])
        assert args.host == "127.0.0.1"
        assert args.port == 8001
        assert "acquirer_alpha" in args.acquirers
        assert args.base_rate == 0.95
        assert args.log_level == "info"
        assert args.reload is False

    def test_parse_args_custom(self) -> None:
        """Verify custom CLI arguments."""
        from acquirer_sim.server import parse_args

        args = parse_args(
            [
                "--host",
                "0.0.0.0",
                "--port",
                "9000",
                "--acquirers",
                "stripe",
                "adyen",
                "--base-rate",
                "0.80",
                "--log-level",
                "debug",
                "--reload",
            ]
        )
        assert args.host == "0.0.0.0"
        assert args.port == 9000
        assert args.acquirers == ["stripe", "adyen"]
        assert args.base_rate == 0.80
        assert args.log_level == "debug"
        assert args.reload is True
