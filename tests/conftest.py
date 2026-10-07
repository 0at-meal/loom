"""Global pytest fixtures and test harness configuration."""

import os
from collections.abc import AsyncGenerator

# The served app logs to a SQLite ledger by default (AUDIT F-04). Tests that build the app
# must not write loom_metrics.db into the working tree, so they opt in explicitly.
os.environ.setdefault("LEDGER_ENABLED", "false")

try:
    import fakeredis.aioredis

    HAS_FAKEREDIS = True
except ImportError:
    HAS_FAKEREDIS = False
import pytest


@pytest.fixture
async def fake_redis() -> AsyncGenerator[object, None]:
    """Provides a self-contained in-memory async Redis instance for tests."""
    if not HAS_FAKEREDIS:
        pytest.skip("fakeredis is not installed")
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.flushall()
    await client.aclose()
