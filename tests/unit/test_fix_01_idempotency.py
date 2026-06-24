"""Fix 01 — payment idempotency + safe retry."""
import asyncio

import pytest

from core.errors import PaymentDuplicateError
from services.payment import (
    issue_volopay_payment,
    payment_idempotency_key,
    payment_with_retry,
)
from tests.fakes import FakePayment, FakeVolopayClient


def test_idempotency_key_is_deterministic():
    # Same order -> same key (so a retry reuses it and Volopay dedupes).
    assert payment_idempotency_key("order-1") == payment_idempotency_key("order-1")
    # Different orders -> different keys.
    assert payment_idempotency_key("order-1") != payment_idempotency_key("order-2")


def test_settled_payment_returns_response_with_one_call():
    client = FakeVolopayClient(script=[FakePayment("settled")])
    result = issue_volopay_payment("order-1", 15000, "vendor-1", client=client)
    assert result.status == "settled"
    assert len(client.create_calls) == 1
    # The call carried the deterministic key.
    assert client.create_calls[0]["idempotency_key"] == payment_idempotency_key("order-1")


def test_duplicate_but_already_settled_returns_existing_without_paying_again():
    client = FakeVolopayClient(
        script=[FakePayment("duplicate")],
        existing=FakePayment("settled", id="pay_original"),
    )
    result = issue_volopay_payment("order-1", 15000, "vendor-1", client=client)
    assert result.id == "pay_original"  # the original payment, not a new charge
    assert result.status == "settled"


def test_duplicate_with_unknown_state_raises():
    client = FakeVolopayClient(
        script=[FakePayment("duplicate")],
        existing=FakePayment("processing"),  # not settled -> unknown/unsafe
    )
    with pytest.raises(PaymentDuplicateError):
        issue_volopay_payment("order-1", 15000, "vendor-1", client=client)


async def test_retry_never_retries_on_duplicate():
    client = FakeVolopayClient(
        script=[FakePayment("duplicate")],
        existing=FakePayment("processing"),
    )
    with pytest.raises(PaymentDuplicateError):
        await payment_with_retry("order-1", 15000, "vendor-1", client=client)
    # Exactly one create attempt — a duplicate is never retried (double-charge guard).
    assert len(client.create_calls) == 1


async def test_retry_recovers_after_transient_error(monkeypatch):
    # Avoid the real 10-minute sleep between retries.
    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    client = FakeVolopayClient(script=[RuntimeError("network blip"), FakePayment("settled")])
    result = await payment_with_retry("order-1", 15000, "vendor-1", client=client)
    assert result.status == "settled"
    assert len(client.create_calls) == 2


async def test_retry_escalates_and_raises_after_exhausting_attempts(monkeypatch):
    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    escalations = []

    async def fake_escalate(order_id, reason):
        escalations.append((order_id, reason))

    client = FakeVolopayClient(
        script=[RuntimeError("e1"), RuntimeError("e2"), RuntimeError("e3")]
    )
    with pytest.raises(RuntimeError):
        await payment_with_retry("order-9", 100, "vendor-1", client=client, escalate=fake_escalate)
    assert len(client.create_calls) == 3        # MAX_RETRIES attempts
    assert escalations == [("order-9", "e3")]   # escalated once, on the last failure
