"""Chat → real goal: confirm_and_create_goal persists a goal with a per-goal REF
and advances it to pending_rfq (the chat-vs-pipeline unification, A1)."""
import api.routes.chat as chat
import core.clients as clients
from core.db import InMemoryStore
from tests.fakes import FakeRedis


def _sess():
    return {"history": [], "goal_text": "snacks for 100 people",
            "last": {"intent": {"category": "fb", "quantity": 100, "location": "Bengaluru",
                                "delivery_address": "12 MG Road, Prestige Tower"},
                     "vendors": [{"name": "BBQ Catering", "phone": "+9111"}],
                     "rfq": "Hi BBQ Catering, ...", "recipient": "BBQ Catering"}}


async def test_get_or_create_company_is_idempotent():
    store = InMemoryStore()
    a = await store.get_or_create_company("IntelliBridge (demo)")
    b = await store.get_or_create_company("IntelliBridge (demo)")
    assert a == b                                   # same company reused, not duplicated


async def test_confirm_creates_persisted_goal_with_real_ref(monkeypatch):
    store = InMemoryStore()
    monkeypatch.setattr(chat, "get_store", lambda: store)
    monkeypatch.setattr(clients, "get_redis", lambda: FakeRedis())

    summary, data = await chat._confirm_and_create_goal(_sess(), {})

    assert "Saved goal" in summary
    g = data["goal"]
    assert g["status"] == "pending_rfq" and g["queued"] is True
    assert g["recipient"] == "BBQ Catering"
    assert g["ref"] and g["ref"] != "pending"       # a REAL per-goal ref, not the preview placeholder
    assert g["ref"] in g["rfq"]                      # the queued RFQ carries the real ref
    assert await store.get_goal_state(g["id"]) == "pending_rfq"   # persisted + advanced


async def test_confirm_without_search_asks_to_search_first(monkeypatch):
    store = InMemoryStore()
    monkeypatch.setattr(chat, "get_store", lambda: store)
    summary, data = await chat._confirm_and_create_goal({"history": [], "last": None}, {})
    assert "search" in summary.lower() and data == {}
