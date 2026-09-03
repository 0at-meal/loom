"""Global pytest fixtures and test harness configuration."""

from collections.abc import AsyncGenerator

import fakeredis.aioredis
import pytest


@pytest.fixture
async def fake_redis() -> AsyncGenerator[fakeredis.aioredis.FakeRedis, None]:
    """Provides a self-contained in-memory async Redis instance for tests."""
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.flushall()
    await client.aclose()
