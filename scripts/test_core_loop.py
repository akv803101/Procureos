"""Single-file core-loop driver — Karpathy Rule 1 & 2.

ONE goal, run end to end through the real agent functions, printing every step.
This is the "overfit one case" harness: before the loop is wrapped in FastAPI +
Redis + the worker, prove it works for a single goal with real LLM calls.

REAL here (live LLM via llm_router, if a key is set):
  intent parsing, PlacesAgent ranking, RFQ message generation, quote parsing,
  option ranking.
STUBBED (gated on credentials we don't have — clearly labeled):
  the Google Places HTTP call (search_fn), the WhatsApp send (send_fn), and the
  vendor replies. The PlacesAgent and dispatch logic that run on top are real.

Run from the project root:
    python -m scripts.test_core_loop

Needs at least one LLM key in .env (GROQ_API_KEY covers intent/RFQ;
ANTHROPIC_API_KEY covers quote parsing). With no keys it prints what to set.
"""
import asyncio
import json

from agents.orchestrator import dispatch_rfqs, parse_intent
from agents.specialist.option_ranker import rank_options
from agents.specialist.places_agent import PlacesAgent
from agents.specialist.quote_parser import parse_quote
from core.config import settings
from core.errors import QuoteAmbiguousError

GOAL = {
    "raw_input": "order snacks for 50 people Koramangala",
    "company_city": "Bengaluru",
    "budget": 20000,
}

# STUB: stands in for the Google Places HTTP response (PlacesAgent runs for real
# on top of this). Shapes match services.google_places._normalize output.
STUB_PLACES = [
    {"google_place_id": "gp_namma", "name": "Namma Caterers", "phone": "+919000000001",
     "google_rating": 4.6, "review_count": 210, "city": "Bengaluru", "source": "google_places"},
    {"google_place_id": "gp_koram", "name": "Koramangala Snacks Co", "phone": "+919000000002",
     "google_rating": 4.3, "review_count": 95, "city": "Bengaluru", "source": "google_places"},
    {"google_place_id": "gp_fresh", "name": "FreshBite Foods", "phone": "+919000000003",
     "google_rating": 4.1, "review_count": 60, "city": "Bengaluru", "source": "google_places"},
]

# STUB: vendor WhatsApp replies, keyed by google_place_id. The third is a range
# price, which should trip the Fix 13 confidence gate -> operator queue.
STUB_REPLIES = {
    "gp_namma": "₹15,000 all inclusive with GST invoice, delivery Tuesday",
    "gp_koram": "18000 ka padega, GST extra 5 percent, kal deliver kar denge",
    "gp_fresh": "bhai 16-17K denge depending on items",
}


def _line(title):
    print("\n" + "=" * 68 + f"\n{title}\n" + "=" * 68)


async def _stub_places_search(query):
    print(f"  [Places stub] query: {query!r}")
    return STUB_PLACES


async def _stub_send(to, body):
    print(f"\n  [WABA stub] -> {to}\n{body}")
    return {"status": "stub_sent", "to": to}


async def main() -> None:
    if not (settings.groq_api_key or settings.anthropic_api_key
            or settings.openai_api_key or settings.google_api_key):
        print("No LLM key set. Add GROQ_API_KEY (and/or ANTHROPIC_API_KEY) to .env, then re-run.")
        print("The core loop calls real models — coding_philosophy Rule 6.")
        return

    goal_id = "00000000-0000-0000-0000-000000000001"

    _line("STEP 1 — Parse intent (REAL LLM)")
    print(f"Goal: {GOAL['raw_input']!r}  (city={GOAL['company_city']})")
    intent = await parse_intent(GOAL["raw_input"], GOAL["company_city"])
    intent.setdefault("location", GOAL["company_city"])
    print(json.dumps(intent, indent=2, ensure_ascii=False))

    _line("STEP 2 — Discover vendors (REAL PlacesAgent; Google Places HTTP stubbed)")
    agent = PlacesAgent(search_fn=_stub_places_search)  # known_vendors_fn=None -> all unproven
    vendors = await agent.search(intent, limit=3)
    for v in vendors:
        print(f"  {v['google_place_id']}: {v['name']}  (rating {v['google_rating']}, band {v['score_band']})")

    _line("STEP 3 — Dispatch RFQs (REAL generate+dispatch; WhatsApp send stubbed)")
    result = await dispatch_rfqs(goal_id, intent, vendors, GOAL["budget"], send_fn=_stub_send)
    print(f"\n  ref={result['ref']}  dispatched={len(result['dispatched'])}  skipped_no_phone={result['skipped_no_phone']}")

    _line("STEP 4 — Parse vendor replies (REAL LLM; Fix 13 confidence gate)")
    quotes = []
    for v in vendors:
        reply = STUB_REPLIES.get(v["google_place_id"], "")
        print(f"\n{v['name']} replied: {reply!r}")
        try:
            quote = await parse_quote(
                reply, category=intent.get("category", "fb"),
                quantity=intent.get("quantity"), location=GOAL["company_city"],
                budget=GOAL["budget"], goal_id=goal_id,
            )
            quote["vendor_id"] = v["google_place_id"]
            quotes.append(quote)
            print(f"  -> price={quote.get('price')} confidence={quote.get('confidence')}")
        except QuoteAmbiguousError as e:
            print(f"  -> AMBIGUOUS, routed to operator queue: {e}")

    _line("STEP 5 — Rank options (REAL LLM)")
    if not quotes:
        print("No confident quotes to rank — all routed to operator.")
        return
    ranked = await rank_options(
        quotes, budget_limit=GOAL["budget"],
        gst_required=intent.get("gst_required", True), min_score=70, vendor_scores={},
    )
    print(json.dumps(ranked, indent=2, ensure_ascii=False))

    _line("CORE LOOP COMPLETE")
    print("intent -> discover (PlacesAgent) -> RFQ dispatch -> quotes (gated) -> ranked ✓")
    print("Next: Slack approval (Fix 11), payment (Fixes 01-03), delivery, rating.")


if __name__ == "__main__":
    asyncio.run(main())
