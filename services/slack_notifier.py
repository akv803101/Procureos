"""Slack approval messages + signature verification (Phase 2).

- build_approval_blocks: pure Block Kit builder for the one-click approval card
  (PRD Section 6) — header, the ranked options, an Approve button per option,
  and a Reject-all button.
- send_approval: posts the card (gated on SLACK_BOT_TOKEN; injectable send_fn).
- verify_slack_signature (Fix 11): HMAC-SHA256 verification of inbound Slack
  requests, with a 5-minute replay window. Pure — no network, fully testable.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time

from core.config import settings

log = logging.getLogger(__name__)

APPROVE_ACTION_ID = "approve_option"
REJECT_ACTION_ID = "reject_goal"


def build_approval_blocks(goal_id: str, ranked: list[dict], *, summary: str | None = None,
                          raw_input: str | None = None) -> list[dict]:
    """Block Kit for the approval card. Each option's Approve button carries
    {goal_id, vendor_id} so the inbound action handler knows what was approved."""
    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": "🛒 Procurement approval needed"}},
    ]
    if raw_input:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": f"*Request:* {raw_input}"}})
    if summary:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": summary}})
    blocks.append({"type": "divider"})

    for opt in ranked:
        price = opt.get("estimated_final_price_with_gst")
        label = opt.get("recommendation_label", "Option")
        text = (f"*{opt.get('rank', '?')}. {label}* — ₹{price}\n"
                f"{opt.get('recommendation_reason', '')}")
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": text},
            "accessory": {
                "type": "button",
                "text": {"type": "plain_text", "text": "Approve"},
                "style": "primary",
                "action_id": APPROVE_ACTION_ID,
                "value": json.dumps({"goal_id": goal_id, "vendor_id": opt.get("vendor_id")}),
            },
        })

    blocks.append({"type": "actions", "elements": [{
        "type": "button",
        "text": {"type": "plain_text", "text": "Reject all"},
        "style": "danger",
        "action_id": REJECT_ACTION_ID,
        "value": json.dumps({"goal_id": goal_id}),
    }]})
    return blocks


async def _default_send(channel: str, blocks: list[dict], text: str) -> dict:
    import httpx

    if not settings.slack_bot_token:
        raise RuntimeError("SLACK_BOT_TOKEN not set — cannot post to Slack")
    headers = {"Authorization": f"Bearer {settings.slack_bot_token}", "Content-Type": "application/json"}
    payload = {"channel": channel, "text": text, "blocks": blocks}
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post("https://slack.com/api/chat.postMessage", headers=headers, json=payload)
        resp.raise_for_status()
        return resp.json()


async def send_approval(channel: str, blocks: list[dict], *, text: str = "Approval needed", send_fn=None) -> dict:
    """Post an approval card to a Slack channel/user. Injectable send_fn for tests."""
    send_fn = send_fn or _default_send
    return await send_fn(channel, blocks, text)


def verify_slack_signature(*, timestamp: str, body: str, signature: str,
                           signing_secret: str | None = None, max_skew_seconds: int = 300,
                           now: float | None = None) -> bool:
    """Fix 11: verify the Slack request HMAC.

    Slack signs `v0:{timestamp}:{raw_body}` with HMAC-SHA256 keyed by the signing
    secret, and sends the hex digest as `v0=...` in X-Slack-Signature. We also
    reject timestamps outside a 5-minute window (replay protection). Returns True
    only on a constant-time match within the window.
    """
    signing_secret = signing_secret if signing_secret is not None else settings.slack_signing_secret
    if not signing_secret:
        log.warning("SLACK_SIGNING_SECRET not set — refusing to accept Slack webhook")
        return False
    try:
        ts = int(timestamp)
    except (TypeError, ValueError):
        return False
    now = now if now is not None else time.time()
    if abs(now - ts) > max_skew_seconds:
        return False  # stale — likely a replay
    basestring = f"v0:{timestamp}:{body}".encode()
    digest = hmac.new(signing_secret.encode(), basestring, hashlib.sha256).hexdigest()
    return hmac.compare_digest(f"v0={digest}", signature or "")
