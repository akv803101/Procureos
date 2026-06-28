"""Chat Mitra inbound adapter: X-Webhook-Signature (hmac_sha256) + message.received
payload normalization (the scheme documented in the Chat Mitra dashboard)."""
import hashlib
import hmac
import json

from services.whatsapp import normalize_inbound, verify_chatmitra_signature, verify_inbound_signature


def _sig(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_chatmitra_signature_valid_and_tampered():
    body = json.dumps({"event": "message.received"}).encode()
    assert verify_chatmitra_signature(body=body, signature_header=_sig(body, "s3cret"), secret="s3cret")
    assert not verify_chatmitra_signature(body=body, signature_header="deadbeef", secret="s3cret")
    assert not verify_chatmitra_signature(body=body, signature_header=_sig(body, "s3cret"), secret="")  # fail-closed


def test_inbound_signature_picks_chatmitra_then_meta():
    body = b'{"event":"message.received"}'
    h = {"x-webhook-signature": _sig(body, "k")}
    assert verify_inbound_signature(body=body, headers=h, secret="k")
    # Meta-style header still honoured (sha256= prefix)
    meta = {"x-hub-signature-256": "sha256=" + _sig(body, "k")}
    assert verify_inbound_signature(body=body, headers=meta, secret="k")
    assert not verify_inbound_signature(body=body, headers={}, secret="k")   # no header -> fail-closed


def test_normalize_chatmitra_message_received():
    ev = normalize_inbound({"event": "message.received",
                            "data": {"from": "+919999999999", "type": "text",
                                     "text": {"body": "Rs 180 per plate incl GST"}}})
    assert ev["from"] == "+919999999999"
    assert "180" in ev["text"]


def test_normalize_chatmitra_ignores_non_message_events():
    assert normalize_inbound({"event": "message.sent", "data": {}}) == {}
    assert normalize_inbound({"event": "message.status.updated", "data": {}}) == {}


def test_normalize_meta_nested_still_works():
    payload = {"entry": [{"changes": [{"value": {"messages": [
        {"from": "+9111", "type": "text", "text": {"body": "hi"}}]}}]}]}
    assert normalize_inbound(payload)["from"] == "+9111"
