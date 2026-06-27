"""Live preview of the FIRST half of the pipeline — NO messages are sent.

    python -m scripts.preview_pipeline "need veg snacks for 50 in Koramangala by Fri, GST"

Runs: goal -> LLM intent (Groq) -> live Google Places discovery -> vendor-graph
ranking -> drafted RFQ (both the approved-template render and a free-form LLM
draft). Read-only: it does NOT call WhatsApp, create goals, or write to the DB.
"""
from __future__ import annotations

import asyncio
import sys

from agents.orchestrator import (
    RFQ_TEMPLATE_NAME,
    _rfq_template_params,
    clarifying_questions,
    generate_rfq,
    needs_clarification,
    parse_intent,
    ref_code_for_goal,
)
from agents.specialist.places_agent import PlacesAgent
from core.store import get_store

COMPANY_CITY = "Bengaluru"
TOP_N = 6

# Must match the body of the approved Meta template `rfq_first_contact_v1`.
TEMPLATE_BODY = (
    "Hi {0}, this is IntelliBridge Procurement. We're sourcing {1} for delivery "
    "in {2}, needed by {3}. If you can supply, please reply here with your best "
    "price (incl. GST) and availability. Quote ref: {4}. Thanks!"
)


async def main(goal_text: str) -> None:
    print("=" * 78)
    print(f"GOAL:  {goal_text}")
    print("=" * 78)

    # 1) Intent (LLM)
    intent = await parse_intent(goal_text, COMPANY_CITY)
    print("\n① PARSED INTENT (Groq)")
    for k in ("category", "subcategory", "quantity", "location", "destination",
              "delivery_address", "needed_by", "urgency", "gst_required",
              "budget_hint", "special_requirements", "confidence"):
        if intent.get(k) not in (None, ""):
            print(f"     {k:20} = {intent[k]}")

    # Intake gate — collect mandatory fields (place + delivery address) before
    # contacting any vendor. Budget is optional. The agent asks the employee.
    if needs_clarification(intent):
        print("\n⚠️  AGENT ASKS THE EMPLOYEE FIRST (no vendors contacted yet):")
        for q in clarifying_questions(intent):
            print(f"     • [{'required' if q['required'] else 'optional'}] {q['ask']}")
        print("\n   (add these details to the goal and re-run to see discovery + RFQ)")
        return

    # 2) Discovery (live Google Places) + 3) vendor-graph ranking
    store = get_store()
    agent = PlacesAgent(known_vendors_fn=store.get_known_vendors)
    vendors = await agent.search(intent, limit=TOP_N)

    print(f"\n② VETTED VENDORS — top {len(vendors)} (rated/known first, then by Google rating)")
    print("   " + "-" * 73)
    for i, v in enumerate(vendors, 1):
        wa = "✓WA" if v.get("phone") else "—  "
        band = v.get("score_band") or "unproven"
        print(f"   {i}. {v.get('name','')[:32]:32}  {(v.get('phone') or 'no phone'):17} {wa}")
        print(f"      ⭐{v.get('google_rating')} ({v.get('review_count')} reviews) · band={band}")
        if v.get("address"):
            print(f"      {v['address'][:70]}")
        if v.get("website"):
            print(f"      {v['website'][:70]}")
    reachable = [v for v in vendors if v.get("phone")]
    print(f"\n   WhatsApp-reachable: {len(reachable)}/{len(vendors)}")

    if not reachable:
        print("\n(no reachable vendors to draft for)")
        return

    # 4) Draft the RFQ for the #1 reachable vendor
    top = reachable[0]
    code = ref_code_for_goal("preview-goal")
    params = _rfq_template_params(top["name"], intent, code)

    print("\n③ DRAFTED RFQ for vendor #1:", top["name"])
    print("   " + "-" * 73)
    print(f"   A) APPROVED TEMPLATE that gets sent cold ({RFQ_TEMPLATE_NAME}):\n")
    print("      " + TEMPLATE_BODY.format(*params).replace("\n", "\n      "))
    print("\n      [buttons]  Share a quote   |   Not interested")

    print("\n   B) FREE-FORM draft the agent uses AFTER the vendor replies (Groq):\n")
    free = await generate_rfq(top["name"], intent, "REF:" + code, intent.get("budget_hint"),
                              is_first_contact=False)
    print("      " + free.replace("\n", "\n      "))
    print()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit('Usage: python -m scripts.preview_pipeline "<your goal>"')
    asyncio.run(main(" ".join(sys.argv[1:])))
