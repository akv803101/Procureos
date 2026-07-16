"""End-to-end (in-memory) proof that the live loop connects:

  chat confirm  ->  RFQ dispatched + chosen vendor persisted
  vendor reply  ->  attributed by REF + parsed + stored  (real waba_router + handlers)
  chat hub      ->  the quote surfaces back in the chat

This is the #5 (send) + #6 (reply hub) wiring, exercised through the SAME code the
webhook runs — only the outbound sender and the LLM quote-parser are stubbed.
"""
import api.routes.chat as chat
import core.clients as clients
import core.waba_handlers as waba_handlers
from core.db import InMemoryStore
from core.refcodes import ref_code
from core.waba_handlers import DefaultWabaHandlers
from core.waba_router import route_incoming_whatsapp
from services.whatsapp import normalize_inbound
from tests.fakes import FakeRedis


class _Req:
    def __init__(self, params):
        self.query_params = params


async def test_send_then_reply_surfaces_in_chat(monkeypatch):
    store = InMemoryStore()
    monkeypatch.setattr(chat, "get_store", lambda: store)
    monkeypatch.setattr(clients, "get_redis", lambda: FakeRedis())
    monkeypatch.setattr(chat.settings, "chat_mitra_api_key", "cm-test")
    monkeypatch.setattr(chat.settings, "rfq_test_recipient", "")

    sent = []

    async def fake_send_template(to, template_name, *, language="en", body_params, send_fn=None):
        sent.append(to)
        return {"ok": True}

    monkeypatch.setattr("services.whatsapp.send_template", fake_send_template)

    # 1) chat confirm -> goal saved, RFQ template dispatched, vendor persisted
    sess = {"history": [], "goal_text": "100 snacks", "goals": [], "seen": {},
            "last": {"intent": {"category": "fb", "quantity": 100, "location": "Bengaluru",
                                "delivery_address": "12 MG Road, Prestige Tower"},
                     "rfq": "Hi ...", "recipient": "BBQ Catering",
                     "dispatch": [{"name": "BBQ Catering", "phone": "+919111111111",
                                   "google_place_id": "pid1", "category": "fb", "city": "Bengaluru"}]}}
    _, data = await chat._confirm_and_create_goal(sess, {})
    goal_id, ref = data["goal"]["id"], data["goal"]["ref"]
    assert sent == ["+919111111111"]              # actually dispatched
    assert ref == ref_code(goal_id)               # the REF the vendor sees maps back to this goal

    # 2) vendor replies on WhatsApp quoting the REF -> real router + handlers attribute & store it
    async def fake_parse_quote(message, *, category, quantity, location, budget, goal_id=None, router=None):
        return {"price": 180, "price_includes_gst": True, "notes": "per plate", "confidence": 0.95}

    monkeypatch.setattr(waba_handlers, "parse_quote", fake_parse_quote)

    inbound = normalize_inbound({"event": "message.received",
                                 "data": {"from": "919111111111", "type": "text",
                                          "text": {"body": f"Rs 180 per plate incl GST REF:{ref}"}}})
    handlers = DefaultWabaHandlers(store=store, redis=FakeRedis())
    res = await route_incoming_whatsapp(inbound, handlers=handlers, store=store)
    assert res.get("collected") == 1              # landed on the right goal, not the operator queue

    # 3) the chat reply hub surfaces that quote (once)
    chat._SESSIONS["live-loop-1"] = sess
    try:
        out = await chat.chat_updates(_Req({"session": "live-loop-1"}))
        assert len(out["updates"]) == 1
        u = out["updates"][0]
        assert u["ref"] == ref and u["status"] == "quotes_received"
        assert u["new_quotes"][0]["price"] == 180 and u["new_quotes"][0]["gst_incl"] is True
        assert (await chat.chat_updates(_Req({"session": "live-loop-1"})))["updates"] == []
    finally:
        chat._SESSIONS.pop("live-loop-1", None)
