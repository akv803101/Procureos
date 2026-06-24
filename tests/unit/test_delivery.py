"""Delivery confirmation — state transitions + rating trigger."""
from datetime import datetime, timedelta, timezone

from core.db import Goal, InMemoryStore, Order
from core.delivery import mark_delivered, mark_delivery_failed
from tests.fakes import FakeRedis


def _setup(goal_status="in_transit"):
    now = datetime.now(timezone.utc)
    goal = Goal(id="g1", status=goal_status, category="fb", company_id="co1")
    order = Order(id="o1", goal_id="g1", vendor_id="v1", company_id="co1",
                  quoted_price=1000, promised_eta=now + timedelta(hours=2), status="in_transit")
    return InMemoryStore(goals={"g1": goal}, orders={"o1": order}), goal, order


async def test_mark_delivered_transitions_and_triggers_rating():
    store, goal, order = _setup()
    res = await mark_delivered("o1", store=store, redis=FakeRedis())
    assert res["status"] == "delivered"
    assert await store.get_goal_state("g1") == "delivered"
    assert order.status == "delivered" and order.delivered_at is not None
    rating = await store.get_rating(res["rating_id"])
    assert rating.order_id == "o1"


async def test_duplicate_delivery_is_idempotent():
    # A second 'delivered' webhook must not create a second rating/prompt.
    store, goal, order = _setup()
    first = await mark_delivered("o1", store=store, redis=FakeRedis())
    assert first["status"] == "delivered"
    second = await mark_delivered("o1", store=store, redis=FakeRedis())
    assert second["status"] == "skipped"
    assert len(await store.get_ratings_for_vendor("v1")) == 1


async def test_delivered_webhook_while_ordered_begins_transit_then_delivers():
    # An early 'delivered' confirmation (goal still 'ordered') is not dropped.
    store, goal, order = _setup(goal_status="ordered")
    order.status = "placed"
    res = await mark_delivered("o1", store=store, redis=FakeRedis())
    assert res["status"] == "delivered"
    assert await store.get_goal_state("g1") == "delivered"


async def test_mark_delivery_failed_transitions():
    store, goal, order = _setup()
    res = await mark_delivery_failed("o1", store=store, redis=FakeRedis())
    assert res["status"] == "delivery_failed"
    assert await store.get_goal_state("g1") == "delivery_failed"
    assert order.status == "failed"
