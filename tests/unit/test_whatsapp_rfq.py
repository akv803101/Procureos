"""WhatsApp send + RFQ dispatch (parallel send, REF codes, phone skip)."""
import re

from agents.orchestrator import dispatch_rfqs, ref_code_for_goal
from services import whatsapp
from tests.fakes import FakeRouter


def test_ref_code_is_8_alnum_chars_and_deterministic():
    ref = ref_code_for_goal("550e8400-e29b-41d4-a716-446655440000")
    assert re.fullmatch(r"[A-Z0-9]{8}", ref)                    # exactly 8 [A-Z0-9]
    assert ref == ref_code_for_goal("550e8400-e29b-41d4-a716-446655440000")  # deterministic
    assert ref != ref_code_for_goal("different-goal-id")        # distinct per goal
    assert re.fullmatch(r"[A-Z0-9]{8}", ref_code_for_goal("goal-1"))  # short ids still 8 chars


async def test_send_text_uses_injected_send_fn():
    calls = []

    async def fake(to, body):
        calls.append((to, body))
        return {"ok": True}

    await whatsapp.send_text("+919999999999", "hello", send_fn=fake)
    assert calls == [("+919999999999", "hello")]


async def test_dispatch_sends_to_phoned_vendors_and_appends_ref():
    sent = []

    async def fake_send(to, body):
        sent.append((to, body))
        return {"ok": True}

    # RFQ text has no REF -> dispatch must append it.
    router = FakeRouter(text="Hello vendor, kindly share price and delivery.")
    vendors = [
        {"google_place_id": "p1", "name": "A", "phone": "+9111"},
        {"google_place_id": "p2", "name": "B", "phone": "+9122"},
        {"google_place_id": "p3", "name": "C"},  # no phone -> skipped
    ]
    res = await dispatch_rfqs(
        "00000000-0000-0000-0000-000000000009",
        {"category": "fb", "location": "BLR"}, vendors, 20000,
        router=router, send_fn=fake_send,
    )
    assert len(res["dispatched"]) == 2
    assert res["skipped_no_phone"] == ["p3"]
    assert re.fullmatch(r"REF:[A-Z0-9]{8}", res["ref"])
    assert len(sent) == 2
    assert all(res["ref"] in body for _, body in sent)   # REF present in every message


async def test_dispatch_keeps_model_ref_when_present():
    sent = []

    async def fake_send(to, body):
        sent.append(body)
        return {"ok": True}

    goal_id = "00000000-0000-0000-0000-000000000009"
    ref = "REF:" + ref_code_for_goal(goal_id)
    router = FakeRouter(text=f"Quote please. {ref}")  # model already included the REF
    vendors = [{"google_place_id": "p1", "name": "A", "phone": "+9111"}]
    await dispatch_rfqs(goal_id, {"category": "fb"}, vendors, 20000, router=router, send_fn=fake_send)
    # REF should appear exactly once (not double-appended).
    assert sent[0].count(ref) == 1
