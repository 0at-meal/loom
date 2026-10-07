"""In-memory idempotency store keyed by transaction_id (AUDIT F-07).

A retried request with the same body gets the first result back without a second
dispatch. A duplicate that arrives while the first is still in flight is rejected, as is
the same transaction_id with a different body. Entries live for ``ttl_sec`` (default
24 h), and at most ``max_entries`` are kept; the oldest are evicted first. The store is
per process and is lost on restart.
"""

from __future__ import annotations

import json
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from router_core.models import RoutingResult

DEFAULT_TTL_SEC = 24 * 3600.0
DEFAULT_MAX_ENTRIES = 100_000


class Admission(StrEnum):
    """What to do with an incoming request."""

    NEW = "NEW"  # first time seen: dispatch it
    REPLAY = "REPLAY"  # completed before with the same body: return the stored result
    IN_FLIGHT = "IN_FLIGHT"  # the first request is still being processed
    MISMATCH = "MISMATCH"  # same transaction_id, different body


@dataclass
class _Entry:
    fingerprint: str
    created_at: float
    result: RoutingResult | None = None


@dataclass(frozen=True)
class Decision:
    """Result of :meth:`IdempotencyStore.admit`."""

    admission: Admission
    result: RoutingResult | None = None


def fingerprint(body: dict[str, Any]) -> str:
    """Canonical form of a request body, excluding transaction_id."""
    return json.dumps(
        {k: v for k, v in body.items() if k != "transaction_id"}, sort_keys=True, default=str
    )


class IdempotencyStore:
    """Bounded, expiring map from transaction_id to the first request's result."""

    def __init__(
        self,
        ttl_sec: float = DEFAULT_TTL_SEC,
        max_entries: int = DEFAULT_MAX_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Create an empty store."""
        if ttl_sec <= 0.0:
            raise ValueError(f"ttl_sec must be > 0, got {ttl_sec}")
        if max_entries < 1:
            raise ValueError(f"max_entries must be >= 1, got {max_entries}")
        self.ttl_sec = ttl_sec
        self.max_entries = max_entries
        self._clock = clock
        self._entries: OrderedDict[str, _Entry] = OrderedDict()
        self.evicted = 0
        self.replayed = 0
        self.rejected_in_flight = 0
        self.rejected_mismatch = 0

    def __len__(self) -> int:
        """Number of transaction_ids currently remembered."""
        return len(self._entries)

    def admit(self, transaction_id: str, body_fingerprint: str) -> Decision:
        """Decide what to do with a request; a NEW request is reserved until completed."""
        now = self._clock()
        self._expire(now)
        entry = self._entries.get(transaction_id)
        if entry is None:
            self._entries[transaction_id] = _Entry(body_fingerprint, now)
            self._evict_overflow()
            return Decision(Admission.NEW)
        if entry.fingerprint != body_fingerprint:
            self.rejected_mismatch += 1
            return Decision(Admission.MISMATCH)
        if entry.result is None:
            self.rejected_in_flight += 1
            return Decision(Admission.IN_FLIGHT)
        self.replayed += 1
        return Decision(Admission.REPLAY, entry.result)

    def complete(self, transaction_id: str, result: RoutingResult) -> None:
        """Store the result of a NEW request so later duplicates replay it."""
        entry = self._entries.get(transaction_id)
        if entry is not None:
            entry.result = result

    def abandon(self, transaction_id: str) -> None:
        """Forget a NEW request that failed before producing a result, so it can be retried."""
        entry = self._entries.get(transaction_id)
        if entry is not None and entry.result is None:
            del self._entries[transaction_id]

    def status(self) -> dict[str, Any]:
        """Counters for /health."""
        return {
            "entries": len(self._entries),
            "ttl_sec": self.ttl_sec,
            "max_entries": self.max_entries,
            "evicted": self.evicted,
            "replayed": self.replayed,
            "rejected_in_flight": self.rejected_in_flight,
            "rejected_mismatch": self.rejected_mismatch,
        }

    def _expire(self, now: float) -> None:
        while self._entries:
            tx_id, entry = next(iter(self._entries.items()))
            if now - entry.created_at < self.ttl_sec:
                return
            del self._entries[tx_id]

    def _evict_overflow(self) -> None:
        while len(self._entries) > self.max_entries:
            self._entries.popitem(last=False)
            self.evicted += 1
