"""POST /webhook/payment — Razorpay signature verification."""
import hashlib
import hmac
import json

import services.razorpay as rzp
from api.main import app
from fastapi.testclient import TestClient

client = TestClient(app)


def test_payment_webhook_accepts_valid_signature(monkeypatch):
    monkeypatch.setattr(rzp.settings, "razorpay_webhook_secret", "whsec")
    body = json.dumps({"event": "payment_link.paid"})
    sig = hmac.new(b"whsec", body.encode(), hashlib.sha256).hexdigest()
    r = client.post("/webhook/payment", data=body,
                    headers={"X-Razorpay-Signature": sig, "Content-Type": "application/json"})
    assert r.status_code == 200
    assert r.json()["data"]["received"] == "payment_link.paid"


def test_payment_webhook_rejects_bad_signature(monkeypatch):
    monkeypatch.setattr(rzp.settings, "razorpay_webhook_secret", "whsec")
    r = client.post("/webhook/payment", data=json.dumps({"event": "x"}),
                    headers={"X-Razorpay-Signature": "deadbeef", "Content-Type": "application/json"})
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "invalid_signature"
