"""Volopay payment integration.

Fix 01 (binding) lives here: every Volopay payment call carries a deterministic
idempotency key derived from the order_id, so a network-timeout retry can never
charge a vendor twice — the same order_id always produces the same key and
Volopay deduplicates on it.

The Volopay HTTP client is gated on credentials we don't have in Phase 1, so it
is injected (`client=`). Tests pass a fake client; Phase 2 wires the real one.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging

from core.config import settings
from core.errors import PaymentDuplicateError

log = logging.getLogger(__name__)


def get_payment_client():
    """Return the configured payment client. Razorpay (test mode) when its keys
    are set; this is the default the payment functions use when no client is
    injected. Tests always inject a fake client explicitly."""
    if settings.razorpay_key_id and settings.razorpay_key_secret:
        from services.razorpay import RazorpayClient
        return RazorpayClient()
    raise NotImplementedError(
        "No payment provider configured — set RAZORPAY_KEY_ID/RAZORPAY_KEY_SECRET in .env"
    )

# Fix 01 retry policy (binding constants).
MAX_RETRIES = 3
RETRY_INTERVAL_SECONDS = 600   # 10 minutes between retries


def payment_idempotency_key(order_id: str) -> str:
    """Deterministic key for a given order.

    sha256("payment:{order_id}") — the SAME order_id ALWAYS produces the SAME
    key, which is the whole point: Volopay dedupes on it, so retrying a timed-out
    request reuses the key instead of creating a second charge.
    """
    return hashlib.sha256(f"payment:{order_id}".encode()).hexdigest()


def get_volopay_client():
    """Real Volopay client — wired in Phase 2 with live credentials."""
    raise NotImplementedError(
        "Volopay client is wired in Phase 2. For tests, pass client= explicitly."
    )


def issue_volopay_payment(order_id: str, amount: float, vendor_id: str, *, client=None):
    """Issue (or recover) a single Volopay payment for an order.

    If Volopay reports the idempotency key as a 'duplicate', we fetch the prior
    payment: if it already settled, return it (the earlier attempt succeeded —
    do NOT pay again); otherwise raise PaymentDuplicateError so a human resolves
    the unknown state rather than the system blindly retrying.
    """
    client = client or get_payment_client()
    idempotency_key = payment_idempotency_key(order_id)

    response = client.create_payment(
        amount=amount, vendor_id=vendor_id, idempotency_key=idempotency_key
    )
    if response.status == "duplicate":
        existing = client.get_payment_by_idempotency_key(idempotency_key)
        if existing.status == "settled":
            return existing  # already paid — safe to return, never retry
        raise PaymentDuplicateError(
            f"Duplicate idempotency key for order {order_id} in unknown state: {existing.status}"
        )
    return response


async def _default_escalate(order_id: str, reason: str) -> None:
    """Placeholder operator escalation — replaced in Phase 2 (operator_logs)."""
    raise NotImplementedError(
        "escalate_to_operator is wired in Phase 2. For tests, pass escalate=."
    )


async def payment_with_retry(
    order_id: str,
    amount: float,
    vendor_id: str,
    *,
    client=None,
    escalate=None,
):
    """Retry a payment up to MAX_RETRIES, honoring idempotency.

    - A settled result returns immediately.
    - PaymentDuplicateError is NEVER retried (re-raised at once) — retrying an
      unknown-duplicate is exactly the double-charge risk Fix 01 prevents.
    - Any other error is retried after RETRY_INTERVAL_SECONDS; on the final
      attempt it escalates to a human operator and re-raises.
    """
    escalate = escalate or _default_escalate
    result = None
    for attempt in range(MAX_RETRIES):
        try:
            result = issue_volopay_payment(order_id, amount, vendor_id, client=client)
            if result.status == "settled":
                return result
            # Not settled but no exception (e.g. still 'processing'): fall through
            # and retry on the next iteration without sleeping, per Fix 01 spec.
        except PaymentDuplicateError:
            raise  # known double-charge guard — do not retry
        except Exception as e:  # noqa: BLE001 — Volopay/network errors are retryable
            log.warning("[%s] payment attempt %d failed: %s", order_id, attempt + 1, e)
            if attempt == MAX_RETRIES - 1:
                await escalate(order_id, reason=str(e))
                raise
            await asyncio.sleep(RETRY_INTERVAL_SECONDS)
    # Exhausted retries without a settled result. Return the last result so the
    # caller can decide (it will not be 'settled'); the caller moves the goal to
    # payment_failed / operator_escalated.
    return result


def issue_virtual_card(order_id: str, limit: float, *, client=None):
    """Issue a Volopay virtual card with the given limit (used with Fix 03).

    The limit is computed by core.budget_engine.calculate_card_limit() — this
    function only performs the issuance call.
    """
    client = client or get_volopay_client()
    return client.issue_virtual_card(limit=limit, order_id=order_id)
