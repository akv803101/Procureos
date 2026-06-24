"""Delivery confirmation (PRD Section 5, step DELIVERY TRACKING).

mark_delivered: record the delivery, move the goal in_transit -> delivered
(Fix 05), then trigger rating collection.
mark_delivery_failed: record the failure, move in_transit -> delivery_failed for
the operator path.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from core.db import Store, SupabaseStore
from core.rating import on_delivery_confirmed
from core.state_machine import GoalState, transition_goal_state

log = logging.getLogger(__name__)

_default_store: Store = SupabaseStore()


async def mark_delivered(order_id: str, *, delivered_at: datetime | None = None,
                         final_price: float | None = None, store: Store | None = None, redis=None,
                         notify_to: str | None = None, vendor_name: str | None = None, send_fn=None) -> dict:
    store = store or _default_store
    delivered_at = delivered_at or datetime.now(timezone.utc)

    # If a 'delivered' confirmation arrives while the goal is still 'ordered'
    # (before the worker poll advanced it), move it into transit first. This is
    # idempotent — begin_transit no-ops unless the goal is exactly 'ordered'.
    await begin_transit(order_id, store=store, redis=redis)

    order = await store.get_order(order_id)
    # Move the goal via the Fix 05 compare-and-set. If it isn't in_transit
    # (a duplicate 'delivered' webhook, or a concurrent path already won), skip —
    # this keeps delivery idempotent: no second order write, rating, or prompt.
    moved = await transition_goal_state(
        order.goal_id, GoalState.IN_TRANSIT, GoalState.DELIVERED, store=store, redis=redis)
    if not moved:
        log.info("order %s delivery already handled (goal not in_transit) — skipping", order_id)
        return {"order_id": order_id, "status": "skipped", "reason": "already handled or not in_transit"}

    await store.set_order_delivered(order_id, delivered_at, final_price=final_price)
    rating = await on_delivery_confirmed(order_id, store=store, notify_to=notify_to,
                                         vendor_name=vendor_name, send_fn=send_fn)
    log.info("order %s delivered; rating %s triggered", order_id, rating["rating_id"])
    return {"order_id": order_id, "status": "delivered", **rating}


async def begin_transit(order_id: str, *, store: Store | None = None, redis=None) -> dict:
    """Move a paid order into transit: goal ordered -> in_transit and order
    placed -> in_transit (Fix 05 CAS-gated, so it's safe to call repeatedly).
    This is what makes the order visible to delivery tracking. In production a
    real courier/vendor 'accepted' signal triggers it; the worker advances it
    for now."""
    store = store or _default_store
    order = await store.get_order(order_id)
    moved = await transition_goal_state(
        order.goal_id, GoalState.ORDERED, GoalState.IN_TRANSIT, store=store, redis=redis)
    if not moved:
        return {"order_id": order_id, "status": "skipped", "reason": "goal not in 'ordered'"}
    await store.set_order_status(order_id, "in_transit")
    log.info("order %s now in transit", order_id)
    return {"order_id": order_id, "status": "in_transit"}


async def mark_delivery_failed(order_id: str, *, store: Store | None = None, redis=None) -> dict:
    store = store or _default_store
    order = await store.get_order(order_id)
    # Gate the order write on the CAS so order and goal state stay consistent.
    moved = await transition_goal_state(
        order.goal_id, GoalState.IN_TRANSIT, GoalState.DELIVERY_FAILED, store=store, redis=redis)
    if not moved:
        log.info("order %s not in_transit — skipping delivery-failed", order_id)
        return {"order_id": order_id, "status": "skipped", "reason": "not in_transit"}
    await store.set_order_status(order_id, "failed")
    log.warning("order %s delivery failed -> operator", order_id)
    return {"order_id": order_id, "status": "delivery_failed"}
