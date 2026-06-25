"""Razorpay payment integration (test mode) — drops into the services/payment seam.

Razorpay is NOT a virtual-card issuer like Volopay, so we pay via a **Payment
Link** (test-mode, payable with a Razorpay test card). The link's reference_id is
the Fix-01 idempotency key (truncated to Razorpay's 40-char limit), which gives us
duplicate detection: a repeated create returns Razorpay's "reference id already
exists" error, which we surface as status="duplicate" so the Fix-01 wrapper can
fetch the existing link instead of paying again.

DEMO MAPPING (documented): creating the link successfully maps to status="settled"
— i.e. "the payment instruction was issued." Real capture is confirmed
asynchronously by the Razorpay webhook (/webhook/payment, payment_link.paid).
This keeps the synchronous Fix 01/02 flow intact for the demo.

The client is SYNC (httpx.Client) to match the payment seam's sync client
interface; tests inject a `requester` and never touch the network.
"""
from __future__ import annotations

import hashlib
import hmac
import logging

from core.config import settings
from core.errors import ProviderDownError

log = logging.getLogger(__name__)

BASE_URL = "https://api.razorpay.com/v1"
_REF_MAX = 40  # Razorpay reference_id max length


class RazorpayResult:
    """Duck-types the payment-client result the Fix-01 wrapper expects (.status)."""
    def __init__(self, status: str, id: str | None = None, short_url: str | None = None,
                 payment_ref: str | None = None):
        self.status = status
        self.id = id
        self.short_url = short_url
        self.payment_ref = payment_ref


class RazorpayClient:
    def __init__(self, *, key_id: str | None = None, key_secret: str | None = None, requester=None):
        self._key_id = key_id or settings.razorpay_key_id
        self._key_secret = key_secret or settings.razorpay_key_secret
        self._requester = requester   # (method, url, json=, params=) -> (status_code, dict)

    def _request(self, method: str, url: str, *, json=None, params=None):
        if self._requester is not None:
            return self._requester(method, url, json=json, params=params)
        import httpx
        with httpx.Client(timeout=20, auth=(self._key_id, self._key_secret)) as c:
            r = c.request(method, url, json=json, params=params)
            return r.status_code, (r.json() if r.content else {})

    def create_payment(self, amount, vendor_id, idempotency_key):
        ref = idempotency_key[:_REF_MAX]
        amount_paise = int(round(float(amount) * 100))
        status, body = self._request("POST", f"{BASE_URL}/payment_links", json={
            "amount": amount_paise, "currency": "INR", "reference_id": ref,
            "description": f"ProcureOS order {ref}", "accept_partial": False,
            "notes": {"vendor_id": str(vendor_id)},
        })
        if status in (200, 201):
            return RazorpayResult(status="settled", id=body.get("id"),
                                  short_url=body.get("short_url"), payment_ref=body.get("id"))
        err = (body.get("error") or {})
        if status == 400 and "reference" in (err.get("description", "").lower()):
            return RazorpayResult(status="duplicate")   # Fix 01 -> fetch existing
        raise ProviderDownError(f"razorpay payment_links {status}: {err.get('description', body)}")

    def get_payment_by_idempotency_key(self, idempotency_key):
        ref = idempotency_key[:_REF_MAX]
        status, body = self._request("GET", f"{BASE_URL}/payment_links", params={"reference_id": ref})
        items = body.get("payment_links") or []
        if items:
            pl = items[0]
            # An existing link for this ref means we already issued it -> settled.
            st = "settled" if pl.get("status") in ("created", "paid", "partially_paid") else pl.get("status", "unknown")
            return RazorpayResult(status=st, id=pl.get("id"), short_url=pl.get("short_url"), payment_ref=pl.get("id"))
        return RazorpayResult(status="unknown")


def verify_razorpay_signature(*, body: bytes, signature_header: str, secret: str | None = None) -> bool:
    """Verify a Razorpay webhook (HMAC-SHA256 of the raw body, X-Razorpay-Signature)."""
    secret = secret if secret is not None else settings.razorpay_webhook_secret
    if not secret:
        log.warning("RAZORPAY_WEBHOOK_SECRET not set — refusing payment webhook")
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header or "")
