"""Runtime store selection.

Returns a live SupabaseStore when Supabase credentials are configured, otherwise
a single shared InMemoryStore so the whole API is demoable end-to-end in one
process (data persists for the process lifetime) without a database. One
singleton per process, shared across every route and the worker.
"""
from __future__ import annotations

import logging

from core.config import settings
from core.db import InMemoryStore, Store, SupabaseStore

log = logging.getLogger(__name__)

_store: Store | None = None


def get_store() -> Store:
    global _store
    if _store is None:
        if settings.supabase_postgres_url:
            _store = SupabaseStore()
            log.info("store: using SupabaseStore (live)")
        else:
            _store = InMemoryStore()
            log.warning("store: SUPABASE_POSTGRES_URL unset — using in-memory store (demo/dev only)")
    return _store


def reset_store_for_tests() -> None:
    """Drop the singleton (test isolation)."""
    global _store
    _store = None
