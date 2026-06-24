"""Lightweight fakes for unit-testing the Phase-1 fixes without live services.

Per coding_philosophy.md we test against real Redis where we can (see the
`requires_redis` fixture in conftest), but Volopay and Supabase have no
credentials yet — so we fake exactly the surfaces the fixes touch. FakeRedis
also lets us drive the lock/SETNX logic deterministically in CI without a server.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# ── Volopay ─────────────────────────────────────────────────────────────────
@dataclass
class FakePayment:
    status: str
    id: str = "pay_fake"


class FakeVolopayClient:
    """create_payment returns the next item from `script` (a FakePayment to
    return, or an Exception to raise). get_payment_by_idempotency_key returns
    `existing`. issue_virtual_card records its calls."""

    def __init__(self, script=None, existing: FakePayment | None = None):
        self.script: list = list(script) if script else [FakePayment("settled")]
        self.existing = existing
        self.create_calls: list[dict] = []
        self.card_calls: list[dict] = []

    def create_payment(self, amount, vendor_id, idempotency_key):
        self.create_calls.append(
            {"amount": amount, "vendor_id": vendor_id, "idempotency_key": idempotency_key}
        )
        item = self.script.pop(0) if self.script else FakePayment("settled")
        if isinstance(item, Exception):
            raise item
        return item

    def get_payment_by_idempotency_key(self, idempotency_key):
        return self.existing

    def issue_virtual_card(self, limit, order_id):
        self.card_calls.append({"limit": limit, "order_id": order_id})
        return {"card_id": "card_fake", "limit": limit, "order_id": order_id}


# ── Redis ───────────────────────────────────────────────────────────────────
class _FakeLock:
    """Mimics redis-py's async Lock for budget_lock(). acquire() returns False
    immediately if the key is already held (we don't actually sleep through
    blocking_timeout — that keeps tests fast while still exercising the
    'lock contended -> BudgetLockError' path)."""

    def __init__(self, store: dict, key: str):
        self._store = store
        self._key = key
        self._owned = False

    async def acquire(self, blocking=True, blocking_timeout=None):
        if self._store.get(self._key):
            return False
        self._store[self._key] = True
        self._owned = True
        return True

    async def release(self):
        if self._owned:
            self._store.pop(self._key, None)
            self._owned = False


class FakeRedis:
    """Just enough of redis.asyncio.Redis for Fix 02 and Fix 05.

    set(nx=True) sets only if absent (returns True) else returns None — exactly
    the SETNX semantics the state lock relies on. lock() returns a _FakeLock
    sharing the same key space.
    """

    def __init__(self):
        self._kv: dict = {}
        self._locks: dict = {}

    async def set(self, key, value, nx=False, ex=None):
        if nx and key in self._kv:
            return None
        self._kv[key] = value
        return True

    async def get(self, key):
        return self._kv.get(key)

    async def delete(self, key):
        self._kv.pop(key, None)
        return 1

    def lock(self, key, timeout=None):
        return _FakeLock(self._locks, key)


# ── Specialist agent (vendor search) for Fix 04 re-fetch ────────────────────
@dataclass
class FakeSpecialistAgent:
    options: list = field(default_factory=lambda: [{"vendor_id": "v_new", "price": 9000}])
    search_calls: list = field(default_factory=list)

    async def search(self, parsed_intent):
        self.search_calls.append(parsed_intent)
        return self.options


# ── LLM router fake for testing the agent wrappers (Phase 2) ────────────────
class FakeRouter:
    """Stands in for llm_router in agent unit tests. Returns a fixed text (or
    raises a fixed exception) and records the calls it received."""

    def __init__(self, text: str = "{}", raise_exc: Exception | None = None):
        self.text = text
        self.raise_exc = raise_exc
        self.calls: list[dict] = []

    async def complete(self, task, prompt, **kwargs):
        self.calls.append({"task": task, "prompt": prompt, **kwargs})
        if self.raise_exc is not None:
            raise self.raise_exc
        from agents.llm_router import LLMResult
        return LLMResult(text=self.text, confidence=1.0)
