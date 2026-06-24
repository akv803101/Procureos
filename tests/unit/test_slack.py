"""Slack signature verification (Fix 11) + approval Block Kit builder."""
import hashlib
import hmac
import json
import time

from services.slack_notifier import (
    APPROVE_ACTION_ID,
    build_approval_blocks,
    verify_slack_signature,
)


def _sign(secret: str, ts: str, body: str) -> str:
    digest = hmac.new(secret.encode(), f"v0:{ts}:{body}".encode(), hashlib.sha256).hexdigest()
    return f"v0={digest}"


def test_valid_signature_passes():
    secret, ts, body = "shhh", str(int(time.time())), "payload=abc"
    assert verify_slack_signature(timestamp=ts, body=body, signature=_sign(secret, ts, body),
                                  signing_secret=secret) is True


def test_tampered_body_fails():
    secret, ts, body = "shhh", str(int(time.time())), "payload=abc"
    sig = _sign(secret, ts, body)
    assert verify_slack_signature(timestamp=ts, body="payload=EVIL", signature=sig,
                                  signing_secret=secret) is False


def test_stale_timestamp_fails():
    secret, ts, body = "shhh", str(int(time.time()) - 600), "x"  # 10 min old
    assert verify_slack_signature(timestamp=ts, body=body, signature=_sign(secret, ts, body),
                                  signing_secret=secret) is False


def test_missing_secret_fails_closed():
    assert verify_slack_signature(timestamp="1", body="x", signature="v0=whatever",
                                  signing_secret="") is False


def test_build_blocks_one_approve_button_per_option():
    ranked = [
        {"vendor_id": "v1", "rank": 1, "recommendation_label": "Preferred",
         "estimated_final_price_with_gst": 17700, "recommendation_reason": "best score"},
        {"vendor_id": "v2", "rank": 2, "recommendation_label": "Reliable",
         "estimated_final_price_with_gst": 21240, "recommendation_reason": "ok"},
    ]
    blocks = build_approval_blocks("g1", ranked, summary="pick one", raw_input="snacks for 50")
    approve = [b for b in blocks
               if b.get("type") == "section" and b.get("accessory", {}).get("action_id") == APPROVE_ACTION_ID]
    assert len(approve) == 2
    assert json.loads(approve[0]["accessory"]["value"]) == {"goal_id": "g1", "vendor_id": "v1"}
    assert any(b.get("type") == "actions" for b in blocks)   # reject button present
