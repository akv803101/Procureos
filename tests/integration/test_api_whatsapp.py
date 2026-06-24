"""HTTP layer: WhatsApp webhook verification (GET) + inbound HMAC + routing."""
import hashlib
import hmac
import json

from fastapi.testclient import TestClient

from api.main import app
from core import waba_handlers
from core.config import settings

client = TestClient(app)


def test_whatsapp_verify_challenge(monkeypatch):
    monkeypatch.setattr(settings, "meta_webhook_verify_token", "vtoken")
    r = client.get("/webhook/whatsapp", params={"hub.verify_token": "vtoken", "hub.challenge": "12345"})
    assert r.status_code == 200
    assert r.text == "12345"


def test_whatsapp_verify_rejects_bad_token(monkeypatch):
    monkeypatch.setattr(settings, "meta_webhook_verify_token", "vtoken")
    r = client.get("/webhook/whatsapp", params={"hub.verify_token": "wrong", "hub.challenge": "x"})
    assert r.status_code == 403


def test_whatsapp_inbound_rejects_bad_signature(monkeypatch):
    monkeypatch.setattr(settings, "meta_webhook_secret", "secret")
    r = client.post("/webhook/whatsapp", content=b"{}", headers={"X-Hub-Signature-256": "sha256=bad"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_signature"


def test_whatsapp_inbound_routes_rating(monkeypatch):
    monkeypatch.setattr(settings, "meta_webhook_secret", "secret")
    captured = {}

    async def fake_rating(self, rating_id, button_id):
        captured["rating_id"] = rating_id
        captured["button_id"] = button_id
        return {"ok": True}

    monkeypatch.setattr(waba_handlers.DefaultWabaHandlers, "handle_employee_rating", fake_rating)

    payload = {"from": "+9111", "type": "interactive",
               "interactive": {"button_reply": {"id": "rate_good_rat-1"}}}
    body = json.dumps(payload).encode()
    sig = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()

    r = client.post("/webhook/whatsapp", content=body, headers={"X-Hub-Signature-256": sig})
    assert r.status_code == 200
    assert captured["rating_id"] == "rat-1"
    assert captured["button_id"] == "rate_good_rat-1"
