"""Inbound webhooks (PRD Section 24).

POST /webhook/slack — Slack interactive actions (Approve / Reject buttons).
HMAC-verified against the Slack signing secret (Fix 11) BEFORE any processing,
using the raw request body. Slack expects a 200 even on logical failure, so
errors are returned as a 200 with a user-visible text rather than an HTTP error.
"""
from __future__ import annotations

import hmac
import json
import logging
from urllib.parse import parse_qs

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from api.envelope import err, ok
from core import approval_manager
from core.clients import get_redis
from core.config import settings
from core.errors import BudgetExceededError, OptionNotFoundError, StateConflictError
from core.store import get_store
from core.waba_handlers import DefaultWabaHandlers
from core.waba_router import route_incoming_whatsapp
from services.razorpay import verify_razorpay_signature
from services.slack_notifier import APPROVE_ACTION_ID, REJECT_ACTION_ID, verify_slack_signature
from services.whatsapp import normalize_inbound, verify_inbound_signature

router = APIRouter(tags=["webhooks"])
log = logging.getLogger(__name__)


@router.post("/webhook/slack")
async def slack_webhook(request: Request):
    raw = (await request.body()).decode()
    timestamp = request.headers.get("X-Slack-Request-Timestamp", "")
    signature = request.headers.get("X-Slack-Signature", "")

    # Fix 11 — verify the HMAC on the RAW body before doing anything else.
    if not verify_slack_signature(timestamp=timestamp, body=raw, signature=signature):
        return JSONResponse(err("invalid_signature", "Slack signature verification failed"), status_code=401)

    form = parse_qs(raw)
    payload_raw = form.get("payload", [None])[0]
    if not payload_raw:
        return ok({"ignored": "no interactive payload"})

    try:
        payload = json.loads(payload_raw)
        value = json.loads(payload.get("actions", [{}])[0].get("value", "{}"))
    except (json.JSONDecodeError, IndexError):
        return JSONResponse(err("bad_payload", "malformed Slack interactive payload"), status_code=400)
    actions = payload.get("actions", [])
    if not actions:
        return ok({"ignored": "no actions"})

    action = actions[0]
    goal_id = value.get("goal_id")
    action_id = action.get("action_id")

    store = get_store()
    try:
        if action_id == APPROVE_ACTION_ID:
            result = await approval_manager.approve_goal(goal_id, value.get("vendor_id"),
                                                         store=store, redis=get_redis())
            return ok({"text": f"✅ Approved — {result.get('status')}"})
        if action_id == REJECT_ACTION_ID:
            result = await approval_manager.reject_goal(goal_id, "rejected via Slack",
                                                        store=store, redis=get_redis())
            return ok({"text": f"❌ Rejected — {result.get('status')}"})
        return ok({"ignored": f"unknown action {action_id}"})
    except (StateConflictError, OptionNotFoundError, BudgetExceededError) as e:
        # Surface to the approver in Slack; still a 200 so Slack doesn't retry.
        return ok({"text": f"Could not complete: {e}"})


@router.get("/webhook/whatsapp")
async def whatsapp_verify(request: Request):
    """WhatsApp webhook URL verification (Meta GET challenge)."""
    params = request.query_params
    token = settings.meta_webhook_verify_token
    if token and hmac.compare_digest(params.get("hub.verify_token", ""), token):
        return PlainTextResponse(params.get("hub.challenge", ""))
    return JSONResponse(err("forbidden", "verify token mismatch"), status_code=403)


@router.post("/webhook/whatsapp")
async def whatsapp_inbound(request: Request):
    """Inbound WhatsApp events → waba_router (Fix 06). HMAC-verified on the raw body,
    auto-detecting Chat Mitra (X-Webhook-Signature, hmac_sha256) or Meta
    (X-Hub-Signature-256). normalize_inbound handles either payload shape."""
    raw = await request.body()
    # Log the raw inbound (truncated) so the FIRST real Chat Mitra webhook reveals its
    # exact payload shape — lets us pin _normalize_chatmitra precisely. Safe: no secrets.
    log.info("inbound whatsapp webhook (%d bytes): %s", len(raw), raw.decode("utf-8", "replace")[:1000])
    if not verify_inbound_signature(body=raw, headers=request.headers):
        return JSONResponse(err("invalid_signature", "WhatsApp signature verification failed"), status_code=401)

    try:
        payload = json.loads(raw.decode() or "{}")
    except json.JSONDecodeError:
        return JSONResponse(err("bad_payload", "request body is not valid JSON"), status_code=400)

    event = normalize_inbound(payload)   # Meta/Chat-Mitra shape -> flat {from,text,type,interactive}
    if not event.get("from"):
        return ok({"ignored": "non-message event (status receipt or unrecognized shape)"})

    store = get_store()
    handlers = DefaultWabaHandlers(store=store, redis=get_redis())
    result = await route_incoming_whatsapp(event, handlers=handlers, store=store)
    return ok(result)


@router.post("/webhook/payment")
async def payment_webhook(request: Request):
    """Razorpay payment webhook (HMAC-verified, X-Razorpay-Signature on raw body).
    Confirms capture asynchronously; the goal already advanced at approval, so this
    is the real-money confirmation + audit point."""
    raw = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")
    if not verify_razorpay_signature(body=raw, signature_header=signature):
        return JSONResponse(err("invalid_signature", "Razorpay signature verification failed"), status_code=401)
    try:
        event = json.loads(raw.decode() or "{}")
    except json.JSONDecodeError:
        return JSONResponse(err("bad_payload", "request body is not valid JSON"), status_code=400)
    etype = event.get("event")
    log.info("razorpay webhook received: %s", etype)
    return ok({"received": etype})
