"""rfq_timeout worker: rank a stuck single-quote goal; time out a no-reply RFQ.
(Fixes the 'only one vendor replied -> stuck forever' dead-end.)"""
import worker.rfq_timeout as rt
from core.db import Goal, InMemoryStore
from tests.fakes import FakeRedis


async def test_ranks_stuck_single_quote_goal(monkeypatch):
    g = Goal(id="g1", status="quotes_received", category="fb", company_id="c1")
    store = InMemoryStore(goals={"g1": g})
    await store.add_collected_quote("g1", {"vendor_id": "v1", "price": 100})

    called = []

    async def fake_rank(goal_id, **kw):
        called.append(goal_id)
        return {"ranked": True}

    monkeypatch.setattr(rt, "_rank_and_request_approval", fake_rank)
    res = await rt.run_rfq_timeout(store=store, redis=FakeRedis())
    assert res["ranked"] == 1 and called == ["g1"]      # single quote no longer dead-ends


async def test_does_not_rank_quotes_received_with_zero_quotes(monkeypatch):
    g = Goal(id="g1", status="quotes_received", category="fb", company_id="c1")  # no quotes
    store = InMemoryStore(goals={"g1": g})
    monkeypatch.setattr(rt, "_rank_and_request_approval",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("should not rank")))
    res = await rt.run_rfq_timeout(store=store, redis=FakeRedis())
    assert res["ranked"] == 0


async def test_times_out_no_reply_pending_rfq():
    g = Goal(id="g2", status="pending_rfq", category="fb", company_id="c1")
    store = InMemoryStore(goals={"g2": g})
    res = await rt.run_rfq_timeout(store=store, redis=FakeRedis())
    assert res["timed_out"] == 1
    assert await store.get_goal_state("g2") == "rfq_timeout"
