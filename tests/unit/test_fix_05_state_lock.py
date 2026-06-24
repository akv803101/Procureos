"""Fix 05 — distributed lock + compare-and-set on goal state transitions."""
from core.db import Goal, InMemoryStore
from core.state_machine import (
    GoalState,
    _lock_key,
    _state_cache_key,
    transition_goal_state,
)
from tests.fakes import FakeRedis


def _store(status="pending_rfq"):
    return InMemoryStore(goals={"g-1": Goal(id="g-1", status=status)})


async def test_successful_transition_updates_state_and_caches_it():
    store = _store("pending_rfq")
    redis = FakeRedis()

    ok = await transition_goal_state(
        "g-1", "pending_rfq", "quotes_received", store=store, redis=redis
    )
    assert ok is True
    assert await store.get_goal_state("g-1") == "quotes_received"
    assert await redis.get(_state_cache_key("g-1")) == "quotes_received"
    # Lock released.
    assert await redis.get(_lock_key("g-1")) is None


async def test_compare_and_set_refuses_when_current_state_differs():
    store = _store("quotes_received")     # already moved on
    redis = FakeRedis()

    ok = await transition_goal_state(
        "g-1", "pending_rfq", "approved", store=store, redis=redis
    )
    assert ok is False                                  # stale caller refused
    assert await store.get_goal_state("g-1") == "quotes_received"   # unchanged
    assert await redis.get(_lock_key("g-1")) is None    # lock still released


async def test_held_lock_blocks_transition_and_is_not_stolen():
    store = _store("pending_rfq")
    redis = FakeRedis()
    # Simulate another process holding the lock.
    await redis.set(_lock_key("g-1"), "1", nx=True, ex=30)

    ok = await transition_goal_state(
        "g-1", "pending_rfq", "quotes_received", store=store, redis=redis
    )
    assert ok is False
    assert await store.get_goal_state("g-1") == "pending_rfq"   # untouched
    # We must NOT delete a lock we don't own.
    assert await redis.get(_lock_key("g-1")) == "1"


async def test_accepts_goalstate_enum_inputs():
    store = _store("pending_rfq")
    redis = FakeRedis()

    ok = await transition_goal_state(
        "g-1", GoalState.PENDING_RFQ, GoalState.QUOTES_RECEIVED, store=store, redis=redis
    )
    assert ok is True
    # Stored as the plain string value, not "GoalState.QUOTES_RECEIVED".
    assert await store.get_goal_state("g-1") == "quotes_received"


async def test_real_redis_serializes_transitions(requires_redis):
    """Same behavior, but against a real Redis SETNX (skips if none reachable)."""
    redis = requires_redis
    store = _store("pending_rfq")

    # Hold the real lock -> transition must fail.
    await redis.set(_lock_key("g-1"), "1", nx=True, ex=30)
    blocked = await transition_goal_state(
        "g-1", "pending_rfq", "quotes_received", store=store, redis=redis
    )
    assert blocked is False

    # Release -> transition succeeds and caches state.
    await redis.delete(_lock_key("g-1"))
    ok = await transition_goal_state(
        "g-1", "pending_rfq", "quotes_received", store=store, redis=redis
    )
    assert ok is True
    assert await redis.get(_state_cache_key("g-1")) == "quotes_received"
