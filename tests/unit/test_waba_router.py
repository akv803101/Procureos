"""WABA routing (Fix 06): the four-priority content-first cascade."""
from core.waba_router import route_incoming_whatsapp


class FakeHandlers:
    def __init__(self):
        self.calls = []

    async def handle_employee_rating(self, rating_id, button_id):
        self.calls.append(("rating", rating_id, button_id)); return "rated"

    async def handle_delivery_confirmed(self, order_id):
        self.calls.append(("delivered", order_id)); return "ok"

    async def handle_delivery_denied(self, order_id):
        self.calls.append(("denied", order_id)); return "ok"

    async def handle_vendor_quote_reply(self, goal_id, vendor_phone, message):
        self.calls.append(("quote", goal_id, vendor_phone, message)); return "quote"

    async def handle_vendor_optout(self, vendor_phone):
        self.calls.append(("optout", vendor_phone)); return "optout"

    async def push_unrouted_message(self, vendor_phone, message, reason):
        self.calls.append(("unrouted", vendor_phone, reason)); return "unrouted"


class _Goal:
    def __init__(self, id):
        self.id = id


class FakeStore:
    def __init__(self, by_partial=None, active=None):
        self._by_partial = by_partial or {}
        self._active = active or []

    async def get_goal_by_partial_id(self, partial, vendor_phone=None):
        return self._by_partial.get(partial)

    async def get_active_rfq_goals_for_vendor(self, sender):
        return self._active


async def test_p1_interactive_rating_button():
    h = FakeHandlers()
    payload = {"from": "+9111", "type": "interactive",
               "interactive": {"button_reply": {"id": "rate_good_rid123"}}}
    await route_incoming_whatsapp(payload, handlers=h, store=FakeStore())
    assert h.calls[0] == ("rating", "rid123", "rate_good_rid123")


async def test_p1_delivery_confirmed_button():
    h = FakeHandlers()
    payload = {"from": "+9111", "type": "interactive",
               "interactive": {"button_reply": {"id": "confirm_delivered_order-9"}}}
    await route_incoming_whatsapp(payload, handlers=h, store=FakeStore())
    assert h.calls[0] == ("delivered", "order-9")


async def test_p2_ref_code_routes_to_quote():
    h = FakeHandlers()
    store = FakeStore(by_partial={"abcd1234": _Goal("g-1")})
    payload = {"from": "+9111", "type": "text", "text": "Yes we can do REF:ABCD1234 at 15000"}
    await route_incoming_whatsapp(payload, handlers=h, store=store)
    assert h.calls[0][0] == "quote" and h.calls[0][1] == "g-1"


async def test_p3_optout_keyword():
    h = FakeHandlers()
    payload = {"from": "+9111", "type": "text", "text": "STOP"}
    await route_incoming_whatsapp(payload, handlers=h, store=FakeStore())
    assert h.calls[0] == ("optout", "+9111")


async def test_p4_single_active_rfq():
    h = FakeHandlers()
    store = FakeStore(active=[_Goal("g-7")])
    payload = {"from": "+9111", "type": "text", "text": "16500 mein denge"}
    await route_incoming_whatsapp(payload, handlers=h, store=store)
    assert h.calls[0][0] == "quote" and h.calls[0][1] == "g-7"


async def test_fallback_unrouted_when_multiple_active_and_no_ref():
    h = FakeHandlers()
    store = FakeStore(active=[_Goal("g-1"), _Goal("g-2")])
    payload = {"from": "+9111", "type": "text", "text": "some price maybe"}
    await route_incoming_whatsapp(payload, handlers=h, store=store)
    assert h.calls[0][0] == "unrouted"


async def test_ref_takes_priority_over_active_rfqs():
    h = FakeHandlers()
    store = FakeStore(by_partial={"abcd1234": _Goal("g-ref")},
                      active=[_Goal("a"), _Goal("b")])
    payload = {"from": "+9111", "type": "text", "text": "REF:ABCD1234 price 12000"}
    await route_incoming_whatsapp(payload, handlers=h, store=store)
    assert h.calls[0][1] == "g-ref"
