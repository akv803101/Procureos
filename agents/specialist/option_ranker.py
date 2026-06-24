"""Option ranker — parsed quotes -> ranked options for the approver (Phase 2)."""
from __future__ import annotations

import json
import logging

from agents.llm_router import LLMTask, llm_router
from agents.prompts.option_ranker import OPTION_RANKER_PROMPT

log = logging.getLogger(__name__)


async def rank_options(
    quotes: list[dict],
    *,
    budget_limit,
    gst_required: bool,
    min_score: int = 70,
    vendor_scores: dict | None = None,
    router=llm_router,
) -> dict:
    """Rank vendor quotes. Returns {ranked_options: [...], recommendation_summary}.

    The ranking prompt does not emit a `confidence` field, so we pass
    min_confidence=0.0 (no confidence gate) — the router still falls through the
    chain on provider failure or unparseable JSON.
    """
    prompt = OPTION_RANKER_PROMPT.format(
        budget_limit=budget_limit,
        gst_required=gst_required,
        min_score=min_score,
        quotes_json=json.dumps(quotes, ensure_ascii=False),
        vendor_scores_json=json.dumps(vendor_scores or {}, ensure_ascii=False),
    )
    result = await router.complete(
        task=LLMTask.OPTION_RANKING, prompt=prompt, require_json=True, min_confidence=0.0,
    )
    parsed = json.loads(result.text)
    log.debug("rank_options -> %d options", len(parsed.get("ranked_options", [])))
    return parsed
