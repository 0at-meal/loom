"""Optional Redis link for the served router: events out, belief snapshots out and in (F-04).

Nothing here sits on the payment path. ``publish_routing_event`` and ``publish_payload``
only enqueue; a background task publishes, and another writes a belief snapshot every
``redis_snapshot_interval_sec``. If Redis is down at startup or later, payments continue,
lost events are counted, and the tasks keep retrying.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import TYPE_CHECKING, Any

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from data_layer.config import DataLayerConfig
from data_layer.models import RoutingEvent

if TYPE_CHECKING:
    from router_core.bandit import BanditStateRegistry
    from router_core.models import RoutingResult

logger = logging.getLogger("loom.data_layer.live")

SNAPSHOT_VERSION = 1
_REDIS_ERRORS = (RedisError, OSError, TimeoutError)


class RedisSidecar:
    """Publish events and persist belief snapshots to Redis without blocking payments."""

    def __init__(
        self,
        registry: BanditStateRegistry,
        config: DataLayerConfig | None = None,
        client: aioredis.Redis | None = None,
    ) -> None:
        """Prepare channels and buffers; nothing connects until :meth:`start`."""
        self._config = config or DataLayerConfig()
        self._registry = registry
        self._client = client
        self._owns_client = client is None
        prefix = self._config.key_prefix
        if prefix and not prefix.endswith(":"):
            prefix = f"{prefix}:"
        self.routing_channel = f"{prefix}{self._config.redis_channel_routing}"
        self.health_channel = f"{prefix}{self._config.redis_channel_health}"
        self.snapshot_key = f"{prefix}loom:beliefs"
        self._queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(
            maxsize=self._config.redis_event_queue_size
        )
        self._tasks: list[asyncio.Task[None]] = []
        self._sequence = 0
        self.connected = False
        self.published = 0
        self.dropped = 0
        self.snapshots_written = 0
        self.restored: list[str] = []
        self.last_error: str | None = None

    async def start(self) -> None:
        """Connect, restore beliefs from the last snapshot if any, and start the workers.

        Never raises because Redis is unavailable: the router starts with in-memory priors.
        """
        if self._client is None:
            self._client = aioredis.Redis(
                host=self._config.redis_host,
                port=self._config.redis_port,
                db=self._config.redis_db,
                password=self._config.redis_password,
                socket_timeout=self._config.redis_timeout_sec,
                socket_connect_timeout=self._config.redis_timeout_sec,
                decode_responses=True,
            )
        try:
            await self._client.ping()
            self.connected = True
            raw = await self._client.get(self.snapshot_key)
            if raw:
                snapshot = json.loads(raw)
                if snapshot.get("version") == SNAPSHOT_VERSION:
                    self.restored = self._registry.restore_beliefs(snapshot["acquirers"])
                    logger.info("Restored beliefs from Redis for %s", self.restored)
        except (*_REDIS_ERRORS, ValueError, KeyError) as exc:
            self._mark_down(exc)
        self._tasks = [
            asyncio.create_task(self._publish_loop()),
            asyncio.create_task(self._snapshot_loop()),
        ]

    def publish_routing_event(self, result: RoutingResult) -> None:
        """Queue a routing event (router ``event_publisher`` hook; does not wait)."""
        self._sequence += 1
        event = RoutingEvent.from_routing_result(result, sequence_number=self._sequence)
        self._offer(self.routing_channel, event.model_dump_json())

    def publish_payload(self, channel: str, payload: dict[str, Any]) -> None:
        """Queue an arbitrary JSON payload, e.g. a health alert on ``health_channel``."""
        self._offer(channel, json.dumps(payload, default=str))

    async def write_snapshot(self) -> bool:
        """Write every acquirer's beliefs to Redis; return False if Redis is unavailable."""
        if self._client is None:
            return False
        payload = {
            "version": SNAPSHOT_VERSION,
            "written_at": time.time(),
            "acquirers": self._registry.export_beliefs(),
        }
        try:
            await self._client.set(self.snapshot_key, json.dumps(payload))
        except _REDIS_ERRORS as exc:
            self._mark_down(exc)
            return False
        self.connected = True
        self.snapshots_written += 1
        return True

    def status(self) -> dict[str, Any]:
        """Counters for /health."""
        return {
            "enabled": True,
            "status": "ok" if self.connected else "degraded",
            "connected": self.connected,
            "published": self.published,
            "dropped": self.dropped,
            "queued": self._queue.qsize(),
            "snapshots_written": self.snapshots_written,
            "restored_acquirers": self.restored,
            "last_error": self.last_error,
        }

    async def close(self) -> None:
        """Stop the workers, try a final snapshot and flush, and close an owned client."""
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks = []
        if self._client is not None and self.connected:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._flush_and_snapshot(), self._config.redis_timeout_sec)
        if self._client is not None and self._owns_client:
            with contextlib.suppress(*_REDIS_ERRORS):
                await self._client.aclose()
            self._client = None

    async def _flush_and_snapshot(self) -> None:
        """Publish whatever is still queued, then write a last snapshot."""
        assert self._client is not None
        while not self._queue.empty():
            channel, message = self._queue.get_nowait()
            try:
                await self._client.publish(channel, message)
                self.published += 1
            except _REDIS_ERRORS as exc:
                self.dropped += 1 + self._queue.qsize()
                self._mark_down(exc)
                return
        await self.write_snapshot()

    def _offer(self, channel: str, message: str) -> None:
        """Enqueue a message, dropping the oldest when the buffer is full."""
        if self._queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                self._queue.get_nowait()
                self.dropped += 1
        self._queue.put_nowait((channel, message))

    def _mark_down(self, exc: BaseException) -> None:
        if self.connected or self.last_error is None:
            logger.warning("Redis unavailable, continuing without it: %s", exc)
        self.connected = False
        self.last_error = f"{type(exc).__name__}: {exc}"

    async def _publish_loop(self) -> None:
        assert self._client is not None
        while True:
            channel, message = await self._queue.get()
            try:
                await self._client.publish(channel, message)
            except _REDIS_ERRORS as exc:
                self.dropped += 1
                self._mark_down(exc)
                await asyncio.sleep(self._config.redis_retry_interval_sec)
                continue
            self.published += 1
            self.connected = True

    async def _snapshot_loop(self) -> None:
        while True:
            await asyncio.sleep(self._config.redis_snapshot_interval_sec)
            await self.write_snapshot()
