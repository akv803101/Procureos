"""GoalProcessor capstone: discover+dispatch, quote collection -> rank -> approval."""
import core.waba_handlers as waba_handlers
from agents.orchestrator import on_quote_collected, process_goal
from agents.specialist.places_agent import PlacesAgent
from core.db import Company, Goal, InMemoryStore
from core.errors import QuoteAmbiguousError
from tests.fakes import FakeRedis, FakeRouter


def _company():
    return Company(id="c1", slack_approval_channel="#procurement",
                   budget_policies={"fb": 20000, "default": 5000})


# ── process_goal: discovery + dispatch ──────────────────────────────────────
async def test_process_goal_discovers_and_dispatches():
    goal = Goal(id="g1", status="processing", category="fb", company_id="c1",
                raw_input="snacks for 50", parsed_intent={"category": "fb", "location": "BLR", "gst_required": True})
    store = InMemoryStore(goals={"g1": goal}, companies={"c1": _company()})

    async def fake_search(query):
        return [{"google_place_id": "p1", "name": "A", "phone": "+9111"},
                {"google_place_id": "p2", "name": "B", "phone": "+9122"}]

    sent = []

    async def wsend(to, body):
        sent.append(to); return {"ok": True}

    res = await process_goal("g1", store=store, redis=FakeRedis(),
                             places_agent=PlacesAgent(search_fn=fake_search),
                             router=FakeRouter(text="Please share your quote."), whatsapp_send_fn=wsend)
    assert res["status"] == "pending_rfq"
    assert res["dispatched"] == 2
    assert len(sent) == 2
    assert await store.get_goal_state("g1") == "pending_rfq"


async def test_process_goal_no_vendors_escalates():
    goal = Goal(id="g1", status="processing", category="fb", company_id="c1", parsed_intent={"category": "fb"})
    store = InMemoryStore(goals={"g1": goal}, companies={"c1": _company()})

    async def empty_search(query):
        return []

    res = await process_goal("g1", store=store, redis=FakeRedis(),
                             places_agent=PlacesAgent(search_fn=empty_search), router=FakeRouter())
    assert res["status"] == "operator_escalated"
    assert await store.get_goal_state("g1") == "operator_escalated"


# ── quote collection -> rank -> approval card ───────────────────────────────
_RANKED = ('{"ranked_options":[{"vendor_id":"p1","rank":1,'
           '"estimated_final_price_with_gst":17700,"recommendation_label":"Preferred",'
           '"recommendation_reason":"best"}],"recommendation_summary":"go p1"}')


async def test_quotes_collected_then_ranked_and_approval_sent():
    goal = Goal(id="g1", status="pending_rfq", category="fb", company_id="c1",
                raw_input="snacks", parsed_intent={"gst_required": True}, budget_limit=20000)
    store = InMemoryStore(goals={"g1": goal}, companies={"c1": _company()})
    slack_sent = []

    async def slack(channel, blocks, text):
        slack_sent.append(channel); return {"ok": True}

    router = FakeRouter(text=_RANKED)
    r1 = await on_quote_collected("g1", {"vendor_id": "p1", "price": 15000},
                                  store=store, redis=FakeRedis(), router=router, slack_send_fn=slack)
    assert r1["collected"] == 1 and r1["ranked"] is False        # below MIN_QUOTES_TO_RANK
    assert await store.get_goal_state("g1") == "quotes_received"

    r2 = await on_quote_collected("g1", {"vendor_id": "p2", "price": 18000},
                                  store=store, redis=FakeRedis(), router=router, slack_send_fn=slack)
    assert r2["ranked"] is True
    assert await store.get_goal_state("g1") == "pending_approval"
    assert slack_sent == ["#procurement"]
    assert goal.approval_sent_at is not None                      # Fix 04 clock stamped
    assert goal.options[0]["vendor_id"] == "p1"


# ── inbound quote handler wiring (waba_handlers) ────────────────────────────
async def test_handle_vendor_quote_reply_routes_parsed_quote(monkeypatch):
    goal = Goal(id="g1", status="pending_rfq", category="fb", company_id="c1",
                parsed_intent={"quantity": 50, "location": "BLR"}, budget_limit=20000)
    store = InMemoryStore(goals={"g1": goal}, companies={"c1": _company()})
    vendor_id = await store.upsert_vendor({"google_place_id": "p1", "name": "A",
                                           "phone": "+9111", "category": "fb"})
    collected = []

    async def fake_parse(message, **kw):
        return {"price": 15000, "confidence": 0.9}

    async def fake_collect(goal_id, quote, **kw):
        collected.append(quote); return {"collected": 1, "ranked": False}

    monkeypatch.setattr(waba_handlers, "parse_quote", fake_parse)
    monkeypatch.setattr(waba_handlers, "on_quote_collected", fake_collect)

    h = waba_handlers.DefaultWabaHandlers(store=store, redis=FakeRedis())
    await h.handle_vendor_quote_reply("g1", "+9111", "15000 with GST")
    assert collected and collected[0]["vendor_phone"] == "+9111"
    assert collected[0]["vendor_id"] == vendor_id      # resolved phone -> persisted vendor id


async def test_handle_vendor_quote_reply_unknown_vendor_to_operator(monkeypatch):
    goal = Goal(id="g1", status="pending_rfq", category="fb", company_id="c1", parsed_intent={})
    store = InMemoryStore(goals={"g1": goal}, companies={"c1": _company()})

    async def fake_parse(message, **kw):
        return {"price": 15000, "confidence": 0.9}

    monkeypatch.setattr(waba_handlers, "parse_quote", fake_parse)
    h = waba_handlers.DefaultWabaHandlers(store=store, redis=FakeRedis())
    res = await h.handle_vendor_quote_reply("g1", "+9999", "15000")   # phone not persisted
    assert res["status"] == "operator_queue" and res["reason"] == "unknown_vendor"


async def test_upsert_vendor_dedups_by_place_id_and_resolves_phone():
    store = InMemoryStore()
    v1 = await store.upsert_vendor({"google_place_id": "p1", "name": "A", "phone": "+9111"})
    v1_again = await store.upsert_vendor({"google_place_id": "p1", "name": "A (updated)", "phone": "+9111"})
    v2 = await store.upsert_vendor({"google_place_id": "p2", "name": "B", "phone": "+9122"})
    assert v1 == v1_again and v1 != v2                 # dedup by google_place_id (Fix 08)
    assert await store.get_vendor_id_by_phone("+9111") == v1
    assert await store.get_vendor_id_by_phone("+9999") is None


async def test_handle_vendor_quote_reply_ambiguous_to_operator(monkeypatch):
    goal = Goal(id="g1", status="pending_rfq", category="fb", company_id="c1", parsed_intent={})
    store = InMemoryStore(goals={"g1": goal}, companies={"c1": _company()})

    async def fake_parse(message, **kw):
        raise QuoteAmbiguousError("range price")

    monkeypatch.setattr(waba_handlers, "parse_quote", fake_parse)
    h = waba_handlers.DefaultWabaHandlers(store=store, redis=FakeRedis())
    res = await h.handle_vendor_quote_reply("g1", "+9111", "16-17k depending")
    assert res["status"] == "operator_queue"
