"""Quote parser — vendor WhatsApp reply -> structured quote (Phase 2).

Fix 13 (confidence gate): quote parsing routes through llm_router with
min_confidence=0.85. If no model in the chain produces a confident structured
price, llm_router raises QuoteAmbiguousError and the goal goes to the operator
queue — the approver is never shown an uncertain quote.
"""
from __future__ import annotations

import json
import logging

from agents.llm_router import LLMTask, llm_router
from agents.prompts.quote_parser import QUOTE_PARSER_PROMPT

log = logging.getLogger(__name__)

QUOTE_CONFIDENCE_THRESHOLD = 0.85  # Fix 13


async def parse_quote(
    vendor_message: str,
    *,
    category: str,
    quantity,
    location: str,
    budget,
    goal_id: str | None = None,
    router=llm_router,
) -> dict:
    """Parse one vendor reply. Raises QuoteAmbiguousError if not confident."""
    prompt = QUOTE_PARSER_PROMPT.format(
        category=category,
        quantity=quantity if quantity is not None else "unspecified",
        location=location,
        budget=budget,
        vendor_message=vendor_message,
    )
    log.debug("[%s] quote parser received: %s", goal_id, vendor_message[:100])
    result = await router.complete(
        task=LLMTask.QUOTE_PARSING,
        prompt=prompt,
        require_json=True,
        min_confidence=QUOTE_CONFIDENCE_THRESHOLD,
        goal_id=goal_id,
    )
    parsed = json.loads(result.text)
    log.debug("[%s] parsed quote: price=%s confidence=%s gst=%s",
              goal_id, parsed.get("price"), parsed.get("confidence"), parsed.get("price_includes_gst"))
    return parsed
