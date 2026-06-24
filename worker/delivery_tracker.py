"""Delivery tracking job (Fix 09 — runs in the worker process).

Polls in_transit orders and, for each that has been delivered, runs the delivery
+ rating flow. The actual delivery signal (a vendor 'delivered' WhatsApp keyword,
or a courier tracking API) is injectable via `is_delivered_fn`; the default is a
no-op until that source is wired.
"""
from __future__ import annotations

import logging

from core.db import Store, SupabaseStore
from core.delivery import begin_transit, mark_delivered

log = logging.getLogger(__name__)

_default_store: Store = SupabaseStore()


async def poll_deliveries(*, store: Store | None = None, redis=None, is_delivered_fn=None) -> dict:
    """Advance paid orders into transit, then mark delivered ones.

    Step 1: 'placed' orders (goal 'ordered') -> in_transit (begin_transit).
    Step 2: 'in_transit' orders that report delivered -> mark_delivered. The
    delivery signal (vendor 'delivered' WhatsApp keyword or courier API) is
    injectable via `is_delivered_fn`; the primary path is the inbound WhatsApp
    confirmation, so the default here is a no-op.
    Returns {"started": n, "delivered": n}.
    """
    store = store or _default_store
    try:
        placed = await store.get_orders_by_status("placed")
        in_transit = await store.get_orders_by_status("in_transit")
    except NotImplementedError:
        log.info("delivery_tracker: data layer not wired yet — skipping")
        return {"started": 0, "delivered": 0}

    started = 0
    for order in placed:
        try:
            res = await begin_transit(order.id, store=store, redis=redis)
            if res.get("status") == "in_transit":
                started += 1
        except Exception:  # one bad order must not stop the sweep
            log.exception("begin_transit failed for order %s", order.id)

    delivered = 0
    for order in in_transit:
        try:
            if is_delivered_fn and await is_delivered_fn(order):
                await mark_delivered(order.id, store=store, redis=redis)
                delivered += 1
        except Exception:
            log.exception("delivery poll failed for order %s", order.id)

    if started or delivered:
        log.info("delivery_tracker: started %d, delivered %d", started, delivered)
    return {"started": started, "delivered": delivered}
