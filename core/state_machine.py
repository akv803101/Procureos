"""Goal state machine.

Fix 05 (binding) lives here: every goal state change MUST go through
`transition_goal_state()`. It takes a Redis SETNX lock per goal so the
APScheduler worker and an inbound webhook can never write conflicting states to
the same goal at the same time, and it does a compare-and-set against the
expected `from_state` so a stale caller can't clobber a state that already moved.

The canonical state set and the allowed transitions are defined here too (the
spec scatters state names across several sections — this is the single source of
truth the rest of the code should import).
"""
from __future__ import annotations

from enum import Enum

from core.clients import get_redis
from core.db import SupabaseStore, Store

# Default production store. Tests pass their own Store (InMemoryStore) via the
# `store=` parameter; runtime wiring of SupabaseStore happens in Phase 2.
_default_store: Store = SupabaseStore()


class GoalState(str, Enum):
    # happy path
    PROCESSING = "processing"          # goal created, intent being parsed
    PENDING_RFQ = "pending_rfq"        # RFQs sent, awaiting vendor replies
    QUOTES_RECEIVED = "quotes_received"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    PAYMENT_QUEUED = "payment_queued"
    ORDERED = "ordered"
    IN_TRANSIT = "in_transit"
    DELIVERED = "delivered"
    RATED = "rated"
    # exceptions
    RFQ_TIMEOUT = "rfq_timeout"
    APPROVAL_EXPIRED = "approval_expired"
    PAYMENT_FAILED = "payment_failed"
    DELIVERY_FAILED = "delivery_failed"
    OPERATOR_ESCALATED = "operator_escalated"
    GOVERNANCE_HOLD = "governance_hold"
    # terminal
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"


# Reference graph of allowed transitions. `transition_goal_state()` enforces
# compare-and-set (per the Fix 05 spec); callers can additionally consult
# `is_allowed_transition()` before attempting a move. Kept as a map of
# from-state -> permitted next states.
ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    GoalState.PROCESSING: {GoalState.PENDING_RFQ, GoalState.OPERATOR_ESCALATED, GoalState.CANCELLED, GoalState.FAILED},
    GoalState.PENDING_RFQ: {GoalState.QUOTES_RECEIVED, GoalState.RFQ_TIMEOUT, GoalState.CANCELLED},
    GoalState.QUOTES_RECEIVED: {GoalState.PENDING_APPROVAL, GoalState.OPERATOR_ESCALATED, GoalState.CANCELLED},
    GoalState.PENDING_APPROVAL: {GoalState.APPROVED, GoalState.APPROVAL_EXPIRED, GoalState.GOVERNANCE_HOLD, GoalState.CANCELLED},
    GoalState.APPROVED: {GoalState.PAYMENT_QUEUED, GoalState.PENDING_APPROVAL, GoalState.PAYMENT_FAILED},
    GoalState.PAYMENT_QUEUED: {GoalState.ORDERED, GoalState.PAYMENT_FAILED},
    GoalState.ORDERED: {GoalState.IN_TRANSIT, GoalState.DELIVERY_FAILED},
    GoalState.IN_TRANSIT: {GoalState.DELIVERED, GoalState.DELIVERY_FAILED},
    GoalState.DELIVERED: {GoalState.RATED},
    GoalState.RATED: {GoalState.COMPLETED},
    # exception recoveries
    GoalState.RFQ_TIMEOUT: {GoalState.OPERATOR_ESCALATED, GoalState.PENDING_RFQ, GoalState.CANCELLED},
    GoalState.APPROVAL_EXPIRED: {GoalState.PENDING_APPROVAL, GoalState.CANCELLED},
    GoalState.PAYMENT_FAILED: {GoalState.PAYMENT_QUEUED, GoalState.OPERATOR_ESCALATED, GoalState.FAILED},
    GoalState.DELIVERY_FAILED: {GoalState.OPERATOR_ESCALATED, GoalState.IN_TRANSIT, GoalState.FAILED},
    GoalState.GOVERNANCE_HOLD: {GoalState.PENDING_APPROVAL, GoalState.OPERATOR_ESCALATED, GoalState.CANCELLED},
    GoalState.OPERATOR_ESCALATED: {GoalState.PENDING_RFQ, GoalState.PENDING_APPROVAL, GoalState.CANCELLED, GoalState.FAILED},
}


def is_allowed_transition(from_state: str, to_state: str) -> bool:
    """Reference check against the transition graph (advisory)."""
    return to_state in ALLOWED_TRANSITIONS.get(from_state, set())


# Redis key conventions (Fix 05).
def _lock_key(goal_id: str) -> str:
    return f"goal_lock:{goal_id}"


def _state_cache_key(goal_id: str) -> str:
    return f"goal_state:{goal_id}"


def _as_value(state) -> str:
    """Normalize a GoalState enum OR a plain string to its plain string value.

    Gotcha: GoalState subclasses (str, Enum), and `str(GoalState.PENDING_RFQ)`
    returns 'GoalState.PENDING_RFQ', not 'pending_rfq'. Use the enum .value (or
    pass the string through) so we always compare/store plain state strings.
    """
    return state.value if isinstance(state, Enum) else state


async def transition_goal_state(
    goal_id: str,
    from_state: str,
    to_state: str,
    payload: dict | None = None,
    *,
    redis=None,
    store: Store | None = None,
) -> bool:
    """Atomically move a goal from `from_state` to `to_state`.

    Fix 05 mechanism:
      1. Acquire a per-goal lock with SET nx=True ex=30. nx means "set only if
         absent", so only one process holds the lock; ex=30 auto-expires it if
         the holder dies, preventing a deadlock.
      2. If the lock is already held, return False immediately (do NOT touch the
         lock — we don't own it).
      3. Re-read the *current* state inside the lock and compare to `from_state`.
         If it already changed, another process won the race — return False
         without writing (compare-and-set).
      4. Write the new state to the source of truth, then mirror it into the
         Redis state cache (24h TTL) so reads are fast.
      5. Always release the lock we acquired in `finally`.

    Returns True iff this call performed the transition.
    """
    redis = redis or get_redis()
    store = store or _default_store

    # Accept either a GoalState enum or a plain string; normalize to the value.
    from_state, to_state = _as_value(from_state), _as_value(to_state)

    acquired = await redis.set(_lock_key(goal_id), "1", nx=True, ex=30)
    if not acquired:
        # Another process is mid-transition on this goal — skip, don't block.
        return False
    try:
        current = await store.get_goal_state(goal_id)
        if current != from_state:
            # State already moved (or never was from_state) — refuse to clobber.
            return False
        await store.set_goal_state(goal_id, to_state, payload)
        await redis.set(_state_cache_key(goal_id), to_state, ex=86400)  # 24h cache
        return True
    finally:
        await redis.delete(_lock_key(goal_id))
