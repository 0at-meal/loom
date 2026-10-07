"""Dashboard telemetry fan-out that never blocks the payment path (AUDIT F-05)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect

logger = logging.getLogger("loom.router_core.telemetry")

DEFAULT_QUEUE_SIZE = 256


class TelemetryBroadcaster:
    """Fan out JSON events to WebSocket clients through per-client bounded queues.

    ``publish`` only enqueues, so it returns immediately however slow a client is. Each
    client has its own sender task; when a client falls ``queue_size`` messages behind, its
    oldest queued message is dropped and counted in ``dropped``.
    """

    def __init__(self, queue_size: int = DEFAULT_QUEUE_SIZE) -> None:
        """Create an empty broadcaster."""
        if queue_size < 1:
            raise ValueError(f"queue_size must be >= 1, got {queue_size}")
        self.queue_size = queue_size
        self.dropped = 0
        self._clients: dict[WebSocket, tuple[asyncio.Queue[str], asyncio.Task[None]]] = {}

    def __len__(self) -> int:
        """Return the number of connected clients."""
        return len(self._clients)

    def register(self, websocket: WebSocket) -> None:
        """Start delivering events to ``websocket``. Must be called inside the event loop."""
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=self.queue_size)
        task = asyncio.create_task(self._sender(websocket, queue))
        self._clients[websocket] = (queue, task)

    def unregister(self, websocket: WebSocket) -> None:
        """Stop delivering to ``websocket`` and cancel its sender."""
        entry = self._clients.pop(websocket, None)
        if entry is not None:
            entry[1].cancel()

    def publish(self, payload: dict[str, Any]) -> None:
        """Queue ``payload`` for every client without waiting on any of them."""
        if not self._clients:
            return
        message = json.dumps(payload, default=str)
        for queue, _task in self._clients.values():
            self._offer(queue, message)

    def send_to(self, websocket: WebSocket, payload: dict[str, Any]) -> None:
        """Queue ``payload`` for one client (e.g. a PONG), behind its pending events."""
        entry = self._clients.get(websocket)
        if entry is not None:
            self._offer(entry[0], json.dumps(payload, default=str))

    async def close(self) -> None:
        """Cancel every sender task and forget all clients."""
        tasks = [task for _queue, task in self._clients.values()]
        self._clients.clear()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task

    def _offer(self, queue: asyncio.Queue[str], message: str) -> None:
        """Enqueue ``message``, dropping the oldest queued message if the queue is full."""
        if queue.full():
            with contextlib.suppress(asyncio.QueueEmpty):
                queue.get_nowait()
                self.dropped += 1
        queue.put_nowait(message)

    async def _sender(self, websocket: WebSocket, queue: asyncio.Queue[str]) -> None:
        """Deliver queued messages to one client until it disconnects or is unregistered."""
        try:
            while True:
                message = await queue.get()
                await websocket.send_text(message)
        except (WebSocketDisconnect, RuntimeError, ConnectionResetError, OSError) as exc:
            logger.debug("Dropping telemetry client after send failure: %s", exc)
            self._clients.pop(websocket, None)
