"""Chat Mitra WABA client — WhatsApp send (Phase 2).

Gated on CHAT_MITRA_API_KEY. httpx is imported lazily so the module imports
without httpx installed; tests inject `send_fn` / `send_buttons_fn`.

NOTE: Chat Mitra's exact REST shape isn't pinned in the spec docs — the default
sender below models a WhatsApp-Cloud-style text send against
settings.chat_mitra_base_url. Confirm the endpoint/payload against Chat Mitra's
API before going live; only the default sender changes, not the call sites.
"""
from __future__ import annotations

import hashlib
import hmac
import logging

from core.config import settings

log = logging.getLogger(__name__)


def verify_meta_signature(*, body: bytes, signature_header: str, secret: str | None = None) -> bool:
    """Verify an inbound WhatsApp webhook HMAC.

    Meta signs the raw request body with HMAC-SHA256 and sends it as
    `X-Hub-Signature-256: sha256=<hex>`. Returns True only on a constant-time
    match. Fails closed if no secret is configured.
    (Chat Mitra may differ — confirm its signing scheme; only this check changes.)
    """
    secret = secret if secret is not None else settings.meta_webhook_secret
    if not secret:
        log.warning("META_WEBHOOK_SECRET not set — refusing inbound WhatsApp webhook")
        return False
    if not signature_header or not signature_header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature_header[len("sha256="):], expected)


async def _default_send(to: str, body: str) -> dict:
    import httpx

    if not settings.chat_mitra_api_key:
        raise RuntimeError("CHAT_MITRA_API_KEY not set — cannot send WhatsApp")
    payload = {
        "from": settings.chat_mitra_waba_number,
        "to": to,
        "type": "text",
        "text": {"body": body},
    }
    headers = {
        "Authorization": f"Bearer {settings.chat_mitra_api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(f"{settings.chat_mitra_base_url}/messages", headers=headers, json=payload)
        resp.raise_for_status()
        return resp.json()


async def send_text(to: str, body: str, *, send_fn=None) -> dict:
    """Send a plain WhatsApp text message. Returns the provider response."""
    send_fn = send_fn or _default_send
    log.debug("whatsapp send_text to=%s len=%d", to, len(body))
    return await send_fn(to, body)


async def send_buttons(to: str, body: str, buttons: list[dict], *, send_fn=None) -> dict:
    """Send interactive quick-reply buttons (used for rating + delivery confirm).

    `buttons` is a list of {"id": ..., "title": ...}. The inbound replies are
    routed by core.waba_router (Fix 06) on the button ids.
    """
    send_fn = send_fn or _default_send_buttons
    return await send_fn(to, body, buttons)


async def _default_send_buttons(to: str, body: str, buttons: list[dict]) -> dict:
    import httpx

    if not settings.chat_mitra_api_key:
        raise RuntimeError("CHAT_MITRA_API_KEY not set — cannot send WhatsApp")
    payload = {
        "from": settings.chat_mitra_waba_number,
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body},
            "action": {"buttons": [{"type": "reply", "reply": b} for b in buttons]},
        },
    }
    headers = {
        "Authorization": f"Bearer {settings.chat_mitra_api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(f"{settings.chat_mitra_base_url}/messages", headers=headers, json=payload)
        resp.raise_for_status()
        return resp.json()


async def send_template(to: str, template_name: str, *, language: str = "en",
                        body_params: list[str], send_fn=None) -> dict:
    """Send an APPROVED WhatsApp template — required for the FIRST contact with a
    vendor whose 24h session window is closed (WhatsApp disallows free-form
    business-initiated text). `body_params` fill the template's {{1}}..{{n}} body
    variables in order. Once the vendor replies (window opens), switch to
    send_text for free-form negotiation.
    """
    send_fn = send_fn or _default_send_template
    log.debug("whatsapp send_template to=%s tpl=%s vars=%d", to, template_name, len(body_params))
    return await send_fn(to, template_name, language, body_params)


async def _default_send_template(to: str, template_name: str, language: str,
                                 body_params: list[str]) -> dict:
    import httpx

    if not settings.chat_mitra_api_key:
        raise RuntimeError("CHAT_MITRA_API_KEY not set — cannot send WhatsApp")
    # WhatsApp Cloud API template shape (Chat Mitra proxies Meta). Static quick-reply
    # buttons are fixed at template-creation time, so only body params are sent here.
    # NOTE: confirm Chat Mitra's exact template-send payload before go-live — only
    # this default sender changes, not the call sites (same caveat as _default_send).
    payload = {
        "from": settings.chat_mitra_waba_number,
        "to": to,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language},
            "components": [
                {"type": "body",
                 "parameters": [{"type": "text", "text": p} for p in body_params]},
            ],
        },
    }
    headers = {
        "Authorization": f"Bearer {settings.chat_mitra_api_key}",
        "Content-Type": "application/json",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(f"{settings.chat_mitra_base_url}/messages", headers=headers, json=payload)
        resp.raise_for_status()
        return resp.json()


def normalize_inbound(payload: dict) -> dict:
    """Normalize a provider WhatsApp webhook to the flat shape waba_router (Fix 06)
    expects: {from, text, type, interactive}.

    Handles the Meta WhatsApp Cloud API nested shape
    (entry[].changes[].value.messages[]) that Chat Mitra proxies under your Meta
    Business Manager. If the payload is already flat (some BSPs post that
    directly), it's returned unchanged. Returns {} for non-message events
    (delivery/read status receipts) so the route can ignore them.

    If Chat Mitra's exact inbound shape differs from Meta's, THIS is the only
    function that changes — the router and handlers are unaffected.
    """
    if payload.get("from"):
        return payload  # already flat
    try:
        value = payload["entry"][0]["changes"][0]["value"]
    except (KeyError, IndexError, TypeError):
        return {}
    messages = value.get("messages") or []
    if not messages:
        return {}  # status receipt or other non-message event
    m = messages[0]
    out = {"from": m.get("from"), "type": m.get("type", "text"),
           "text": (m.get("text") or {}).get("body", "")}
    if m.get("type") == "interactive":
        out["interactive"] = m.get("interactive")
    elif m.get("type") == "button":
        # Template quick-reply tap (sent outside the 24h session window): the id is
        # in button.payload, label in button.text. Route it like a button_reply so
        # the rating/delivery prefixes still match.
        btn = m.get("button") or {}
        out["type"] = "interactive"
        out["interactive"] = {"button_reply": {"id": btn.get("payload") or ""}}
        out["text"] = btn.get("text", "")
    return out
