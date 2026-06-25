"""normalize_inbound — Meta/Chat-Mitra webhook shape -> flat router shape."""
from services.whatsapp import normalize_inbound


def test_meta_text_message():
    payload = {"entry": [{"changes": [{"value": {"messages": [
        {"from": "919900000001", "type": "text", "text": {"body": "15000 with GST"}}]}}]}]}
    assert normalize_inbound(payload) == {"from": "919900000001", "type": "text", "text": "15000 with GST"}


def test_meta_interactive_button():
    payload = {"entry": [{"changes": [{"value": {"messages": [
        {"from": "919900000001", "type": "interactive",
         "interactive": {"button_reply": {"id": "rate_good_r1"}}}]}}]}]}
    out = normalize_inbound(payload)
    assert out["from"] == "919900000001" and out["type"] == "interactive"
    assert out["interactive"]["button_reply"]["id"] == "rate_good_r1"


def test_flat_payload_passthrough():
    flat = {"from": "919900000001", "type": "text", "text": "hi"}
    assert normalize_inbound(flat) is flat


def test_status_receipt_returns_empty():
    payload = {"entry": [{"changes": [{"value": {"statuses": [{"status": "delivered"}]}}]}]}
    assert normalize_inbound(payload) == {}


def test_unrecognized_shape_returns_empty():
    assert normalize_inbound({"foo": "bar"}) == {}
