"""Redis Pub/Sub event broadcasting and subscription for Loom telemetry (Phase 5 Ticket B)."""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from collections.abc import AsyncIterator, Iterator
from typing import Any, Literal

import redis
import redis.asyncio as aioredis
from pydantic import ValidationError

from data_layer.config import DataLayerConfig
from data_layer.models import HealthAlertEvent, RoutingEvent
from router_core.models import RoutingResult

logger = logging.getLogger("loom.data_layer.redis_pubsub")


class EventPublisher:
    """Synchronous Redis Pub/Sub broadcaster for transaction routing and health events."""

    def __init__(
        self,
        redis_client: redis.Redis[Any] | None = None,
        config: DataLayerConfig | None = None,
        routing_channel: str | None = None,
        health_channel: str | None = None,
        raise_on_error: bool = False,
    ) -> None:
        """Initialize publisher with Redis client, target channels, and error policy."""
        self._config = config or DataLayerConfig()
        self._owns_client = redis_client is None
        prefix = self._config.key_prefix
        if prefix and not prefix.endswith(":"):
            prefix = f"{prefix}:"

        if redis_client is not None:
            self._redis = redis_client
        else:
            self._redis = redis.Redis(
                host=self._config.redis_host,
                port=self._config.redis_port,
                db=self._config.redis_db,
                password=self._config.redis_password,
                socket_timeout=self._config.redis_timeout_sec,
                decode_responses=True,
            )

        # Configure channels with optional namespace prefix
        default_routing = f"{prefix}{self._config.redis_channel_routing}"
        default_health = f"{prefix}{self._config.redis_channel_health}"
        self._routing_channel = routing_channel or default_routing
        self._health_channel = health_channel or default_health
        self._raise_on_error = raise_on_error

        # Thread-safe monotonic sequence counter
        self._sequence_lock = threading.Lock()
        self._sequence_number = 0

    @property
    def routing_channel(self) -> str:
        """Return the target routing channel name."""
        return self._routing_channel

    @property
    def health_channel(self) -> str:
        """Return the target health channel name."""
        return self._health_channel

    @property
    def sequence_number(self) -> int:
        """Return the last assigned monotonic sequence number."""
        with self._sequence_lock:
            return self._sequence_number

    @property
    def redis_client(self) -> redis.Redis[Any]:
        """Return the underlying Redis client."""
        return self._redis

    def next_sequence_number(self) -> int:
        """Atomically increment and return the next monotonic sequence number."""
        with self._sequence_lock:
            self._sequence_number += 1
            return self._sequence_number

    def publish_routing_event(
        self,
        result_or_event: RoutingResult | RoutingEvent,
    ) -> RoutingEvent | None:
        """Serialize and publish a routing decision/outcome event to Redis Pub/Sub."""
        try:
            if isinstance(result_or_event, RoutingResult):
                seq = self.next_sequence_number()
                event = RoutingEvent.from_routing_result(result_or_event, sequence_number=seq)
            else:
                event = result_or_event

            payload = event.model_dump_json()
            subscribers = self._redis.publish(self._routing_channel, payload)
            logger.debug(
                "Published routing event seq=%d tx_id=%s to channel=%s (received by %d)",
                event.sequence_number,
                event.transaction_id,
                self._routing_channel,
                subscribers,
            )
            return event
        except Exception as exc:
            logger.warning(
                "Failed to publish routing event to channel=%s: %s",
                self._routing_channel,
                exc,
            )
            if self._raise_on_error:
                raise
            return None

    def publish_health_alert(
        self,
        acquirer_id: str,
        old_health: float,
        new_health: float,
        severity: Literal["INFO", "WARNING", "CRITICAL"] = "WARNING",
        message: str | None = None,
    ) -> HealthAlertEvent | None:
        """Publish an acquirer health degradation or recovery alert to Redis Pub/Sub."""
        try:
            alert_msg = (
                message
                or f"Acquirer {acquirer_id} health changed {old_health:.3f} -> {new_health:.3f}"
            )
            event = HealthAlertEvent(
                timestamp=time.time(),
                acquirer_id=acquirer_id,
                old_health=old_health,
                new_health=new_health,
                severity=severity,
                message=alert_msg,
            )
            payload = event.model_dump_json()
            subscribers = self._redis.publish(self._health_channel, payload)
            logger.debug(
                "Published health alert for %s to channel=%s (received by %d subscribers)",
                acquirer_id,
                self._health_channel,
                subscribers,
            )
            return event
        except Exception as exc:
            logger.warning(
                "Failed to publish health alert to channel=%s: %s",
                self._health_channel,
                exc,
            )
            if self._raise_on_error:
                raise
            return None

    def publish_raw(self, channel: str, message: str) -> int:
        """Publish raw string payload to a specified channel, returning subscriber count."""
        return int(self._redis.publish(channel, message))

    def close(self) -> None:
        """Close Redis connection if owned by this publisher instance."""
        if self._owns_client and self._redis is not None:
            self._redis.close()
            logger.debug("Closed EventPublisher Redis connection")

    def __enter__(self) -> EventPublisher:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()


class AsyncEventPublisher:
    """Asynchronous Redis Pub/Sub broadcaster for transaction routing and health events."""

    def __init__(
        self,
        redis_client: aioredis.Redis[Any] | None = None,
        config: DataLayerConfig | None = None,
        routing_channel: str | None = None,
        health_channel: str | None = None,
        raise_on_error: bool = False,
    ) -> None:
        """Initialize async publisher with Redis connection and channel configuration."""
        self._config = config or DataLayerConfig()
        self._owns_client = redis_client is None
        prefix = self._config.key_prefix
        if prefix and not prefix.endswith(":"):
            prefix = f"{prefix}:"

        if redis_client is not None:
            self._redis = redis_client
        else:
            self._redis = aioredis.Redis(
                host=self._config.redis_host,
                port=self._config.redis_port,
                db=self._config.redis_db,
                password=self._config.redis_password,
                socket_timeout=self._config.redis_timeout_sec,
                decode_responses=True,
            )

        default_routing = f"{prefix}{self._config.redis_channel_routing}"
        default_health = f"{prefix}{self._config.redis_channel_health}"
        self._routing_channel = routing_channel or default_routing
        self._health_channel = health_channel or default_health
        self._raise_on_error = raise_on_error

        self._sequence_lock = asyncio.Lock()
        self._sequence_number = 0

    @property
    def routing_channel(self) -> str:
        """Return the target routing channel name."""
        return self._routing_channel

    @property
    def health_channel(self) -> str:
        """Return the target health channel name."""
        return self._health_channel

    @property
    def sequence_number(self) -> int:
        """Return the last assigned monotonic sequence number."""
        return self._sequence_number

    async def next_sequence_number(self) -> int:
        """Atomically increment and return next sequence number."""
        async with self._sequence_lock:
            self._sequence_number += 1
            return self._sequence_number

    async def publish_routing_event(
        self,
        result_or_event: RoutingResult | RoutingEvent,
    ) -> RoutingEvent | None:
        """Serialize and publish a routing decision/outcome event to Redis asynchronously."""
        try:
            if isinstance(result_or_event, RoutingResult):
                seq = await self.next_sequence_number()
                event = RoutingEvent.from_routing_result(result_or_event, sequence_number=seq)
            else:
                event = result_or_event

            payload = event.model_dump_json()
            subscribers = await self._redis.publish(self._routing_channel, payload)
            logger.debug(
                "Async published routing event seq=%d tx_id=%s to channel=%s (received by %d)",
                event.sequence_number,
                event.transaction_id,
                self._routing_channel,
                subscribers,
            )
            return event
        except Exception as exc:
            logger.warning(
                "Failed to async publish routing event to channel=%s: %s",
                self._routing_channel,
                exc,
            )
            if self._raise_on_error:
                raise
            return None

    async def publish_health_alert(
        self,
        acquirer_id: str,
        old_health: float,
        new_health: float,
        severity: Literal["INFO", "WARNING", "CRITICAL"] = "WARNING",
        message: str | None = None,
    ) -> HealthAlertEvent | None:
        """Publish health transition alert asynchronously."""
        try:
            alert_msg = (
                message
                or f"Acquirer {acquirer_id} health changed {old_health:.3f} -> {new_health:.3f}"
            )
            event = HealthAlertEvent(
                timestamp=time.time(),
                acquirer_id=acquirer_id,
                old_health=old_health,
                new_health=new_health,
                severity=severity,
                message=alert_msg,
            )
            payload = event.model_dump_json()
            subscribers = await self._redis.publish(self._health_channel, payload)
            logger.debug(
                "Async published health alert for %s to channel=%s (received by %d subscribers)",
                acquirer_id,
                self._health_channel,
                subscribers,
            )
            return event
        except Exception as exc:
            logger.warning(
                "Failed to async publish health alert to channel=%s: %s",
                self._health_channel,
                exc,
            )
            if self._raise_on_error:
                raise
            return None

    async def publish_raw(self, channel: str, message: str) -> int:
        """Publish raw string payload asynchronously."""
        return int(await self._redis.publish(channel, message))

    async def aclose(self) -> None:
        """Close async Redis connection if owned."""
        if self._owns_client and self._redis is not None:
            await self._redis.close()
            logger.debug("Closed AsyncEventPublisher Redis connection")

    async def __aenter__(self) -> AsyncEventPublisher:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.aclose()


class EventSubscriber:
    """Synchronous Redis Pub/Sub consumer for real-time telemetry events and testing."""

    def __init__(
        self,
        redis_client: redis.Redis[Any] | None = None,
        config: DataLayerConfig | None = None,
        channels: list[str] | str | None = None,
    ) -> None:
        """Initialize subscriber and open subscription on specified channels."""
        self._config = config or DataLayerConfig()
        self._owns_client = redis_client is None
        prefix = self._config.key_prefix
        if prefix and not prefix.endswith(":"):
            prefix = f"{prefix}:"

        if redis_client is not None:
            self._redis = redis_client
        else:
            self._redis = redis.Redis(
                host=self._config.redis_host,
                port=self._config.redis_port,
                db=self._config.redis_db,
                password=self._config.redis_password,
                socket_timeout=self._config.redis_timeout_sec,
                decode_responses=True,
            )

        self._pubsub = self._redis.pubsub()
        target_channels: list[str]
        if channels is None:
            target_channels = [f"{prefix}{self._config.redis_channel_routing}"]
        elif isinstance(channels, str):
            target_channels = [channels]
        else:
            target_channels = list(channels)

        if target_channels:
            self.subscribe(*target_channels)

    @property
    def pubsub(self) -> Any:
        """Return underlying redis PubSub instance."""
        return self._pubsub

    def subscribe(self, *channels: str) -> None:
        """Subscribe to additional channel(s)."""
        if channels:
            self._pubsub.subscribe(*channels)
            logger.debug("Subscribed to channels: %s", channels)

    def unsubscribe(self, *channels: str) -> None:
        """Unsubscribe from channel(s)."""
        if channels:
            self._pubsub.unsubscribe(*channels)
            logger.debug("Unsubscribed from channels: %s", channels)

    def get_raw_message(self, timeout: float = 1.0) -> dict[str, Any] | None:
        """Read the next data frame from Pub/Sub, filtering out control frames."""
        deadline = time.perf_counter() + timeout
        while True:
            remaining = max(0.0, deadline - time.perf_counter())
            raw = self._pubsub.get_message(timeout=remaining)
            if raw is None:
                return None
            msg_type = raw.get("type") if isinstance(raw, dict) else None
            if msg_type in ("subscribe", "unsubscribe", "psubscribe", "punsubscribe"):
                if time.perf_counter() >= deadline:
                    return None
                continue
            return raw if isinstance(raw, dict) else None

    def get_event(self, timeout: float = 1.0) -> RoutingEvent | None:
        """Read and validate the next RoutingEvent envelope from the subscribed channel."""
        raw = self.get_raw_message(timeout=timeout)
        if raw is None:
            return None
        data = raw.get("data")
        if not data or not isinstance(data, str):
            return None
        try:
            return RoutingEvent.model_validate_json(data)
        except (ValidationError, ValueError) as exc:
            logger.warning("Failed to validate RoutingEvent payload: %s", exc)
            return None

    def listen(self, timeout: float | None = None) -> Iterator[RoutingEvent]:
        """Generator continuously yielding RoutingEvents until timeout or stopped."""
        start_time = time.perf_counter()
        while True:
            if timeout is not None:
                elapsed = time.perf_counter() - start_time
                if elapsed >= timeout:
                    break
                step_timeout = max(0.01, timeout - elapsed)
            else:
                step_timeout = 1.0

            event = self.get_event(timeout=step_timeout)
            if event is not None:
                yield event

    def drain(self, max_events: int | None = None, timeout: float = 0.05) -> list[RoutingEvent]:
        """Drain currently buffered events from pub/sub queue up to max_events."""
        events: list[RoutingEvent] = []
        while max_events is None or len(events) < max_events:
            event = self.get_event(timeout=timeout)
            if event is None:
                break
            events.append(event)
        return events

    def close(self) -> None:
        """Close pubsub handle and Redis client if owned."""
        try:
            self._pubsub.close()
        except redis.RedisError:
            pass
        if self._owns_client and self._redis is not None:
            self._redis.close()
            logger.debug("Closed EventSubscriber Redis connection")

    def __enter__(self) -> EventSubscriber:
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.close()


class AsyncEventSubscriber:
    """Asynchronous Redis Pub/Sub consumer for real-time WebSocket feeds and async handlers."""

    def __init__(
        self,
        redis_client: aioredis.Redis[Any] | None = None,
        config: DataLayerConfig | None = None,
        channels: list[str] | str | None = None,
    ) -> None:
        """Initialize async subscriber and register initial channels."""
        self._config = config or DataLayerConfig()
        self._owns_client = redis_client is None
        prefix = self._config.key_prefix
        if prefix and not prefix.endswith(":"):
            prefix = f"{prefix}:"

        if redis_client is not None:
            self._redis = redis_client
        else:
            self._redis = aioredis.Redis(
                host=self._config.redis_host,
                port=self._config.redis_port,
                db=self._config.redis_db,
                password=self._config.redis_password,
                socket_timeout=self._config.redis_timeout_sec,
                decode_responses=True,
            )

        self._pubsub = self._redis.pubsub()
        target_channels: list[str]
        if channels is None:
            target_channels = [f"{prefix}{self._config.redis_channel_routing}"]
        elif isinstance(channels, str):
            target_channels = [channels]
        else:
            target_channels = list(channels)

        self._initial_channels = target_channels
        self._subscribed = False

    async def _ensure_subscribed(self) -> None:
        """Ensure initial subscriptions are established."""
        if not self._subscribed and self._initial_channels:
            await self._pubsub.subscribe(*self._initial_channels)
            self._subscribed = True

    async def subscribe(self, *channels: str) -> None:
        """Subscribe to additional channel(s) asynchronously."""
        if channels:
            await self._pubsub.subscribe(*channels)
            self._subscribed = True

    async def unsubscribe(self, *channels: str) -> None:
        """Unsubscribe from channel(s) asynchronously."""
        if channels:
            await self._pubsub.unsubscribe(*channels)

    async def get_raw_message(self, timeout: float = 1.0) -> dict[str, Any] | None:
        """Read the next data message asynchronously, filtering control frames."""
        await self._ensure_subscribed()
        deadline = time.perf_counter() + timeout
        while True:
            remaining = max(0.0, deadline - time.perf_counter())
            raw = await self._pubsub.get_message(timeout=remaining)
            if raw is None:
                return None
            msg_type = raw.get("type") if isinstance(raw, dict) else None
            if msg_type in ("subscribe", "unsubscribe", "psubscribe", "punsubscribe"):
                if time.perf_counter() >= deadline:
                    return None
                continue
            return raw if isinstance(raw, dict) else None

    async def get_event(self, timeout: float = 1.0) -> RoutingEvent | None:
        """Read and validate next RoutingEvent asynchronously."""
        raw = await self.get_raw_message(timeout=timeout)
        if raw is None:
            return None
        data = raw.get("data")
        if not data or not isinstance(data, str):
            return None
        try:
            return RoutingEvent.model_validate_json(data)
        except (ValidationError, ValueError) as exc:
            logger.warning("Failed to validate RoutingEvent payload: %s", exc)
            return None

    async def listen(self) -> AsyncIterator[RoutingEvent]:
        """Asynchronously stream validated RoutingEvents indefinitely."""
        await self._ensure_subscribed()
        while True:
            event = await self.get_event(timeout=1.0)
            if event is not None:
                yield event

    async def drain(
        self,
        max_events: int | None = None,
        timeout: float = 0.05,
    ) -> list[RoutingEvent]:
        """Asynchronously drain available messages up to max_events."""
        events: list[RoutingEvent] = []
        while max_events is None or len(events) < max_events:
            event = await self.get_event(timeout=timeout)
            if event is None:
                break
            events.append(event)
        return events

    async def aclose(self) -> None:
        """Close pubsub handle and Redis connection if owned."""
        try:
            await self._pubsub.close()
        except redis.RedisError:
            pass
        if self._owns_client and self._redis is not None:
            await self._redis.close()
            logger.debug("Closed AsyncEventSubscriber Redis connection")

    async def __aenter__(self) -> AsyncEventSubscriber:
        await self._ensure_subscribed()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.aclose()
