"""Comprehensive test suite for Redis Pub/Sub telemetry and event streaming (Phase 5 Ticket B).

Validates:
1. RoutingEvent and HealthAlertEvent schema definitions, JSON serialization, and deserialization.
2. In-order reception and zero event drops under sustained transaction bursts.
3. Multi-subscriber fanout across independent subscriber instances.
4. Zero-subscriber safety (no memory buildup, no blocking, no errors).
5. Fault tolerance and error isolation (Redis failure never crashes payment routing).
6. End-to-end integration between BanditRouter and EventPublisher.
7. Asynchronous publisher and subscriber lifecycle (AsyncEventPublisher & AsyncEventSubscriber).
8. Channel namespacing and health alert broadcasting.
"""

from __future__ import annotations

import time
from typing import Literal
from unittest.mock import MagicMock

import fakeredis
import fakeredis.aioredis
import httpx
import pytest
import redis

from acquirer_sim.models import AuthorizeRequest, AuthorizeResponse
from data_layer.config import DataLayerConfig
from data_layer.models import HealthAlertEvent, RoutingEvent
from data_layer.redis_pubsub import (
    AsyncEventPublisher,
    AsyncEventSubscriber,
    EventPublisher,
    EventSubscriber,
)
from router_core.models import AcquirerRouteConfig, RouterConfig, RoutingResult
from router_core.pid import PIDConfig, PIDDiagnostics
from router_core.router import BanditRouter
from router_core.state import AcquirerStateConfig, AcquirerStateSnapshot


def _create_mock_routing_result(
    tx_id: str = "tx_test_001",
    acquirer_id: str = "acquirer_alpha",
    success: bool = True,
    smoothed_weight: float = 0.75,
) -> RoutingResult:
    """Helper to create a fully populated RoutingResult for tests."""
    status: Literal["AUTHORIZED", "DECLINED", "ERROR"] = "AUTHORIZED" if success else "DECLINED"
    snap = AcquirerStateSnapshot(
        acquirer_id=acquirer_id,
        alpha=2.5 if success else 1.0,
        beta=1.0 if success else 2.5,
        health_score=1.0 if success else 0.5,
        alpha_prior=1.0,
        beta_prior=1.0,
        success_count=1 if success else 0,
        failure_count=0 if success else 1,
        total_count=1,
        last_updated_at=time.time(),
    )
    payload = (
        AuthorizeResponse(
            authorized=True,
            status="AUTHORIZED",
            acquirer_id=acquirer_id,
            transaction_id=tx_id,
            simulated_latency_ms=18.5,
            timestamp=time.time(),
            decline_code=None,
        )
        if success
        else AuthorizeResponse(
            authorized=False,
            status="DECLINED",
            acquirer_id=acquirer_id,
            transaction_id=tx_id,
            simulated_latency_ms=15.0,
            timestamp=time.time(),
            decline_code="INSUFFICIENT_FUNDS",
        )
    )
    return RoutingResult(
        transaction_id=tx_id,
        selected_acquirer=acquirer_id,
        thompson_samples={"acquirer_alpha": 0.85, "acquirer_beta": 0.65},
        status=status,
        authorized=success,
        success=success,
        response_payload=payload,
        error_message=None if success else "Card declined",
        routing_latency_ms=0.12,
        acquirer_latency_ms=18.5,
        total_latency_ms=18.62,
        state_snapshot=snap,
        smoothed_allocation={
            "acquirer_alpha": smoothed_weight,
            "acquirer_beta": 1.0 - smoothed_weight,
        },
        target_allocation={"acquirer_alpha": 1.0, "acquirer_beta": 0.0},
        pid_diagnostics=PIDDiagnostics(
            error={"acquirer_alpha": 0.25, "acquirer_beta": -0.25},
            p_term={"acquirer_alpha": 0.05, "acquirer_beta": -0.05},
            i_term={"acquirer_alpha": 0.01, "acquirer_beta": -0.01},
            d_term={"acquirer_alpha": 0.0, "acquirer_beta": 0.0},
            raw_delta={"acquirer_alpha": 0.06, "acquirer_beta": -0.06},
            pre_projection_allocation={"acquirer_alpha": 0.76, "acquirer_beta": 0.24},
        ),
        timestamp=time.time(),
    )


class TestRoutingEventSchema:
    """Validates the Pydantic v2 domain schemas for telemetry."""

    def test_routing_event_from_routing_result_serialization(self) -> None:
        """Verify RoutingEvent captures all fields and serializes/deserializes cleanly."""
        res = _create_mock_routing_result(tx_id="tx_schema_01", success=True)
        event = RoutingEvent.from_routing_result(res, sequence_number=42)

        assert event.sequence_number == 42
        assert event.event_type == "ROUTING_COMPLETED"
        assert event.transaction_id == "tx_schema_01"
        assert event.selected_acquirer == "acquirer_alpha"
        assert event.status == "AUTHORIZED"
        assert event.authorized is True
        assert event.success is True
        assert event.decline_code is None
        assert event.allocation_weight == 0.75
        assert event.pid_diagnostics is not None
        assert "p_term" in event.pid_diagnostics
        assert event.updated_state["alpha"] == 2.5
        assert event.updated_state["health_score"] == 1.0

        # Verify JSON round-trip
        json_str = event.model_dump_json()
        deserialized = RoutingEvent.model_validate_json(json_str)
        assert deserialized == event

    def test_routing_event_decline_code_extraction(self) -> None:
        """Verify decline_code is properly mapped from AuthorizeResponse on failure."""
        res = _create_mock_routing_result(tx_id="tx_declined_01", success=False)
        event = RoutingEvent.from_routing_result(res, sequence_number=7)

        assert event.status == "DECLINED"
        assert event.authorized is False
        assert event.decline_code == "INSUFFICIENT_FUNDS"
        assert event.updated_state["beta"] == 2.5

    def test_health_alert_event_schema(self) -> None:
        """Verify HealthAlertEvent schema validation and JSON serialization."""
        alert = HealthAlertEvent(
            timestamp=1756973000.0,
            acquirer_id="acquirer_alpha",
            old_health=0.95,
            new_health=0.45,
            severity="CRITICAL",
            message="Acquirer alpha health collapsed to 0.450 due to 5xx error burst",
        )
        json_str = alert.model_dump_json()
        parsed = HealthAlertEvent.model_validate_json(json_str)
        assert parsed.event_type == "HEALTH_ALERT"
        assert parsed.severity == "CRITICAL"
        assert parsed.acquirer_id == "acquirer_alpha"
        assert parsed.old_health == 0.95
        assert parsed.new_health == 0.45


class TestRedisPubSubDelivery:
    """Validates real-time Redis Pub/Sub delivery, order preservation, and zero-drop guarantees."""

    @pytest.fixture
    def fake_server(self) -> fakeredis.FakeServer:
        """Shared in-memory Redis server for publisher and subscriber."""
        return fakeredis.FakeServer()

    def test_in_order_delivery_and_zero_drops_under_burst(
        self,
        fake_server: fakeredis.FakeServer,
    ) -> None:
        """Verify a subscriber receives every event in strict sequential order without drops."""
        client_pub = fakeredis.FakeRedis(server=fake_server, decode_responses=True)
        client_sub = fakeredis.FakeRedis(server=fake_server, decode_responses=True)

        publisher = EventPublisher(redis_client=client_pub, routing_channel="events:routing")
        subscriber = EventSubscriber(redis_client=client_sub, channels=["events:routing"])

        # Drain initial control subscription message
        _ = subscriber.get_event(timeout=0.05)

        num_events = 150
        # Publish 150 events in a rapid burst
        for i in range(1, num_events + 1):
            res = _create_mock_routing_result(tx_id=f"tx_burst_{i:04d}", success=(i % 3 != 0))
            pub_event = publisher.publish_routing_event(res)
            assert pub_event is not None
            assert pub_event.sequence_number == i

        # Consume all 150 events
        received_events: list[RoutingEvent] = []
        for _ in range(num_events):
            evt = subscriber.get_event(timeout=1.0)
            assert evt is not None, f"Dropped event at index {len(received_events) + 1}"
            received_events.append(evt)

        # Assert zero drops
        assert len(received_events) == num_events

        # Assert strict monotonic sequence order and content integrity
        for idx, evt in enumerate(received_events, start=1):
            assert evt.sequence_number == idx
            assert evt.transaction_id == f"tx_burst_{idx:04d}"
            expected_success = (idx % 3) != 0
            assert evt.success == expected_success

        # Ensure no residual events remain
        assert subscriber.get_event(timeout=0.05) is None

        subscriber.close()
        publisher.close()

    def test_multi_subscriber_fanout(
        self,
        fake_server: fakeredis.FakeServer,
    ) -> None:
        """Verify multiple independent subscribers all receive the full broadcast stream."""
        client_pub = fakeredis.FakeRedis(server=fake_server, decode_responses=True)
        client_sub1 = fakeredis.FakeRedis(server=fake_server, decode_responses=True)
        client_sub2 = fakeredis.FakeRedis(server=fake_server, decode_responses=True)

        publisher = EventPublisher(redis_client=client_pub, routing_channel="events:routing")
        sub1 = EventSubscriber(redis_client=client_sub1, channels=["events:routing"])
        sub2 = EventSubscriber(redis_client=client_sub2, channels=["events:routing"])

        # Drain subscribe messages
        _ = sub1.get_event(timeout=0.05)
        _ = sub2.get_event(timeout=0.05)

        num_events = 50
        for i in range(1, num_events + 1):
            res = _create_mock_routing_result(tx_id=f"tx_fanout_{i}")
            publisher.publish_routing_event(res)

        events_sub1 = sub1.drain(max_events=num_events, timeout=0.1)
        events_sub2 = sub2.drain(max_events=num_events, timeout=0.1)

        assert len(events_sub1) == num_events
        assert len(events_sub2) == num_events
        assert [e.sequence_number for e in events_sub1] == list(range(1, num_events + 1))
        assert [e.sequence_number for e in events_sub2] == list(range(1, num_events + 1))

        sub1.close()
        sub2.close()
        publisher.close()

    def test_zero_subscribers_safety(
        self,
        fake_server: fakeredis.FakeServer,
    ) -> None:
        """Verify publishing with zero active subscribers succeeds with no errors or leaks."""
        client_pub = fakeredis.FakeRedis(server=fake_server, decode_responses=True)
        publisher = EventPublisher(redis_client=client_pub, routing_channel="events:routing")

        res = _create_mock_routing_result(tx_id="tx_zero_subs")
        published = publisher.publish_routing_event(res)

        assert published is not None
        assert published.sequence_number == 1
        assert publisher.sequence_number == 1
        publisher.close()

    def test_error_isolation_and_resilience(self) -> None:
        """Verify Redis exceptions are suppressed by default so the payment route never fails."""
        mock_redis = MagicMock(spec=redis.Redis)
        mock_redis.publish.side_effect = redis.ConnectionError("Redis connection lost")

        # Default policy: raise_on_error=False
        safe_publisher = EventPublisher(redis_client=mock_redis, raise_on_error=False)
        res = _create_mock_routing_result(tx_id="tx_err_isolated")

        # Must not raise exception
        result = safe_publisher.publish_routing_event(res)
        assert result is None

        # Strict test policy: raise_on_error=True
        strict_publisher = EventPublisher(redis_client=mock_redis, raise_on_error=True)
        with pytest.raises(redis.ConnectionError):
            strict_publisher.publish_routing_event(res)

    def test_health_alert_channel_broadcast(
        self,
        fake_server: fakeredis.FakeServer,
    ) -> None:
        """Verify health degradation alerts broadcast cleanly to events:health."""
        client_pub = fakeredis.FakeRedis(server=fake_server, decode_responses=True)
        client_sub = fakeredis.FakeRedis(server=fake_server, decode_responses=True)

        publisher = EventPublisher(redis_client=client_pub, health_channel="events:health")
        subscriber = EventSubscriber(redis_client=client_sub, channels=["events:health"])

        # Drain subscribe message
        _ = subscriber.get_raw_message(timeout=0.05)

        alert = publisher.publish_health_alert(
            acquirer_id="acquirer_beta",
            old_health=0.98,
            new_health=0.35,
            severity="CRITICAL",
            message="Immediate circuit breaker trip on acquirer_beta",
        )
        assert alert is not None
        assert alert.severity == "CRITICAL"

        raw_msg = subscriber.get_raw_message(timeout=1.0)
        assert raw_msg is not None
        received_alert = HealthAlertEvent.model_validate_json(raw_msg["data"])
        assert received_alert.acquirer_id == "acquirer_beta"
        assert received_alert.old_health == 0.98
        assert received_alert.new_health == 0.35
        assert received_alert.severity == "CRITICAL"

        subscriber.close()
        publisher.close()

    def test_channel_namespacing(self) -> None:
        """Verify key_prefix properly namespaces routing and health channels."""
        config = DataLayerConfig(
            key_prefix="staging:",
            redis_channel_routing="events:routing",
            redis_channel_health="events:health",
        )
        mock_redis = MagicMock(spec=redis.Redis)
        publisher = EventPublisher(redis_client=mock_redis, config=config)

        assert publisher.routing_channel == "staging:events:routing"
        assert publisher.health_channel == "staging:events:health"


class TestBanditRouterPubSubIntegration:
    """Validates end-to-end integration of BanditRouter and EventPublisher."""

    @pytest.mark.asyncio
    async def test_router_route_publishes_event_to_subscriber(self) -> None:
        """Verify calling BanditRouter.route() automatically emits telemetry to Pub/Sub."""
        server = fakeredis.FakeServer()
        r_pub = fakeredis.FakeRedis(server=server, decode_responses=True)
        r_sub = fakeredis.FakeRedis(server=server, decode_responses=True)

        publisher = EventPublisher(redis_client=r_pub, routing_channel="events:routing")
        subscriber = EventSubscriber(redis_client=r_sub, channels=["events:routing"])

        # Drain subscribe frame
        _ = subscriber.get_event(timeout=0.05)

        # Build router configuration
        routes = [
            AcquirerRouteConfig(
                acquirer_id="acquirer_alpha",
                base_url="https://mock-acquirer-alpha.internal",
                state_config=AcquirerStateConfig(alpha_prior=1.0, beta_prior=1.0, decay_factor=0.9),
            ),
            AcquirerRouteConfig(
                acquirer_id="acquirer_beta",
                base_url="https://mock-acquirer-beta.internal",
                state_config=AcquirerStateConfig(alpha_prior=1.0, beta_prior=1.0, decay_factor=0.9),
            ),
        ]
        router_config = RouterConfig(
            routes=routes,
            seed=42,
            pid_config=PIDConfig(kp=0.2, ki=0.01, kd=0.05),
        )

        def mock_transport(req: httpx.Request) -> httpx.Response:
            resp_data = {
                "authorized": True,
                "status": "AUTHORIZED",
                "acquirer_id": "acquirer_alpha",
                "transaction_id": "tx_e2e_001",
                "simulated_latency_ms": 12.0,
                "timestamp": time.time(),
                "decline_code": None,
            }
            return httpx.Response(200, json=resp_data)

        async with httpx.AsyncClient(transport=httpx.MockTransport(mock_transport)) as client:
            router = BanditRouter(
                config=router_config,
                http_client=client,
                event_publisher=publisher,
            )

            req = AuthorizeRequest(
                transaction_id="tx_e2e_001",
                amount=100.0,
                currency="USD",
                merchant_id="merchant_test",
            )
            result = await router.route(req)

            assert result.status == "AUTHORIZED"
            assert result.authorized is True

            # Subscriber should receive the published RoutingEvent
            event = subscriber.get_event(timeout=1.0)
            assert event is not None
            assert event.transaction_id == "tx_e2e_001"
            assert event.sequence_number == 1
            assert event.status == "AUTHORIZED"
            assert event.selected_acquirer in ("acquirer_alpha", "acquirer_beta")
            assert event.routing_latency_ms > 0.0
            assert event.acquirer_latency_ms > 0.0
            assert event.total_latency_ms > 0.0
            assert event.updated_state["alpha"] > 1.0

        subscriber.close()
        publisher.close()


class TestAsyncPubSubDelivery:
    """Validates AsyncEventPublisher and AsyncEventSubscriber under async workflows."""

    @pytest.mark.asyncio
    async def test_async_publisher_and_subscriber_burst_delivery(self) -> None:
        """Verify AsyncEventPublisher and AsyncEventSubscriber deliver events with zero drops."""
        server = fakeredis.FakeServer()
        c_pub = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)
        c_sub = fakeredis.aioredis.FakeRedis(server=server, decode_responses=True)

        async_publisher = AsyncEventPublisher(redis_client=c_pub, routing_channel="events:routing")
        async_subscriber = AsyncEventSubscriber(redis_client=c_sub, channels=["events:routing"])

        # Drain subscribe frame
        _ = await async_subscriber.get_event(timeout=0.05)

        num_events = 75
        for i in range(1, num_events + 1):
            res = _create_mock_routing_result(tx_id=f"tx_async_{i:03d}")
            evt = await async_publisher.publish_routing_event(res)
            assert evt is not None
            assert evt.sequence_number == i

        # Drain all events
        received = await async_subscriber.drain(max_events=num_events, timeout=0.2)
        assert len(received) == num_events

        for idx, evt in enumerate(received, start=1):
            assert evt.sequence_number == idx
            assert evt.transaction_id == f"tx_async_{idx:03d}"

        await async_subscriber.aclose()
        await async_publisher.aclose()
