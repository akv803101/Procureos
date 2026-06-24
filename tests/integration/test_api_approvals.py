"""HTTP layer: /health, Slack webhook HMAC gate, approval endpoints + envelope.

Uses FastAPI's TestClient. The approval logic is monkeypatched (it needs live
Supabase) — these tests verify the HTTP wiring, the Fix 11 signature gate, and
the standard response envelope.
"""
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

from fastapi.testclient import TestClient

from api.main import app
from core import approval_manager
from services import slack_notifier

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_slack_webhook_rejects_bad_signature():
    r = client.post(
        "/webhook/slack", content="payload=%7B%7D",
        headers={"X-Slack-Request-Timestamp": "1", "X-Slack-Signature": "v0=bad",
                 "Content-Type": "application/x-www-form-urlencoded"},
    )
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_signature"


def test_slack_webhook_approve_with_valid_signature(monkeypatch):
    async def fake_approve(goal_id, vendor_id, **kw):
        return {"status": "ordered", "goal_id": goal_id}

    monkeypatch.setattr(approval_manager, "approve_goal", fake_approve)
    monkeypatch.setattr(slack_notifier.settings, "slack_signing_secret", "shhh")

    payload = {"actions": [{"action_id": "approve_option",
                            "value": json.dumps({"goal_id": "g1", "vendor_id": "v1"})}]}
    body = urlencode({"payload": json.dumps(payload)})
    ts = str(int(time.time()))
    sig = "v0=" + hmac.new(b"shhh", f"v0:{ts}:{body}".encode(), hashlib.sha256).hexdigest()

    r = client.post("/webhook/slack", content=body,
                    headers={"X-Slack-Request-Timestamp": ts, "X-Slack-Signature": sig,
                             "Content-Type": "application/x-www-form-urlencoded"})
    assert r.status_code == 200
    assert "Approved" in r.json()["data"]["text"]


def test_approve_endpoint_requires_token():
    r = client.post("/approvals/g1/approve", json={"option_id": "v1"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"


def test_approve_endpoint_token_goal_mismatch(monkeypatch):
    async def fake_consume(token, **kw):
        return "a-different-goal"

    monkeypatch.setattr(approval_manager, "consume_approval_token", fake_consume)
    r = client.post("/approvals/g1/approve", json={"option_id": "v1", "token": "tok"})
    assert r.status_code == 403


def test_approve_endpoint_valid_token(monkeypatch):
    async def fake_consume(token, **kw):
        return "g1"

    async def fake_approve(goal_id, option_id, **kw):
        return {"status": "ordered", "goal_id": goal_id, "option_id": option_id}

    monkeypatch.setattr(approval_manager, "consume_approval_token", fake_consume)
    monkeypatch.setattr(approval_manager, "approve_goal", fake_approve)
    r = client.post("/approvals/g1/approve", json={"option_id": "v1", "token": "tok"})
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert body["data"]["status"] == "ordered"
