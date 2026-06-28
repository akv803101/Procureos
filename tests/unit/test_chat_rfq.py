"""RFQ artifact builder — spec fidelity, no fabricated deadline, budget visibility,
and the code-enforced address-specificity gate (from chat-agent red-team round 3)."""
from api.routes.chat import _address_is_specific, _draft_rfq


def _laptop_intent(**over):
    base = {"category": "it_hardware", "subcategory": "laptops", "quantity": 100,
            "location": "Mumbai", "delivery_address": "Express Towers, 10th Floor, Nariman Point",
            "special_requirements": "i7 13th gen, 32GB, 1TB SSD, 14-inch"}
    base.update(over)
    return base


def test_rfq_carries_the_spec():
    r = _draft_rfq(_laptop_intent(), "Lenovo Store", "ABCD1234")
    assert "i7 13th gen" in r and "Spec:" in r          # spec reaches the vendor, not just "100 laptops"


def test_rfq_never_fabricates_a_deadline():
    r = _draft_rfq(_laptop_intent(), "Lenovo Store", "ABCD1234")   # no needed_by / urgency given
    assert "this week" not in r.lower()
    assert "earliest available timeline" in r.lower()


def test_rfq_uses_the_given_date():
    r = _draft_rfq(_laptop_intent(needed_by="2026-07-15"), "V", "C0DE1234")
    assert "needed by 15 Jul 2026" in r


def test_budget_internal_is_hidden():
    r = _draft_rfq(_laptop_intent(budget_hint="90k/unit", budget_visibility="internal"), "V", "C0DE1234")
    assert "90k" not in r and "open to your best competitive quote" in r.lower()


def test_budget_show_is_revealed():
    r = _draft_rfq(_laptop_intent(budget_hint="90k/unit", budget_visibility="show"), "V", "C0DE1234")
    assert "indicative budget is 90k/unit" in r


def test_address_gate_rejects_area_landmark():
    assert _address_is_specific("Koramangala, near Forum Mall") is False
    assert _address_is_specific("Bengaluru") is False
    assert _address_is_specific("12 MG Road, Bengaluru") is True          # has a number
    assert _address_is_specific("Prestige Tower, 4th floor") is True      # building + floor keyword
