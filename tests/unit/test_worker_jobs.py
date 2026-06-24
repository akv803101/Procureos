"""Worker jobs — delivery polling + score reconcile."""
from datetime import datetime, timedelta, timezone

from core.db import Goal, InMemoryStore, Order, Rating
from tests.fakes import FakeRedis
from worker.delivery_tracker import poll_deliveries
from worker.score_updater import reconcile_vendor_scores


async def test_poll_marks_delivered_when_signal_true():
    now = datetime.now(timezone.utc)
    goal = Goal(id="g1", status="in_transit", category="fb", company_id="co1")
    order = Order(id="o1", goal_id="g1", vendor_id="v1", company_id="co1", quoted_price=1000,
                  promised_eta=now + timedelta(hours=2), status="in_transit")
    store = InMemoryStore(goals={"g1": goal}, orders={"o1": order})

    async def always_delivered(o):
        return True

    res = await poll_deliveries(store=store, redis=FakeRedis(), is_delivered_fn=always_delivered)
    assert res["delivered"] == 1
    assert order.status == "delivered"
    assert await store.get_goal_state("g1") == "delivered"


async def test_poll_starts_placed_orders_into_transit():
    # A paid order (placed, goal 'ordered') is advanced to in_transit by the sweep.
    goal = Goal(id="g1", status="ordered", category="fb", company_id="co1")
    order = Order(id="o1", goal_id="g1", vendor_id="v1", company_id="co1", status="placed")
    store = InMemoryStore(goals={"g1": goal}, orders={"o1": order})
    res = await poll_deliveries(store=store, redis=FakeRedis())
    assert res["started"] == 1
    assert order.status == "in_transit"
    assert await store.get_goal_state("g1") == "in_transit"


async def test_poll_is_noop_for_in_transit_without_signal():
    order = Order(id="o1", goal_id="g1", vendor_id="v1", company_id="co1", status="in_transit")
    store = InMemoryStore(orders={"o1": order})
    res = await poll_deliveries(store=store, redis=FakeRedis())
    assert res == {"started": 0, "delivered": 0}
    assert order.status == "in_transit"


async def test_reconcile_recomputes_scores():
    store = InMemoryStore()
    for _ in range(3):
        await store.create_rating(Rating(id="", order_id="o", vendor_id="v1", company_id="co1",
                                         overall_rating=5, delivered_on_time=True,
                                         price_accurate=True, response_time_mins=20))
    out = await reconcile_vendor_scores(["v1"], store=store)
    assert out["v1"]["score"] is not None
