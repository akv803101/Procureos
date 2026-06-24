"""Approval orchestration + magic-link tokens (Fix 12) + Fixes 01-05 in concert."""
from datetime import datetime, timedelta, timezone

import pytest

from core.approval_manager import (
    approve_goal,
    consume_approval_token,
    generate_approval_token,
    reject_goal,
)
from core.db import Goal, InMemoryStore
from core.errors import (
    ApprovalTokenError,
    BudgetExceededError,
    OptionNotFoundError,
    StateConflictError,
)
from tests.fakes import FakePayment, FakeRedis, FakeSpecialistAgent, FakeVolopayClient


def _pending_goal(**over) -> Goal:
    base = dict(
        id="g1", status="pending_approval", category="fb", company_id="co1",
        approval_sent_at=datetime.now(timezone.utc),
        parsed_intent={"gst_required": True},
        options=[
            {"vendor_id": "v1", "price": 15000, "estimated_final_price_with_gst": 17700, "rank": 1},
            {"vendor_id": "v2", "price": 18000, "estimated_final_price_with_gst": 21240, "rank": 2},
        ],
    )
    base.update(over)
    return Goal(**base)


def _store(goal: Goal | None = None, limit=20000, spent=0):
    goal = goal or _pending_goal()
    store = InMemoryStore(budgets={("co1", "fb"): limit}, spent={("co1", "fb"): spent}, goals={goal.id: goal})
    return store, goal


# ── magic-link tokens (Fix 12) ──────────────────────────────────────────────
async def test_token_roundtrip():
    store, _ = _store()
    tok = await generate_approval_token("g1", "approver1", store=store)
    assert await consume_approval_token(tok, store=store) == "g1"


async def test_token_is_single_use():
    store, _ = _store()
    tok = await generate_approval_token("g1", store=store)
    await consume_approval_token(tok, store=store)
    with pytest.raises(ApprovalTokenError):
        await consume_approval_token(tok, store=store)


async def test_token_expiry_enforced():
    store, _ = _store()
    tok = await generate_approval_token("g1", store=store, ttl_seconds=-1)  # already expired
    with pytest.raises(ApprovalTokenError):
        await consume_approval_token(tok, store=store)


async def test_unknown_token_rejected():
    store, _ = _store()
    with pytest.raises(ApprovalTokenError):
        await consume_approval_token("nope", store=store)


# ── approve_goal (Fixes 01-05) ──────────────────────────────────────────────
async def test_approve_happy_path_orders_and_spends():
    store, _ = _store()
    res = await approve_goal(
        "g1", "v1", store=store, redis=FakeRedis(),
        volopay_client=FakeVolopayClient([FakePayment("settled")]))
    assert res["status"] == "ordered"
    assert res["card_limit"] == round(15000 * 1.28, 2)        # Fix 03
    assert res["payment_status"] == "settled"
    assert await store.get_goal_state("g1") == "ordered"      # Fix 05 transitions
    assert store.spend_records[0]["amount"] == 17700          # Fix 02 recorded the GST-inclusive amount


async def test_approve_rejects_wrong_state():
    store, _ = _store(_pending_goal(status="ordered"))
    with pytest.raises(StateConflictError):
        await approve_goal("g1", "v1", store=store, redis=FakeRedis(), volopay_client=FakeVolopayClient())


async def test_approve_unknown_option():
    store, _ = _store()
    with pytest.raises(OptionNotFoundError):
        await approve_goal("g1", "v999", store=store, redis=FakeRedis(), volopay_client=FakeVolopayClient())


async def test_over_budget_blocks_and_marks_payment_failed():
    store, _ = _store(spent=19000)   # only 1000 left; option needs 17700
    with pytest.raises(BudgetExceededError):
        await approve_goal("g1", "v1", store=store, redis=FakeRedis(),
                           volopay_client=FakeVolopayClient([FakePayment("settled")]))
    assert await store.get_goal_state("g1") == "payment_failed"
    assert store.spend_records == []


async def test_stale_options_require_reapproval_no_payment():
    # fb TTL is 4h; options presented 5h ago -> stale (Fix 04).
    goal = _pending_goal(approval_sent_at=datetime.now(timezone.utc) - timedelta(hours=5))
    store, _ = _store(goal)
    notes = []

    async def notifier(*, goal_id, note):
        notes.append(note)

    res = await approve_goal(
        "g1", "v1", store=store, redis=FakeRedis(),
        volopay_client=FakeVolopayClient([FakePayment("settled")]),
        specialist_agent=FakeSpecialistAgent(), send_approval_notification=notifier)
    assert res["status"] == "re_approval_needed"
    assert store.spend_records == []      # never paid
    assert notes                          # approver was asked to re-approve


async def test_reject_cancels_goal():
    store, _ = _store()
    res = await reject_goal("g1", "too expensive", store=store, redis=FakeRedis())
    assert res["status"] == "cancelled"
    assert await store.get_goal_state("g1") == "cancelled"
