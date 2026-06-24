"""Lazily-constructed external clients.

Redis is real and local (docker-compose provides it). Supabase/Volopay clients
are gated on credentials that don't exist yet in Phase 1 — accessing them
without creds raises a clear error rather than failing cryptically later.
"""
from __future__ import annotations

import redis.asyncio as aioredis

from core.config import settings

_redis: aioredis.Redis | None = None


def get_redis() -> aioredis.Redis:
    """Return a shared async Redis client.

    decode_responses=True so SET/GET deal in str (the state machine stores plain
    state strings). from_url() is lazy — it does not open a socket until the
    first command, so importing this module never requires a running Redis.
    """
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    return _redis
