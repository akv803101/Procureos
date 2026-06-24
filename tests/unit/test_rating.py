"""Rating collection — system signals, prompt, score recalc on reply."""
from datetime import datetime, timedelta, timezone

from core.db import Goal, InMemoryStore, Order, Rating
from core.rating import on_delivery_confirmed, on_rating_received
from tests.fakes import FakeRedis


def _order(id="o1", vendor="v1", quoted=1000, final=1000, eta_h=2, delivered_h=1, resp=25) -> Order:
    now = datetime.now(timezone.utc)
    return Order(id=id, goal_id="g1", vendor_id=vendor, company_id="co1",
                 quoted_price=quoted, final_price=final,
                 promised_eta=now + timedelta(hours=eta_h),
                 delivered_at=now + timedelta(hours=delivered_h),
                 status="delivered", vendor_response_time_mins=resp)


async def test_delivery_creates_rating_with_system_signals():
    order = _order()
    store = InMemoryStore(orders={"o1": order})
    res = await on_delivery_confirmed("o1", store=store)
    r = await store.get_rating(res["rating_id"])
    assert r.delivered_on_time is True       # delivered 1h < eta 2h
    assert r.price_accurate is True          # final 1000 <= quoted 1000
    assert r.is_repeat_order is False        # no prior orders
    assert order.rating_sent is True
    assert res["prompt_sent"] is False       # no notify_to


async def test_repeat_detected_and_prompt_sent():
    store = InMemoryStore(orders={"o1": _order(id="o1"), "o2": _order(id="o2")})
    sent = []

    async def fake_send(to, body, buttons):
        sent.append((to, buttons))
        return {"ok": True}

    res = await on_delivery_confirmed("o2", store=store, notify_to="+9111",
                                      vendor_name="Acme", send_fn=fake_send)
    r = await store.get_rating(res["rating_id"])
    assert r.is_repeat_order is True
    assert res["prompt_sent"] is True
    assert sent[0][0] == "+9111"
    assert any(b["id"] == f"rate_good_{res['rating_id']}" for b in sent[0][1])


async def test_late_and_overpriced_signals():
    order = _order(eta_h=1, delivered_h=3, quoted=1000, final=1200)
    store = InMemoryStore(orders={"o1": order})
    res = await on_delivery_confirmed("o1", store=store)
    r = await store.get_rating(res["rating_id"])
    assert r.delivered_on_time is False
    assert r.price_accurate is False


async def test_rating_received_maps_recomputes_and_completes_goal():
    goal = Goal(id="g1", status="delivered", category="fb", company_id="co1")
    store = InMemoryStore(orders={"o1": _order()}, goals={"g1": goal})
    res = await on_delivery_confirmed("o1", store=store)
    # Two more ratings for the same vendor so the score crosses the >=3 gate.
    for oid in ("o2", "o3"):
        await store.create_rating(Rating(id="", order_id=oid, vendor_id="v1", company_id="co1",
                                         overall_rating=5, delivered_on_time=True,
                                         price_accurate=True, response_time_mins=20))
    out = await on_rating_received(res["rating_id"], satisfied=True, store=store, redis=FakeRedis())
    assert out["overall_rating"] == 5
    assert out["vendor_score"]["score"] is not None
    r = await store.get_rating(res["rating_id"])
    assert r.satisfied is True and r.overall_rating == 5
    # The rating drives the goal to its terminal state.
    assert await store.get_goal_state("g1") == "completed"


async def test_rating_issue_maps_to_two():
    goal = Goal(id="g1", status="delivered", category="fb", company_id="co1")
    store = InMemoryStore(orders={"o1": _order()}, goals={"g1": goal})
    res = await on_delivery_confirmed("o1", store=store)
    out = await on_rating_received(res["rating_id"], satisfied=False, store=store, redis=FakeRedis())
    assert out["overall_rating"] == 2
    assert await store.get_goal_state("g1") == "completed"
