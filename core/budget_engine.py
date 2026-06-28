"""Budget engine — the only path through which payments execute.

Two binding fixes live here:

Fix 02 (atomic budget re-check): budget is verified again, at payment time,
inside a Redis distributed lock keyed on (company_id, category). Without this,
two approvals firing at once can both read "budget available" and both pay,
overspending the category cap. The lock serializes the check-and-spend.

Fix 03 (GST buffer on card limit): the Volopay virtual card is issued for the
quoted price × 1.28 (the highest GST slab), because the vendor's invoice adds
GST on top of the quote. Without the buffer the card declines at settlement.
"""
from __future__ import annotations

import contextlib
import logging

from core.clients import get_redis
from core.db import Store, SupabaseStore
from core.errors import BudgetExceededError, BudgetLockError
from services.payment import issue_volopay_payment

log = logging.getLogger(__name__)

from core.store import get_store  # lazy shared store (no eager SupabaseStore; avoids split-brain)

# ── Fix 03 — GST buffer ─────────────────────────────────────────────────────
GST_BUFFER_MULTIPLIER = 1.28   # covers the maximum 28% GST slab in India


def calculate_card_limit(quoted_price: float, gst_required: bool) -> float:
    """Card limit to request from Volopay for an order.

    When GST applies, buffer by ×1.28 so the card still authorizes once the
    vendor's invoice adds up to 28% GST on top of the quote. When GST does not
    apply, a small 5% buffer absorbs misc charges. Volopay auto-returns the
    unused authorized amount post-settlement, so over-authorizing is safe.
    """
    if not gst_required:
        return round(quoted_price * 1.05, 2)
    return round(quoted_price * GST_BUFFER_MULTIPLIER, 2)


# ── Fix 02 — atomic budget lock ─────────────────────────────────────────────
def _budget_lock_key(company_id: str, category: str) -> str:
    return f"budget_lock:{company_id}:{category}"


@contextlib.asynccontextmanager
async def budget_lock(company_id: str, category: str, timeout: int = 10, *, redis=None):
    """Hold a per-(company, category) distributed lock for the duration.

    redis-py Lock semantics:
      timeout=10          -> the lock key auto-expires after 10s, so a crashed
                             holder can't deadlock the category forever.
      blocking_timeout=5  -> wait up to 5s to acquire; if we can't, fail fast
                             with BudgetLockError rather than hanging.
    """
    redis = redis or get_redis()
    lock = redis.lock(_budget_lock_key(company_id, category), timeout=timeout)
    acquired = await lock.acquire(blocking=True, blocking_timeout=5)
    if not acquired:
        raise BudgetLockError(
            f"Could not acquire budget lock for {company_id}/{category} — another payment in progress"
        )
    try:
        yield
    finally:
        await lock.release()


async def execute_payment_with_budget_check(
    order_id: str,
    company_id: str,
    category: str,
    amount: float,
    vendor_id: str,
    *,
    redis=None,
    store: Store | None = None,
    client=None,
    notify_budget_exceeded=None,
):
    """Re-check budget and execute payment atomically (Fix 02).

    NOTE (deviation from the verbatim spec, approved): the spec's body calls
    issue_volopay_payment(order_id, amount, vendor_id) but its function signature
    omitted vendor_id. We thread vendor_id through as a parameter so the call is
    well-defined. Everything else matches the spec: re-read budget + spent inside
    the lock, block if over budget, pay INSIDE the lock, then record the spend.
    """
    store = store or get_store()

    async with budget_lock(company_id, category, redis=redis):
        budget = await store.get_budget(company_id, category)
        spent = await store.get_spent_this_period(company_id, category)
        available = budget.limit - spent
        log.debug(
            "[%s] budget check %s/%s: limit=%s spent=%s available=%s amount=%s",
            order_id, company_id, category, budget.limit, spent, available, amount,
        )
        if amount > available:
            if notify_budget_exceeded is not None:
                await notify_budget_exceeded(company_id, category, amount, available)
            raise BudgetExceededError(
                f"Budget exceeded for {category}: need {amount}, available {available}"
            )

        # Execute payment INSIDE the lock so a concurrent approval cannot slip
        # between the check and the spend.
        # PHASE 2 NOTE: issue_volopay_payment is currently sync. When the real
        # Volopay HTTP client lands, make it async (httpx.AsyncClient) or wrap it
        # in asyncio.to_thread() so this blocking call does not stall the event
        # loop while the distributed budget lock is held.
        result = issue_volopay_payment(order_id, amount, vendor_id, client=client)
        await store.record_spend(company_id, category, amount, order_id)
        return result
