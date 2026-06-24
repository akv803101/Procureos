"""Fix 04 — approval TTL + price re-fetch on expiry.

Includes the regression test that proves the `.seconds` -> `.total_seconds()`
correction: a 25-hour-old approval against a 24h TTL must be treated as expired.
With the original `.seconds` bug it would read ~1h elapsed and wrongly pass.
"""
from datetime import datetime, timedelta, timezone

from core.approval_manager import check_approval_before_payment
from core.db import Goal, InMemoryStore
from tests.fakes import FakeRedis, FakeSpecialistAgent


def _store_with_goal(category, minutes_ago=None, hours_ago=None, status="approved"):
    if hours_ago is not None:
        approved_at = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
    else:
        approved_at = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago or 0)
    goal = Goal(
        id="g-1",
        status=status,
        category=category,
        approved_at=approved_at,
        parsed_intent={"category": category, "quantity": 2},
    )
    return InMemoryStore(goals={"g-1": goal}), goal


class _Notifier:
    def __init__(self):
        self.calls = []

    async def __call__(self, *, goal_id, note):
        self.calls.append({"goal_id": goal_id, "note": note})


async def test_fresh_approval_passes_without_refetch():
    store, _ = _store_with_goal("flights", minutes_ago=5)   # ttl 30m
    agent = FakeSpecialistAgent()
    notifier = _Notifier()

    ok = await check_approval_before_payment(
        "g-1", store=store, specialist_agent=agent,
        send_approval_notification=notifier, redis=FakeRedis(),
    )
    assert ok is True
    assert agent.search_calls == []     # no re-fetch
    assert notifier.calls == []


async def test_expired_flight_approval_blocks_and_refetches():
    store, goal = _store_with_goal("flights", minutes_ago=40)   # > 30m ttl
    agent = FakeSpecialistAgent(options=[{"vendor_id": "v_fresh", "price": 8800}])
    notifier = _Notifier()

    ok = await check_approval_before_payment(
        "g-1", store=store, specialist_agent=agent,
        send_approval_notification=notifier, redis=FakeRedis(),
    )
    assert ok is False                              # payment blocked
    assert agent.search_calls == [goal.parsed_intent]
    assert goal.options == [{"vendor_id": "v_fresh", "price": 8800}]
    assert notifier.calls and "expired" in notifier.calls[0]["note"].lower()
    assert goal.status == "pending_approval"        # routed back for re-approval


async def test_24h_ttl_is_measured_across_day_boundary():
    # REGRESSION GUARD for the .seconds bug: water TTL = 24h.
    # 23h old -> still fresh.
    store_fresh, _ = _store_with_goal("water", hours_ago=23)
    assert await check_approval_before_payment(
        "g-1", store=store_fresh, specialist_agent=FakeSpecialistAgent(),
        send_approval_notification=_Notifier(), redis=FakeRedis(),
    ) is True

    # 25h old -> expired. (The old .seconds code would compute ~1h and pass.)
    store_stale, _ = _store_with_goal("water", hours_ago=25)
    assert await check_approval_before_payment(
        "g-1", store=store_stale, specialist_agent=FakeSpecialistAgent(),
        send_approval_notification=_Notifier(), redis=FakeRedis(),
    ) is False


async def test_naive_approved_at_is_treated_as_utc_without_crashing():
    # DB-style aware timestamps are normal; a naive one must not raise TypeError.
    goal = Goal(
        id="g-1", status="approved", category="flights",
        approved_at=datetime.utcnow() - timedelta(minutes=5),  # naive, recent
        parsed_intent={},
    )
    store = InMemoryStore(goals={"g-1": goal})
    ok = await check_approval_before_payment(
        "g-1", store=store, specialist_agent=FakeSpecialistAgent(),
        send_approval_notification=_Notifier(), redis=FakeRedis(),
    )
    assert ok is True


async def test_unknown_category_uses_default_ttl():
    # 'generic' is not in APPROVAL_TTL -> default 4h. 3h old -> still fresh.
    store, _ = _store_with_goal("generic", hours_ago=3)
    assert await check_approval_before_payment(
        "g-1", store=store, specialist_agent=FakeSpecialistAgent(),
        send_approval_notification=_Notifier(), redis=FakeRedis(),
    ) is True
