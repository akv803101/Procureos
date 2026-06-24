"""Agent wrappers (intent parse, quote parse, option rank) with a fake router."""
import pytest

from agents.orchestrator import parse_intent
from agents.specialist.option_ranker import rank_options
from agents.specialist.quote_parser import parse_quote
from core.errors import QuoteAmbiguousError
from tests.fakes import FakeRouter


async def test_parse_intent_returns_parsed_dict():
    router = FakeRouter(text='{"category":"fb","quantity":50,"confidence":0.92}')
    intent = await parse_intent("snacks for 50 Koramangala", "Bengaluru", router=router)
    assert intent["category"] == "fb"
    assert intent["quantity"] == 50
    # It routed the INTENT_PARSING task at the 0.70 threshold.
    assert router.calls[0]["min_confidence"] == 0.70


async def test_parse_quote_returns_parsed_dict():
    router = FakeRouter(text='{"price":15000,"confidence":0.9,"price_includes_gst":true}')
    quote = await parse_quote(
        "15000 with GST", category="fb", quantity=50, location="Bengaluru",
        budget=20000, goal_id="G-1", router=router,
    )
    assert quote["price"] == 15000
    assert router.calls[0]["min_confidence"] == 0.85   # Fix 13 gate


async def test_parse_quote_propagates_ambiguous():
    router = FakeRouter(raise_exc=QuoteAmbiguousError("range price"))
    with pytest.raises(QuoteAmbiguousError):
        await parse_quote(
            "16-17K depending", category="fb", quantity=50, location="Bengaluru",
            budget=20000, router=router,
        )


async def test_rank_options_returns_parsed_dict():
    router = FakeRouter(text='{"ranked_options":[{"vendor_id":"v1","rank":1}],"recommendation_summary":"go v1"}')
    ranked = await rank_options(
        [{"vendor_id": "v1", "price": 15000}], budget_limit=20000,
        gst_required=True, router=router,
    )
    assert ranked["ranked_options"][0]["vendor_id"] == "v1"
