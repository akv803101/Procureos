"""WABA routing (Fix 06, binding).

One Chat Mitra WABA number receives BOTH vendor RFQ replies and employee
delivery/rating taps, and a single phone can be a vendor for company A and an
employee for company B. So routing is by MESSAGE CONTENT first, phone number
last. Four-priority cascade; anything unattributable goes to the operator queue.

Handlers and the store are injected (a `WabaHandlers` object + a store exposing
`get_goal_by_partial_id` / `get_active_rfq_goals_for_vendor`). The handler
bodies (quote parsing, rating, delivery, opt-out) are wired as those pipeline
steps land; the routing logic here is complete and tested now.
"""
from __future__ import annotations

import logging
import re
from typing import Protocol

log = logging.getLogger(__name__)

# Fix 06: REF code is exactly 8 chars [A-Z0-9], matched case-insensitively then
# looked up lowercased.
REF_RE = re.compile(r"REF:([A-Z0-9]{8})")
OPT_OUT_KEYWORDS = {"OPTOUT", "OPT OUT", "STOP", "UNSUBSCRIBE"}


class WabaHandlers(Protocol):
    async def handle_employee_rating(self, rating_id: str, button_id: str): ...
    async def handle_delivery_confirmed(self, order_id: str): ...
    async def handle_delivery_denied(self, order_id: str): ...
    async def handle_vendor_quote_reply(self, goal_id: str, vendor_phone: str, message: str): ...
    async def handle_vendor_optout(self, vendor_phone: str): ...
    async def push_unrouted_message(self, vendor_phone: str, message: str, reason: str): ...


async def route_incoming_whatsapp(payload: dict, *, handlers: WabaHandlers, store):
    """Route one inbound WhatsApp event. Returns whatever the chosen handler
    returns (handlers decide the side effects)."""
    sender = payload.get("from")
    message = payload.get("text", "") or ""

    # ── P1: interactive button taps (ratings, delivery confirmation) ─────────
    if payload.get("type") == "interactive":
        inter = payload.get("interactive", {})
        # Accept either a reply-button or a list pick (both carry an id).
        button_id = (inter.get("button_reply") or inter.get("list_reply") or {}).get("id", "")
        if button_id.startswith("rate_good_") or button_id.startswith("rate_bad_"):
            rating_id = button_id.split("_", 2)[-1]
            return await handlers.handle_employee_rating(rating_id, button_id)
        if button_id.startswith("confirm_delivered_"):
            return await handlers.handle_delivery_confirmed(button_id.replace("confirm_delivered_", ""))
        if button_id.startswith("confirm_not_delivered_"):
            return await handlers.handle_delivery_denied(button_id.replace("confirm_not_delivered_", ""))
        log.warning("interactive payload with unknown button_id=%r", button_id)

    # ── P2: REF code in free text — the strongest vendor-reply signal ─────────
    m = REF_RE.search(message.upper())
    if m:
        partial_id = m.group(1).lower()
        goal = await store.get_goal_by_partial_id(partial_id, vendor_phone=sender)
        if goal is not None:
            return await handlers.handle_vendor_quote_reply(goal.id, sender, message)
        log.warning("REF %s from %s matched no goal", partial_id, sender)

    # ── P3: opt-out keywords ─────────────────────────────────────────────────
    if message.strip().upper() in OPT_OUT_KEYWORDS:
        return await handlers.handle_vendor_optout(sender)

    # ── P4: known vendor with exactly ONE active RFQ — safe to attribute ──────
    active = await store.get_active_rfq_goals_for_vendor(sender)
    if active and len(active) == 1:
        return await handlers.handle_vendor_quote_reply(active[0].id, sender, message)

    # ── Fallback: can't attribute — operator queue ───────────────────────────
    return await handlers.push_unrouted_message(
        sender, message,
        reason="Cannot attribute to goal — no REF code, and zero or multiple active RFQs",
    )
