"""Goal endpoints (PRD Section 24).

POST /goals        — submit a procurement goal; parses intent, persists, and
                     kicks the GoalProcessor off in the background. Returns 202.
GET  /goals/{id}   — current goal status (to watch the pipeline progress).

Company/employee come from the request body in this increment (demo mode);
Phase 3 auth replaces company_id with the JWT company_id claim.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import JSONResponse

from agents.orchestrator import parse_intent, process_goal
from api.envelope import err, ok
from core.clients import get_redis
from core.db import Goal
from core.state_machine import GoalState, transition_goal_state
from core.store import get_store
from schemas.goals import CreateGoalRequest

router = APIRouter(prefix="/goals", tags=["goals"])
log = logging.getLogger(__name__)

_store = get_store()   # SupabaseStore when creds are present, else shared InMemoryStore


async def _run_process_goal(goal_id: str) -> None:
    """Background wrapper: never let a pipeline failure vanish — escalate the
    goal to the operator queue so it doesn't sit silently stuck."""
    try:
        await process_goal(goal_id, store=_store, redis=get_redis())
    except Exception:  # noqa: BLE001
        log.exception("process_goal failed for %s — escalating", goal_id)
        try:
            current = await _store.get_goal_state(goal_id)
            await transition_goal_state(goal_id, current, GoalState.OPERATOR_ESCALATED,
                                        store=_store, redis=get_redis())
        except Exception:
            log.exception("could not escalate goal %s", goal_id)


@router.post("")
async def create_goal(req: CreateGoalRequest, background: BackgroundTasks):
    try:
        intent = await parse_intent(req.raw_input, req.company_city)
    except Exception as e:  # noqa: BLE001 — intent parsing failed / too ambiguous
        return JSONResponse(err("intent_unclear", str(e)), status_code=422)

    goal = Goal(
        id="", status="processing", category=intent.get("category"),
        company_id=req.company_id, employee_id=req.employee_id,
        raw_input=req.raw_input, parsed_intent=intent,
        budget_limit=intent.get("budget_hint"),
    )
    goal_id = await _store.create_goal(goal)

    # Run discovery + RFQ dispatch off the request path (Fix 09 spirit: never
    # block the HTTP response on the agent pipeline).
    background.add_task(_run_process_goal, goal_id)
    log.info("goal %s submitted (company=%s)", goal_id, req.company_id)
    return JSONResponse(ok({"goal_id": goal_id, "status": "processing"}), status_code=202)


@router.get("/{goal_id}")
async def get_goal(goal_id: str):
    try:
        goal = await _store.get_goal(goal_id)
    except KeyError:
        return JSONResponse(err("not_found", "goal not found"), status_code=404)
    return ok({
        "goal_id": goal.id, "status": goal.status, "category": goal.category,
        "options": goal.options, "collected_quotes": len(goal.collected_quotes or []),
    })
