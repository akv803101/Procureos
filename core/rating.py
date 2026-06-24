"""Rating collection — the second human touchpoint (PRD Section 16).

on_delivery_confirmed: create the rating row with system-calculated signals
(on-time, price-accurate, repeat) and (if we know the employee) send a one-tap
👍/👎 WhatsApp prompt.

on_rating_received: map the tap to a 1-5 score (V1: 5 satisfied / 2 issue) and
immediately recompute the vendor's composite score.
"""
from __future__ import annotations

import logging

from core.db import Rating, Store, SupabaseStore
from core.state_machine import GoalState, transition_goal_state
from core.vendor_scorer import calculate_vendor_score
from services import whatsapp

log = logging.getLogger(__name__)

_default_store: Store = SupabaseStore()

# V1 maps the binary tap to the 1-5 scale (no 1/3/4 in V1).
RATING_SATISFIED = 5
RATING_ISSUE = 2


def build_rating_buttons(rating_id: str) -> list[dict]:
    """Quick-reply buttons; ids are parsed by waba_router (rate_good_/rate_bad_)."""
    return [
        {"id": f"rate_good_{rating_id}", "title": "👍 Satisfied"},
        {"id": f"rate_bad_{rating_id}", "title": "👎 Had an Issue"},
    ]


async def send_rating_prompt(to: str, vendor_name: str, rating_id: str, *, send_fn=None) -> dict:
    body = f"How was your order from {vendor_name}? One tap, takes 4 seconds."
    return await whatsapp.send_buttons(to, body, build_rating_buttons(rating_id), send_fn=send_fn)


async def on_delivery_confirmed(order_id: str, *, store: Store | None = None,
                                notify_to: str | None = None, vendor_name: str | None = None,
                                send_fn=None) -> dict:
    """Create the rating (system signals) and, if we have the employee's number,
    send the rating prompt."""
    store = store or _default_store
    order = await store.get_order(order_id)

    delivered_on_time = (order.delivered_at is not None and order.promised_eta is not None
                         and order.delivered_at <= order.promised_eta)
    final = order.final_price if order.final_price is not None else order.quoted_price
    price_accurate = (final is not None and order.quoted_price is not None and final <= order.quoted_price)
    prev = await store.get_orders_for_company_vendor(order.company_id, order.vendor_id, exclude_order_id=order_id)
    is_repeat = len(prev) > 0

    rating_id = await store.create_rating(Rating(
        id="", order_id=order_id, vendor_id=order.vendor_id, company_id=order.company_id,
        delivered_on_time=delivered_on_time, price_accurate=price_accurate,
        response_time_mins=order.vendor_response_time_mins, is_repeat_order=is_repeat,
    ))
    await store.mark_order_rating_sent(order_id)

    sent = False
    if notify_to:
        await send_rating_prompt(notify_to, vendor_name or "the vendor", rating_id, send_fn=send_fn)
        sent = True
    log.debug("rating %s created for order %s (prompt_sent=%s)", rating_id, order_id, sent)
    return {"rating_id": rating_id, "prompt_sent": sent,
            "system_signals": {"delivered_on_time": delivered_on_time, "price_accurate": price_accurate,
                               "is_repeat_order": is_repeat}}


async def on_rating_received(rating_id: str, satisfied: bool, comment: str | None = None,
                             *, store: Store | None = None, redis=None) -> dict:
    """Record the employee's tap, recompute the vendor's composite score, and
    drive the goal to its terminal state: delivered -> rated -> completed."""
    store = store or _default_store
    overall = RATING_SATISFIED if satisfied else RATING_ISSUE
    await store.update_rating(rating_id, overall_rating=overall, satisfied=satisfied, comment=comment)
    rating = await store.get_rating(rating_id)
    score = await calculate_vendor_score(rating.vendor_id, store=store)

    # Complete the goal lifecycle. CAS-gated, so a re-rating is a harmless no-op.
    order = await store.get_order(rating.order_id)
    if await transition_goal_state(order.goal_id, GoalState.DELIVERED, GoalState.RATED,
                                   store=store, redis=redis):
        await transition_goal_state(order.goal_id, GoalState.RATED, GoalState.COMPLETED,
                                    store=store, redis=redis)
    log.debug("rating %s received satisfied=%s -> vendor %s score=%s; goal %s completed",
              rating_id, satisfied, rating.vendor_id, score.get("score"), order.goal_id)
    return {"rating_id": rating_id, "overall_rating": overall, "vendor_score": score}
