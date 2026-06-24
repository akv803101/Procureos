"""Shared test fixtures.

`requires_redis` gives a real async Redis client when one is reachable (the
docker-compose redis), and skips the test otherwise — so the same suite runs
both in CI-with-Redis and on a laptop without it.
"""
import pytest
import pytest_asyncio
import redis.asyncio as aioredis

from core.config import settings


@pytest_asyncio.fixture
async def requires_redis():
    client = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        await client.ping()
    except Exception:  # noqa: BLE001 — any connection failure means "no redis"
        await client.aclose()
        pytest.skip("Redis not reachable at REDIS_URL — skipping live-Redis test")
    # Clean only the keys our fixes use, so we never stomp unrelated data.
    async for key in client.scan_iter(match="goal_lock:*"):
        await client.delete(key)
    async for key in client.scan_iter(match="goal_state:*"):
        await client.delete(key)
    async for key in client.scan_iter(match="budget_lock:*"):
        await client.delete(key)
    yield client
    await client.aclose()
