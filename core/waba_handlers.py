"""Concrete WABA handlers (the bodies waba_router routes to).

Rating and delivery are fully wired (this increment). Vendor-quote-reply,
opt-out, and the unrouted fallback are thin for now — quote ingestion lands with
the GoalProcessor, and opt-out/operator-queue land with the data layer; both are
logged so nothing is silently dropped.
"""
from __future__ import annotations

import logging

from agents.orchestrator import on_quote_collected
from agents.specialist.quote_parser import parse_quote
from core.db import Store, SupabaseStore
from core.delivery import mark_delivered, mark_delivery_failed
from core.errors import QuoteAmbiguousError
from core.rating import on_rating_received

log = logging.getLogger(__name__)


class DefaultWabaHandlers:
    def __init__(self, *, store: Store | None = None, redis=None):
        self._store = store or SupabaseStore()
        self._redis = redis

    async def handle_employee_rating(self, rating_id: str, button_id: str):
        satisfied = button_id.startswith("rate_good_")
        return await on_rating_received(rating_id, satisfied, store=self._store, redis=self._redis)

    async def handle_delivery_confirmed(self, order_id: str):
        return await mark_delivered(order_id, store=self._store, redis=self._redis)

    async def handle_delivery_denied(self, order_id: str):
        return await mark_delivery_failed(order_id, store=self._store, redis=self._redis)

    async def handle_vendor_quote_reply(self, goal_id: str, vendor_phone: str, message: str):
        """Parse the vendor's reply (Fix 13 confidence gate) and feed it to the
        GoalProcessor. An ambiguous quote routes to the operator queue."""
        goal = await self._store.get_goal(goal_id)
        try:
            quote = await parse_quote(
                message, category=goal.category,
                quantity=(goal.parsed_intent or {}).get("quantity"),
                location=(goal.parsed_intent or {}).get("location"),
                budget=goal.budget_limit, goal_id=goal_id)
        except QuoteAmbiguousError as e:
            log.info("ambiguous quote for goal=%s from=%s -> operator: %s", goal_id, vendor_phone, e)
            return {"status": "operator_queue", "goal_id": goal_id, "reason": "ambiguous_quote"}

        # Resolve the replying vendor to its persisted id (orders/ratings are
        # keyed by vendors.id, not the phone). If we can't attribute it, operator.
        vendor_id = await self._store.get_vendor_id_by_phone(vendor_phone)
        if not vendor_id:
            log.info("quote from unknown vendor %s for goal=%s -> operator", vendor_phone, goal_id)
            return {"status": "operator_queue", "goal_id": goal_id, "reason": "unknown_vendor"}
        quote["vendor_id"] = vendor_id
        quote["vendor_phone"] = vendor_phone
        return await on_quote_collected(goal_id, quote, store=self._store, redis=self._redis)

    async def handle_vendor_optout(self, vendor_phone: str):
        log.info("vendor %s opted out", vendor_phone)
        return {"status": "opted_out", "vendor_phone": vendor_phone}

    async def push_unrouted_message(self, vendor_phone: str, message: str, reason: str):
        log.warning("unrouted WhatsApp from %s -> operator queue (%s)", vendor_phone, reason)
        return {"status": "operator_queue", "reason": reason}
