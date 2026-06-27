"""Goal orchestration — intent parsing entry point (Phase 2).

For now this exposes intent parsing; the full goal pipeline (discover -> RFQ ->
quotes -> rank -> approve -> pay -> deliver -> rate) is assembled here as each
step lands. All LLM calls go through llm_router (non-negotiable rule).
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, timezone

from agents.llm_router import LLMTask, llm_router
from agents.prompts.intent_parser import INTENT_PARSER_PROMPT
from agents.prompts.rfq_generator import RFQ_GENERATOR_PROMPT
from agents.specialist.option_ranker import rank_options
from agents.specialist.places_agent import PlacesAgent
from core.config import settings
from core.db import Store, SupabaseStore
from core.refcodes import ref_code
from core.state_machine import GoalState, transition_goal_state
from services import whatsapp
from services.slack_notifier import build_approval_blocks, send_approval

log = logging.getLogger(__name__)

_default_store: Store = SupabaseStore()

# Rank + request approval once we have at least this many quotes. The RFQ-timeout
# worker ranks with fewer if vendors go silent past the deadline (later increment).
MIN_QUOTES_TO_RANK = 2


async def parse_intent(
    raw_input: str,
    company_city: str,
    current_date: str | None = None,
    *,
    router=llm_router,
) -> dict:
    """Parse a free-text procurement goal into structured intent.

    min_confidence=0.70 (prompts.md): intent parsing tolerates more ambiguity
    than quote parsing. If the whole chain stays below 0.70 the router raises
    AllModelsFailedError — the orchestrator's intent_unclear handling (asking the
    employee to clarify) is wired in a later increment.
    """
    current_date = current_date or date.today().isoformat()
    prompt = INTENT_PARSER_PROMPT.format(
        raw_input=raw_input, company_city=company_city, current_date=current_date,
    )
    log.debug("parse_intent input: %s", raw_input[:100])
    result = await router.complete(
        task=LLMTask.INTENT_PARSING, prompt=prompt, require_json=True, min_confidence=0.70,
    )
    parsed = json.loads(result.text)
    log.debug("parse_intent -> category=%s confidence=%s", parsed.get("category"), parsed.get("confidence"))
    return parsed


def ref_code_for_goal(goal_id: str) -> str:
    """8-char [A-Z0-9] REF embedded in every RFQ (Fix 06). Hash-based via
    core.refcodes.ref_code so it's always exactly 8 chars and the inbound router
    can match it back to the goal (and so can store.get_goal_by_partial_id)."""
    return ref_code(goal_id)


# Approved WhatsApp template for the FIRST cold contact (vendor's 24h session
# window is closed, so free-form text is disallowed). Must be created + approved
# in Meta/Chat Mitra; the name must match exactly.
RFQ_TEMPLATE_NAME = "rfq_first_contact_v1"
RFQ_TEMPLATE_LANG = "en"


# Human-readable labels so an RFQ never leaks the internal category code ("fb").
CATEGORY_LABEL = {
    "fb": "snacks / catering", "water": "drinking water",
    "stationery": "office stationery", "it_hardware": "IT hardware",
    "hotel": "hotel rooms", "flights": "flights", "generic": "supplies",
}
_PEOPLE_BASED = {"fb", "hotel", "flights"}  # quantity = number of people, not units


def _human_requirement(intent: dict) -> str:
    """Natural requirement phrase for the vendor — 'snacks for 100 people' or
    '30 office chairs' — preferring the specific item (subcategory), never 'fb'."""
    cat = intent.get("category") or "generic"
    item = intent.get("subcategory") or CATEGORY_LABEL.get(cat, "supplies")
    qty = intent.get("quantity")
    if not qty:
        return item
    return f"{item} for {qty} people" if cat in _PEOPLE_BASED else f"{qty} {item}"


def _human_needed_by(intent: dict) -> str:
    """A firm date ('5 Jul 2026') when the goal names one, else friendly urgency."""
    raw = intent.get("needed_by")
    if raw:
        try:
            dt = datetime.strptime(raw, "%Y-%m-%d")
            return f"{dt.day} {dt:%b %Y}"
        except (ValueError, TypeError):
            return str(raw)
    return {"asap": "as soon as possible", "this_week": "this week",
            "flexible": "a flexible date"}.get(intent.get("urgency"), "this week")


def _rfq_template_params(vendor_name: str, intent: dict, code: str) -> list[str]:
    """Fill rfq_first_contact_v1 body vars {{1}}..{{5}} in order:
    vendor name, requirement, location, needed-by, quote ref."""
    location = intent.get("location") or intent.get("destination") or "Bengaluru"
    return [vendor_name, _human_requirement(intent), location, _human_needed_by(intent), code]


async def generate_rfq(vendor_name: str, intent: dict, ref_code: str, budget,
                       *, is_first_contact: bool = True, router=llm_router) -> str:
    """Generate a WhatsApp RFQ message (plain text, so require_json=False)."""
    prompt = RFQ_GENERATOR_PROMPT.format(
        vendor_name=vendor_name,
        category_display=intent.get("subcategory")
        or CATEGORY_LABEL.get(intent.get("category") or "generic", "supplies"),
        quantity_display=intent.get("quantity", "unspecified"),
        location=intent.get("location") or intent.get("destination") or "",
        budget_display=budget,
        gst_required=intent.get("gst_required", True),
        urgency_display=_human_needed_by(intent),
        ref_code=ref_code,
        is_first_contact=is_first_contact,
    )
    result = await router.complete(task=LLMTask.RFQ_GENERATION, prompt=prompt, require_json=False)
    return result.text.strip()


async def dispatch_rfqs(goal_id: str, intent: dict, vendors: list[dict], budget,
                        *, router=llm_router, send_fn=None, is_first_contact: bool = True) -> dict:
    """Generate and send an RFQ to each vendor in parallel (top-N already chosen
    by the caller). Vendors without a WhatsApp phone are skipped (the real path
    falls back to an Exotel call — a later increment).

    Returns {"ref", "dispatched": [...], "skipped_no_phone": [...]}.
    """
    code = ref_code_for_goal(goal_id)
    ref = "REF:" + code
    with_phone = [v for v in vendors if v.get("phone")]
    skipped = [v.get("vendor_id") or v.get("google_place_id") or v.get("name")
               for v in vendors if not v.get("phone")]

    async def _one(v: dict) -> dict:
        vid = v.get("vendor_id") or v.get("google_place_id") or v.get("name")
        if is_first_contact:
            # Cold contact: WhatsApp permits only an APPROVED TEMPLATE outside the
            # vendor's 24h window. An injected send_fn here must be template-shaped
            # (to, template_name, language, body_params).
            params = _rfq_template_params(v["name"], intent, code)
            await whatsapp.send_template(v["phone"], RFQ_TEMPLATE_NAME,
                                         language=RFQ_TEMPLATE_LANG,
                                         body_params=params, send_fn=send_fn)
            channel = "template"
        else:
            # Window already open (vendor replied): free-form negotiation text.
            msg = await generate_rfq(v["name"], intent, ref, budget,
                                     is_first_contact=False, router=router)
            if ref not in msg:  # ensure the REF is present even if the model omitted it
                msg = f"{msg}\n\n{ref}"
            await whatsapp.send_text(v["phone"], msg, send_fn=send_fn)
            channel = "text"
        log.debug("[%s] RFQ sent to %s via %s (%s)", goal_id, v.get("name"), channel, ref)
        return {"vendor_id": vid, "phone": v["phone"], "ref": ref, "channel": channel, "sent": True}

    dispatched = await asyncio.gather(*[_one(v) for v in with_phone]) if with_phone else []
    return {"ref": ref, "dispatched": list(dispatched), "skipped_no_phone": skipped}


# ── GoalProcessor: orchestrate one goal end to end ──────────────────────────
async def process_goal(goal_id: str, *, store: Store | None = None, redis=None,
                       places_agent=None, router=llm_router, whatsapp_send_fn=None) -> dict:
    """Stage 1 of the pipeline: discover vendors and dispatch RFQs.

    The goal then progresses asynchronously as vendor replies arrive on the
    WhatsApp webhook (-> on_quote_collected) and the approver acts in Slack
    (-> approve_goal). Moves the goal processing -> pending_rfq (or
    -> operator_escalated if no vendors are found).
    """
    store = store or _default_store
    goal = await store.get_goal(goal_id)
    intent = goal.parsed_intent or {}

    # Discovery: an injected agent (tests) wins; else demo_mode uses seeded vendors
    # so RFQs only reach your own number(s); else live Google Places + vendor graph.
    if places_agent is not None:
        vendors = await places_agent.search(intent, limit=3)
    elif settings.demo_mode:
        vendors = await store.get_demo_vendors(intent.get("category") or "generic",
                                               intent.get("location") or intent.get("destination") or "")
        log.info("[%s] demo_mode discovery: %d seeded vendor(s)", goal_id, len(vendors))
    else:
        vendors = await PlacesAgent(known_vendors_fn=store.get_known_vendors).search(intent, limit=3)
    if not vendors:
        await transition_goal_state(goal_id, GoalState.PROCESSING, GoalState.OPERATOR_ESCALATED,
                                    store=store, redis=redis)
        log.warning("[%s] no vendors discovered -> operator", goal_id)
        return {"goal_id": goal_id, "status": "operator_escalated", "reason": "no vendors found"}

    # Persist discovered vendors so they have real ids (Fix 08: dedup by
    # google_place_id). Orders/ratings are keyed by vendors.id, so this must
    # happen before any quote/option carries a vendor_id downstream.
    for v in vendors:
        v["vendor_id"] = await store.upsert_vendor(v)

    result = await dispatch_rfqs(goal_id, intent, vendors, goal.budget_limit,
                                 router=router, send_fn=whatsapp_send_fn)
    if not result["dispatched"]:
        # Vendors found but none reachable on WhatsApp (no phone). The Exotel
        # call fallback covers this later; for now escalate so it isn't stranded.
        await transition_goal_state(goal_id, GoalState.PROCESSING, GoalState.OPERATOR_ESCALATED,
                                    store=store, redis=redis)
        log.warning("[%s] %d vendors found but none reachable -> operator", goal_id, len(vendors))
        return {"goal_id": goal_id, "status": "operator_escalated", "reason": "no reachable vendors"}

    await transition_goal_state(goal_id, GoalState.PROCESSING, GoalState.PENDING_RFQ,
                                store=store, redis=redis)
    log.info("[%s] discovered %d vendors, dispatched %d RFQs (%s)",
             goal_id, len(vendors), len(result["dispatched"]), result["ref"])
    return {"goal_id": goal_id, "status": "pending_rfq",
            "vendors": len(vendors), "dispatched": len(result["dispatched"]), "ref": result["ref"]}


async def on_quote_collected(goal_id: str, quote: dict, *, store: Store | None = None,
                             redis=None, router=llm_router, slack_send_fn=None) -> dict:
    """A parsed vendor quote arrived. Store it; once enough quotes are in, rank
    them and send the approval card. Idempotent on the state transitions."""
    store = store or _default_store
    await store.add_collected_quote(goal_id, quote)
    quotes = await store.get_collected_quotes(goal_id)

    goal = await store.get_goal(goal_id)
    if goal.status == GoalState.PENDING_RFQ.value:
        await transition_goal_state(goal_id, GoalState.PENDING_RFQ, GoalState.QUOTES_RECEIVED,
                                    store=store, redis=redis)
        goal = await store.get_goal(goal_id)

    ranked = None
    if goal.status == GoalState.QUOTES_RECEIVED.value and len(quotes) >= MIN_QUOTES_TO_RANK:
        ranked = await _rank_and_request_approval(goal_id, store=store, redis=redis,
                                                  router=router, slack_send_fn=slack_send_fn)
    return {"goal_id": goal_id, "collected": len(quotes), "ranked": ranked is not None}


async def _rank_and_request_approval(goal_id: str, *, store: Store, redis=None,
                                     router=llm_router, slack_send_fn=None) -> dict | None:
    # Claim the right to rank FIRST (Fix 05 CAS). With concurrent quotes both
    # callers reach here, but only the one that wins quotes_received ->
    # pending_approval ranks + sends the card — the loser returns without doing
    # the (expensive) LLM rank, a duplicate options write, or a second Slack card.
    moved = await transition_goal_state(goal_id, GoalState.QUOTES_RECEIVED, GoalState.PENDING_APPROVAL,
                                        store=store, redis=redis)
    if not moved:
        return None

    goal = await store.get_goal(goal_id)
    company = await store.get_company(goal.company_id)
    quotes = await store.get_collected_quotes(goal_id)
    budget = (goal.budget_limit
              or company.budget_policies.get(goal.category)
              or company.budget_policies.get("default"))

    # PRD "rated vendors first": feed the ranker our platform scores for the
    # vendors in this quote set (null = unproven, per the ranker prompt).
    vendor_ids = [q.get("vendor_id") for q in quotes if q.get("vendor_id")]
    vendor_scores = await store.get_vendor_scores(vendor_ids)

    ranked = await rank_options(quotes, budget_limit=budget,
                                gst_required=goal.parsed_intent.get("gst_required", True),
                                vendor_scores=vendor_scores, router=router)
    await store.update_goal_options(goal_id, ranked.get("ranked_options", []))
    await store.set_goal_approval_sent(goal_id, datetime.now(timezone.utc))  # Fix 04 clock

    blocks = build_approval_blocks(goal_id, ranked.get("ranked_options", []),
                                   summary=ranked.get("recommendation_summary"),
                                   raw_input=goal.raw_input)
    if company.slack_approval_channel:
        await send_approval(company.slack_approval_channel, blocks, send_fn=slack_send_fn)
    log.info("[%s] ranked %d options -> approval requested", goal_id, len(ranked.get("ranked_options", [])))
    return ranked
