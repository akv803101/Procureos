"""Razorpay client (test-mode payment links) + signature + provider selection."""
import hashlib
import hmac

import pytest

import services.payment as pay
from core.errors import ProviderDownError
from services.razorpay import RazorpayClient, verify_razorpay_signature

KEY = "d" * 64   # a 64-char idempotency key (sha256 hex shape)


def test_create_payment_success_maps_to_settled():
    def req(method, url, json=None, params=None):
        assert method == "POST" and url.endswith("/payment_links")
        assert json["amount"] == 1_500_000 and json["currency"] == "INR"   # 15000 INR -> paise
        assert len(json["reference_id"]) == 40                              # truncated
        return (201, {"id": "plink_1", "short_url": "https://rzp.io/i/abc", "status": "created"})

    r = RazorpayClient(key_id="k", key_secret="s", requester=req).create_payment(15000, "vend-1", KEY)
    assert r.status == "settled" and r.id == "plink_1" and r.short_url == "https://rzp.io/i/abc"


def test_create_payment_duplicate_reference():
    def req(method, url, json=None, params=None):
        return (400, {"error": {"description": "Payment link with the given reference id already exists."}})

    r = RazorpayClient(key_id="k", key_secret="s", requester=req).create_payment(15000, "v", KEY)
    assert r.status == "duplicate"


def test_create_payment_other_error_raises():
    def req(method, url, json=None, params=None):
        return (500, {"error": {"description": "server error"}})

    with pytest.raises(ProviderDownError):
        RazorpayClient(key_id="k", key_secret="s", requester=req).create_payment(15000, "v", KEY)


def test_get_payment_by_key_existing_is_settled():
    def req(method, url, json=None, params=None):
        assert method == "GET" and params["reference_id"] == KEY[:40]
        return (200, {"payment_links": [{"id": "plink_1", "short_url": "u", "status": "created"}]})

    r = RazorpayClient(key_id="k", key_secret="s", requester=req).get_payment_by_idempotency_key(KEY)
    assert r.status == "settled" and r.id == "plink_1"


def test_get_payment_by_key_missing_is_unknown():
    def req(method, url, json=None, params=None):
        return (200, {"payment_links": []})

    r = RazorpayClient(key_id="k", key_secret="s", requester=req).get_payment_by_idempotency_key(KEY)
    assert r.status == "unknown"


def test_verify_razorpay_signature():
    secret, body = "whsec", b'{"event":"payment_link.paid"}'
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    assert verify_razorpay_signature(body=body, signature_header=sig, secret=secret) is True
    assert verify_razorpay_signature(body=body, signature_header="deadbeef", secret=secret) is False
    assert verify_razorpay_signature(body=body, signature_header=sig, secret="") is False  # fail closed


def test_get_payment_client_selects_razorpay(monkeypatch):
    monkeypatch.setattr(pay.settings, "razorpay_key_id", "rzp_test_x")
    monkeypatch.setattr(pay.settings, "razorpay_key_secret", "secret")
    assert isinstance(pay.get_payment_client(), RazorpayClient)


def test_get_payment_client_unconfigured_raises(monkeypatch):
    monkeypatch.setattr(pay.settings, "razorpay_key_id", "")
    monkeypatch.setattr(pay.settings, "razorpay_key_secret", "")
    with pytest.raises(NotImplementedError):
        pay.get_payment_client()
