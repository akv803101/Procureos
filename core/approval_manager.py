"""Approval lifecycle.

Fix 04 (binding): approvals carry a per-category TTL. If payment is attempted
after the approved options have gone stale (flight prices move in minutes), the
options are re-fetched and the approver is asked to re-approve — payment is
blocked on the stale approval. This prevents booking at a price the approver
never actually saw.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

from core.budget_engine import calculate_card_limit, execute_payment_with_budget_check
from core.db import ApprovalToken, Order, Store, SupabaseStore
from core.errors import ApprovalTokenError, OptionNotFoundError, StateConflictError
from core.state_machine import GoalState, transition_goal_state

log = logging.getLogger(__name__)

from core.store import get_store  # lazy shared store (no eager SupabaseStore; avoids split-brain)

# Fix 12: magic-link approval tokens — 4-hour TTL, one-time use.
APPROVAL_LINK_TTL_SECONDS = 14400

# Per-category time-to-live for an approval, in SECONDS (Fix 04, binding).
APPROVAL_TTL = {
    "flights": 1800,       # 30 minutes — prices change fast
    "hotels": 14400,       # 4 hours
    "fb": 14400,           # 4 hours
    "water": 86400,        # 24 hours — stable pricing
    "stationery": 86400,   # 24 hours
    "it_hardware": 43200,  # 12 hours
}
DEFAULT_APPROVAL_TTL = 14400  # 4 hours, when category is not in the table


def _elapsed_seconds(approval_time: datetime) -> float:
    """Total seconds since `approval_time`.

    Two corrections vs the verbatim spec (approved):
      1. The spec used `(utcnow() - approval_time).seconds`, which returns only
         the 0-86399 sub-day component — so a 24h TTL (water/stationery = 86400)
         could NEVER be exceeded. We use `.total_seconds()` so elapsed time is
         measured correctly across day boundaries.
      2. DB timestamps are timezone-aware (TIMESTAMPTZ) while `utcnow()` is
         naive; subtracting the two raises TypeError. We compare in aware UTC and
         treat a naive `approved_at` as UTC.
    """
    if approval_time.tzinfo is None:
        approval_time = approval_time.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - approval_time).total_seconds()


async def check_approval_before_payment(
    goal_id: str,
    *,
    store: Store | None = None,
    specialist_agent=None,
    send_approval_notification=None,
    redis=None,
) -> bool:
    """Return True iff the existing approval is still fresh enough to pay on.

    If the approval has expired for the goal's category, re-fetch options, notify
    the approver, move the goal back to pending_approval, and return False
    (payment must NOT proceed).
    """
    store = store or get_store()
    goal = await store.get_goal(goal_id)
    ttl = APPROVAL_TTL.get(goal.category, DEFAULT_APPROVAL_TTL)
    # Staleness clock starts when the options were PRESENTED (approval_sent_at),
    # not when approved — approve_goal sets approved_at=now immediately before
    # this check, so measuring from approved_at would always read ~0s. Fall back
    # to approved_at (then "fresh") only when approval_sent_at is unset.
    reference_time = goal.approval_sent_at or goal.approved_at
    if reference_time is None:
        return True
    elapsed = _elapsed_seconds(reference_time)
    log.debug("[%s] options age=%.0fs ttl=%ds category=%s", goal_id, elapsed, ttl, goal.category)

    if elapsed > ttl:
        await re_fetch_and_notify_approver(
            goal_id,
            store=store,
            specialist_agent=specialist_agent,
            send_approval_notification=send_approval_notification,
            redis=redis,
        )
        return False  # block payment — approval is stale
    return True


async def re_fetch_and_notify_approver(
    goal_id: str,
    *,
    store: Store | None = None,
    specialist_agent=None,
    send_approval_notification=None,
    redis=None,
) -> None:
    """Pull fresh options, store them, re-notify the approver, reset state.

    `specialist_agent` (vendor search) and `send_approval_notification` are
    injected — they belong to the agent/notification layers built in Phase 2.
    """
    store = store or get_store()
    if specialist_agent is None or send_approval_notification is None:
        raise NotImplementedError(
            "re_fetch_and_notify_approver needs the specialist agent + notifier "
            "(Phase 2). For tests, inject specialist_agent= and send_approval_notification=."
        )

    goal = await store.get_goal(goal_id)
    fresh_options = await specialist_agent.search(goal.parsed_intent)
    await store.update_goal_options(goal_id, fresh_options)
    await send_approval_notification(
        goal_id=goal_id,
        note="⚠️ Prices refreshed — previous approval expired. Please re-approve.",
    )
    # Route the state change through the state machine (Fix 05), never directly.
    # The goal could be in approved/approval_expired when this fires.
    current = await store.get_goal_state(goal_id)
    await transition_goal_state(goal_id, current, GoalState.PENDING_APPROVAL, store=store, redis=redis)


# ── Fix 12 — magic-link approval tokens ─────────────────────────────────────
async def generate_approval_token(
    goal_id: str,
    approver_id: str | None = None,
    *,
    store: Store | None = None,
    ttl_seconds: int = APPROVAL_LINK_TTL_SECONDS,
) -> str:
    """Create a one-time, TTL-bounded token for an emailed approval link."""
    store = store or get_store()
    token = secrets.token_urlsafe(32)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    await store.create_approval_token(
        ApprovalToken(token=token, goal_id=goal_id, approver_id=approver_id, expires_at=expires_at)
    )
    return token


async def consume_approval_token(token: str, *, store: Store | None = None) -> str:
    """Validate + burn a magic-link token. Returns the goal_id, or raises
    ApprovalTokenError (unknown / already used / expired)."""
    store = store or get_store()
    rec = await store.get_approval_token(token)
    if rec is None:
        raise ApprovalTokenError("unknown approval token")
    if rec.used_at is not None:
        raise ApprovalTokenError("approval token already used")
    expires_at = rec.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) > expires_at:
        raise ApprovalTokenError("approval token expired")
    await store.mark_approval_token_used(token, datetime.now(timezone.utc))
    return rec.goal_id


# ── Approval orchestration (ties Fixes 01-05 together) ──────────────────────
def _find_option(goal, option_id: str) -> dict | None:
    for opt in (goal.options or []):
        if str(opt.get("vendor_id")) == str(option_id) or str(opt.get("option_id")) == str(option_id):
            return opt
    return None


async def approve_goal(
    goal_id: str,
    option_id: str,
    *,
    store: Store | None = None,
    redis=None,
    volopay_client=None,
    specialist_agent=None,
    send_approval_notification=None,
    notify_budget_exceeded=None,
) -> dict:
    """Approve the chosen option and execute payment.

    Sequence (each step is one of the binding fixes):
      - guard the goal is pending_approval (else 409)
      - record the chosen option + approved_at, move to 'approved' (Fix 05 lock)
      - Fix 04: re-check option freshness; if stale, refresh + ask to re-approve
        (do NOT pay) and return early
      - Fix 03: compute the GST-buffered card limit
      - Fix 02: re-check budget under the distributed lock and (Fix 01) fire the
        idempotent payment INSIDE the lock
      - move to 'ordered'; on any payment failure move to 'payment_failed'
    """
    store = store or get_store()
    goal = await store.get_goal(goal_id)
    if goal.status != GoalState.PENDING_APPROVAL.value:
        raise StateConflictError(f"goal {goal_id} is '{goal.status}', not pending_approval")
    option = _find_option(goal, option_id)
    if option is None:
        raise OptionNotFoundError(str(option_id))

    # Win the transition FIRST (Fix 05 CAS), THEN record the approval — so a
    # losing concurrent caller (Slack double-click, or Slack + magic-link both
    # firing) doesn't mutate approval fields or pay twice.
    moved = await transition_goal_state(
        goal_id, GoalState.PENDING_APPROVAL, GoalState.APPROVED, store=store, redis=redis)
    if not moved:
        raise StateConflictError("could not transition to approved (lock held or state changed)")
    await store.set_goal_approval(goal_id, option_id=str(option_id), approved_at=datetime.now(timezone.utc))

    # Fix 04 — block payment on stale options.
    fresh = await check_approval_before_payment(
        goal_id, store=store, specialist_agent=specialist_agent,
        send_approval_notification=send_approval_notification, redis=redis)
    if not fresh:
        return {"goal_id": goal_id, "status": "re_approval_needed",
                "reason": "options refreshed — prices were stale (Fix 04)"}

    # Fix 03 — GST-buffered card limit.
    price = option.get("estimated_final_price_with_gst") or option.get("price")
    card_limit = calculate_card_limit(
        option.get("price", price), goal.parsed_intent.get("gst_required", True))

    # Create the real order row the payment + delivery tracking hang off.
    order = Order(id="", goal_id=goal_id, vendor_id=option.get("vendor_id"),
                  company_id=goal.company_id, quoted_price=option.get("price"), status="placed")
    order_id = await store.create_order(order)

    await transition_goal_state(goal_id, GoalState.APPROVED, GoalState.PAYMENT_QUEUED, store=store, redis=redis)
    try:
        # Fix 02 (atomic budget lock) wrapping Fix 01 (idempotent Volopay payment).
        # order_id keys the idempotency hash, so a retry never double-charges.
        payment = await execute_payment_with_budget_check(
            order_id=order_id, company_id=goal.company_id, category=goal.category,
            amount=price, vendor_id=option.get("vendor_id"),
            redis=redis, store=store, client=volopay_client,
            notify_budget_exceeded=notify_budget_exceeded)
    except Exception:
        # Leave the goal in a clean, recoverable state for the operator path.
        await transition_goal_state(goal_id, GoalState.PAYMENT_QUEUED, GoalState.PAYMENT_FAILED, store=store, redis=redis)
        raise

    await transition_goal_state(goal_id, GoalState.PAYMENT_QUEUED, GoalState.ORDERED, store=store, redis=redis)
    return {"goal_id": goal_id, "order_id": order_id, "status": "ordered", "option": option,
            "card_limit": card_limit, "payment_status": getattr(payment, "status", None)}


async def reject_goal(goal_id: str, reason: str, *, store: Store | None = None, redis=None) -> dict:
    """Reject the options. For now this cancels the goal; the rejection-feedback
    refinement loop (goal_refiner) is wired in a later increment."""
    store = store or get_store()
    goal = await store.get_goal(goal_id)
    if goal.status != GoalState.PENDING_APPROVAL.value:
        raise StateConflictError(f"goal {goal_id} is '{goal.status}', not pending_approval")
    moved = await transition_goal_state(
        goal_id, GoalState.PENDING_APPROVAL, GoalState.CANCELLED, store=store, redis=redis)
    if not moved:
        raise StateConflictError("could not transition to cancelled (lock held or state changed)")
    log.info("goal %s rejected: %s", goal_id, reason)
    return {"goal_id": goal_id, "status": "cancelled", "reason": reason}
