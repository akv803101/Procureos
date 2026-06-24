"""Goal refiner prompt — verbatim from prompts.md (Prompt 4)."""

GOAL_REFINER_PROMPT = """
You are a procurement search optimizer.
An approver rejected vendor options for a procurement request. Refine the search.

Original request: "{raw_input}"
Original parsed intent: {original_intent_json}

Rejection reason: "{rejection_reason}"
Rejection note (optional): "{rejection_note}"

Previous options that were rejected:
{rejected_options_json}

Based on the rejection reason, suggest refined search parameters:
- "too_expensive": tighten budget filter, look for economy options
- "wrong_dates": adjust delivery timeline requirement
- "wrong_vendor_type": refine category/subcategory search terms
- "missing_gst": filter strictly to GST-registered vendors only
- "other": use the note to infer what to change

Return ONLY valid JSON:
{{
  "refined_budget_limit": number or null,
  "refined_category": string or null,
  "refined_subcategory": string or null,
  "gst_registered_only": boolean,
  "faster_delivery_required": boolean,
  "exclude_vendor_ids": [string],
  "additional_search_terms": string or null,
  "refinement_explanation": string (what changed and why, max 100 chars)
}}

Return ONLY JSON. No preamble, no explanation, no markdown.
"""
