"""Approval endpoints (PRD Section 24).

POST /approvals/{goal_id}/approve   — sign off on a chosen option
POST /approvals/{goal_id}/reject    — reject the options

Auth for this increment is the magic-link token (Fix 12). The JWT/role path
(approver acting from the app UI) lands with Phase 3 auth; the Slack-button path
goes through /webhook/slack (HMAC-verified, Fix 11).

Exceptions map to the documented HTTP codes via the standard envelope.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from api.envelope import err, ok
from core import approval_manager
from core.clients import get_redis
from core.errors import (
    ApprovalTokenError,
    BudgetExceededError,
    OptionNotFoundError,
    StateConflictError,
)
from core.store import get_store
from schemas.approvals import ApproveRequest, RejectRequest

router = APIRouter(prefix="/approvals", tags=["approvals"])


async def _resolve_goal_from_token(token: str | None, goal_id: str):
    """Consume the magic-link token (one-time, TTL) and confirm it's for this goal.
    Returns (None, response) on failure, or (goal_id, None) on success."""
    if not token:
        return None, JSONResponse(err("unauthorized", "Approval token required"), status_code=401)
    token_goal_id = await approval_manager.consume_approval_token(token, store=get_store())
    if token_goal_id != goal_id:
        return None, JSONResponse(err("forbidden", "Token does not match this goal"), status_code=403)
    return token_goal_id, None


@router.post("/{goal_id}/approve")
async def approve(goal_id: str, req: ApproveRequest):
    try:
        _, fail = await _resolve_goal_from_token(req.token, goal_id)
        if fail is not None:
            return fail
        result = await approval_manager.approve_goal(goal_id, req.option_id,
                                                     store=get_store(), redis=get_redis())
        return ok(result)
    except ApprovalTokenError as e:
        return JSONResponse(err("token_invalid", str(e)), status_code=410)
    except StateConflictError as e:
        return JSONResponse(err("state_conflict", str(e)), status_code=409)
    except OptionNotFoundError as e:
        return JSONResponse(err("option_not_found", str(e)), status_code=404)
    except BudgetExceededError as e:
        return JSONResponse(err("budget_exhausted", str(e)), status_code=402)


@router.post("/{goal_id}/reject")
async def reject(goal_id: str, req: RejectRequest):
    try:
        _, fail = await _resolve_goal_from_token(req.token, goal_id)
        if fail is not None:
            return fail
        result = await approval_manager.reject_goal(goal_id, req.reason,
                                                    store=get_store(), redis=get_redis())
        return ok(result)
    except ApprovalTokenError as e:
        return JSONResponse(err("token_invalid", str(e)), status_code=410)
    except StateConflictError as e:
        return JSONResponse(err("state_conflict", str(e)), status_code=409)
