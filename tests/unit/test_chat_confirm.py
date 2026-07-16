"""Chat → real goal: confirm_and_create_goal persists a goal with a per-goal REF,
persists the chosen vendor, DISPATCHES the WhatsApp template, and the reply hub
surfaces inbound quotes (the chat-vs-pipeline unification, A1 + live send/reply)."""
import api.routes.chat as chat
import core.clients as clients
from core.db import Goal, InMemoryStore
from tests.fakes import FakeRedis


def _sess():
    return {"history": [], "goal_text": "snacks for 100 people",
            "last": {"intent": {"category": "fb", "quantity": 100, "location": "Bengaluru",
                                "delivery_address": "12 MG Road, Prestige Tower"},
                     "vendors": [{"name": "BBQ Catering", "phone": "+9111"}],
                     "rfq": "Hi BBQ Catering, ...", "recipient": "BBQ Catering"}}


class _FakeReq:
    def __init__(self, params):
        self.query_params = params


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


def _patch_sender(monkeypatch):
    """Capture WhatsApp template sends instead of hitting the network."""
    sent = []

    async def fake_send_template(to, template_name, *, language="en", body_params, send_fn=None):
        sent.append({"to": to, "template": template_name, "params": list(body_params)})
        return {"ok": True}

    monkeypatch.setattr("services.whatsapp.send_template", fake_send_template)
    return sent


async def test_confirm_dispatches_and_persists_vendor(monkeypatch):
    store = InMemoryStore()
    monkeypatch.setattr(chat, "get_store", lambda: store)
    monkeypatch.setattr(clients, "get_redis", lambda: FakeRedis())
    monkeypatch.setattr(chat.settings, "chat_mitra_api_key", "cm-test")
    monkeypatch.setattr(chat.settings, "rfq_test_recipient", "")
    sent = _patch_sender(monkeypatch)

    sess = _sess()
    sess["last"]["dispatch"] = [{"name": "BBQ Catering", "phone": "+919111111111",
                                 "google_place_id": "pid1", "category": "fb", "city": "Bengaluru"}]
    summary, data = await chat._confirm_and_create_goal(sess, {})

    assert data["goal"]["sent"] is True and "SENT" in summary
    assert sent and sent[0]["to"] == "+919111111111"           # dispatched to the real vendor phone
    assert sent[0]["template"] == "rfq_first_contact_v1"
    assert data["goal"]["ref"] in sent[0]["params"]            # the REF travels in the template body
    assert await store.get_vendor_id_by_phone("919111111111")  # persisted -> replies attribute back
    assert sess["goals"][0]["id"] == data["goal"]["id"]        # registered for the reply hub


async def test_confirm_test_recipient_override_routes_to_own_number(monkeypatch):
    store = InMemoryStore()
    monkeypatch.setattr(chat, "get_store", lambda: store)
    monkeypatch.setattr(clients, "get_redis", lambda: FakeRedis())
    monkeypatch.setattr(chat.settings, "chat_mitra_api_key", "cm-test")
    monkeypatch.setattr(chat.settings, "rfq_test_recipient", "+919000000000")
    sent = _patch_sender(monkeypatch)

    sess = _sess()
    sess["last"]["dispatch"] = [{"name": "BBQ Catering", "phone": "+919111111111", "google_place_id": "pid1"},
                                {"name": "Other Co", "phone": "+919222222222", "google_place_id": "pid2"}]
    _, data = await chat._confirm_and_create_goal(sess, {"vendor": "all"})

    assert [s["to"] for s in sent] == ["+919000000000"]        # one self-test send to the own number
    assert data["goal"]["sent"] is True
    assert await store.get_vendor_id_by_phone("919000000000")  # reply from the test number attributes


async def test_chat_updates_surfaces_new_quote_once(monkeypatch):
    store = InMemoryStore()
    monkeypatch.setattr(chat, "get_store", lambda: store)
    store._goals["g1"] = Goal(id="g1", status="quotes_received", category="fb", company_id="c1")
    await store.add_collected_quote("g1", {"price": 180, "price_includes_gst": True, "notes": "per plate"})

    sid = "sess-updates-1"
    chat._SESSIONS[sid] = {"history": [], "last": None, "goal_text": None,
                           "goals": [{"id": "g1", "ref": "ABCD1234", "recipient": "BBQ Catering"}], "seen": {}}
    try:
        out = await chat.chat_updates(_FakeReq({"session": sid}))
        assert len(out["updates"]) == 1
        q = out["updates"][0]["new_quotes"][0]
        assert q["price"] == 180 and q["gst_incl"] is True     # the reply surfaces in chat
        assert (await chat.chat_updates(_FakeReq({"session": sid})))["updates"] == []  # not re-shown
    finally:
        chat._SESSIONS.pop(sid, None)
