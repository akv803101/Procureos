"""Conversational procurement agent (demo) — chat with the agent in the browser.

GET  /chat          -> a minimal chat UI.
POST /chat/message  -> {session, message} -> {reply, vendors?, rfq?}

This is a REAL LLM-driven agent (Claude + tool use), not a scripted funnel: it
holds the conversation, answers follow-up questions, asks for missing mandatory
fields naturally (delivery city + exact address; budget optional), and decides on
its own when it has enough to call the `find_vendors` tool. Read-only — it never
sends WhatsApp, writes to the DB, or moves money.

Session history is in-memory (demo only) keyed by a client-generated session id.
"""
from __future__ import annotations

import logging
from datetime import date

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from agents.orchestrator import _rfq_template_params, ref_code_for_goal
from agents.specialist.places_agent import PlacesAgent
from core.config import settings
from core.store import get_store

router = APIRouter(tags=["chat"])
log = logging.getLogger(__name__)

COMPANY_CITY = "Bengaluru"
TOP_N = 5
CHAT_MODEL = "claude-sonnet-4-6"
TEMPLATE_BODY = (
    "Hi {0}, this is IntelliBridge Procurement. We're sourcing {1} for delivery "
    "in {2}, needed by {3}. If you can supply, please reply here with your best "
    "price (incl. GST) and availability. Quote ref: {4}. Thanks!"
)

SYSTEM = """You are ProcureOS, a procurement assistant for IntelliBridge, an Indian \
company based in Bengaluru. Employees chat with you in plain language to buy things \
(snacks/catering = "fb", drinking water = "water", office stationery, IT hardware, \
hotels, flights). Today is {today}. GST invoices are required by default for B2B.

Behave like a sharp, friendly colleague — NOT a form. Keep replies short and natural.

Before you can find vendors you MUST know: the category, the quantity, the delivery \
city/area, AND the exact delivery address (building / floor / area + landmark). Budget \
is OPTIONAL. A date is needed only for dated events. Ask for missing MANDATORY info \
conversationally, one or two things at a time — never dump a checklist.

Answer the employee's questions. If something can only be confirmed by the vendor \
(e.g. "do they serve non-veg?", "can they do it by Friday?"), say you'll include it \
in the request, or offer to add it as a requirement — do NOT restart the conversation.

When you have the mandatory info, call the find_vendors tool, then present the results \
warmly and concisely (the UI shows the vendor cards + drafted message, so don't repeat \
the full list verbatim — just summarise and tell them what happens next: they pick or \
you can send the RFQ). If the employee changes something (quantity, date, veg/non-veg, \
address), update it and call find_vendors again."""

FIND_VENDORS_TOOL = {
    "name": "find_vendors",
    "description": ("Search live for verified vendors matching the requirement and draft "
                    "an RFQ. Only call once you have category, quantity, city, and the exact "
                    "delivery_address. Call again whenever the requirement changes."),
    "input_schema": {
        "type": "object",
        "properties": {
            "category": {"type": "string",
                         "enum": ["fb", "water", "stationery", "it_hardware", "hotel", "flights", "generic"]},
            "item": {"type": "string", "description": "specific item, e.g. 'non-veg snacks', 'A4 paper'"},
            "quantity": {"type": "number", "description": "people for fb/hotel/flights, units otherwise"},
            "city": {"type": "string"},
            "delivery_address": {"type": "string", "description": "building / floor / area + landmark"},
            "needed_by": {"type": "string", "description": "YYYY-MM-DD if a date was given, else omit"},
            "budget": {"type": "string", "description": "free text e.g. '₹100/person' or '40k', if given"},
            "special_requirements": {"type": "string"},
        },
        "required": ["category", "city", "delivery_address"],
    },
}

_SESSIONS: dict[str, list] = {}


def _intent_from_args(a: dict) -> dict:
    return {
        "category": a.get("category", "generic"),
        "subcategory": a.get("item"),
        "quantity": a.get("quantity"),
        "location": a.get("city"),
        "delivery_address": a.get("delivery_address"),
        "needed_by": a.get("needed_by"),
        "budget_hint": a.get("budget"),
        "special_requirements": a.get("special_requirements"),
        "gst_required": True,
    }


async def _find_vendors(args: dict) -> tuple[str, dict]:
    """Run live discovery + draft the RFQ. Returns (summary_for_model, ui_data)."""
    intent = _intent_from_args(args)
    agent = PlacesAgent(known_vendors_fn=get_store().get_known_vendors)
    vendors = await agent.search(intent, limit=TOP_N)
    cards = [{"name": v.get("name"), "phone": v.get("phone"), "rating": v.get("google_rating"),
              "reviews": v.get("review_count"), "address": v.get("address")} for v in vendors]
    rfq = None
    reachable = [v for v in vendors if v.get("phone")]
    if reachable:
        params = _rfq_template_params(reachable[0]["name"], intent, ref_code_for_goal("chat-demo"))
        rfq = TEMPLATE_BODY.format(*params)
    summary = (f"Found {len(cards)} verified vendors: "
               + "; ".join(f"{c['name']} (⭐{c['rating']}, {c['reviews']} reviews)" for c in cards)
               + ". A draft RFQ to the top vendor is ready. (These are shown to the user as cards.)")
    return summary, {"vendors": cards, "rfq": rfq}


@router.post("/chat/message")
async def chat_message(request: Request) -> dict:
    from anthropic import AsyncAnthropic

    body = await request.json()
    session = body.get("session", "default")
    text = (body.get("message") or "").strip()
    history = _SESSIONS.setdefault(session, [])
    history.append({"role": "user", "content": text})

    client = AsyncAnthropic(api_key=settings.anthropic_api_key)
    system = SYSTEM.format(today=date.today().isoformat())
    ui_data: dict = {}

    try:
        for _ in range(5):  # allow a tool round-trip or two
            resp = await client.messages.create(
                model=CHAT_MODEL, max_tokens=1024, temperature=0,
                system=system, tools=[FIND_VENDORS_TOOL], messages=history,
            )
            # Persist the assistant turn (reconstruct clean blocks for re-sending).
            assistant_blocks: list = []
            for b in resp.content:
                if b.type == "text":
                    assistant_blocks.append({"type": "text", "text": b.text})
                elif b.type == "tool_use":
                    assistant_blocks.append({"type": "tool_use", "id": b.id,
                                             "name": b.name, "input": b.input})
            history.append({"role": "assistant", "content": assistant_blocks})

            if resp.stop_reason == "tool_use":
                results = []
                for b in resp.content:
                    if b.type == "tool_use" and b.name == "find_vendors":
                        summary, data = await _find_vendors(b.input)
                        ui_data = data
                        results.append({"type": "tool_result", "tool_use_id": b.id, "content": summary})
                history.append({"role": "user", "content": results})
                continue

            reply = "".join(b.text for b in resp.content if b.type == "text").strip()
            return {"reply": reply or "…", "done": bool(ui_data), **ui_data}
    except Exception as e:  # noqa: BLE001 — surface a friendly message in the demo UI
        log.exception("chat agent error")
        return {"reply": f"(agent error: {e})", "done": False}

    return {"reply": "Sorry, I got stuck — could you rephrase?", "done": False}


@router.get("/chat", response_class=HTMLResponse)
async def chat_page() -> str:
    return _CHAT_HTML


_CHAT_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>ProcureOS — chat</title>
<style>
  :root { --bg:#0f1115; --card:#181b22; --me:#2563eb; --bot:#222733; --text:#e6e8ee; --muted:#8b93a7; }
  * { box-sizing:border-box; } body { margin:0; background:var(--bg); color:var(--text);
    font:15px/1.5 -apple-system,Segoe UI,Roboto,sans-serif; height:100vh; display:flex; flex-direction:column; }
  header { padding:14px 18px; background:var(--card); border-bottom:1px solid #262b36; font-weight:600; }
  header small { color:var(--muted); font-weight:400; margin-left:8px; }
  #log { flex:1; overflow-y:auto; padding:18px; display:flex; flex-direction:column; gap:12px; }
  .row { display:flex; } .row.me { justify-content:flex-end; }
  .bubble { max-width:76%; padding:10px 14px; border-radius:14px; white-space:pre-wrap; }
  .me .bubble { background:var(--me); color:#fff; border-bottom-right-radius:4px; }
  .bot .bubble { background:var(--bot); border-bottom-left-radius:4px; }
  .vendors { display:flex; flex-direction:column; gap:8px; margin-top:6px; }
  .v { background:#1d2230; border:1px solid #2a3040; border-radius:10px; padding:10px 12px; }
  .v b { color:#fff; } .v .meta { color:var(--muted); font-size:13px; }
  .rfq { margin-top:10px; background:#16261c; border:1px solid #234; border-left:3px solid #3fb950;
    padding:10px 12px; border-radius:8px; color:#cfe8d6; font-size:14px; }
  .tag { display:inline-block; font-size:11px; color:#9fb0ff; background:#1b2030; padding:1px 7px;
    border-radius:6px; margin-bottom:6px; }
  form { display:flex; gap:8px; padding:14px; background:var(--card); border-top:1px solid #262b36; }
  input { flex:1; background:#0f1320; border:1px solid #2a3040; color:var(--text); padding:11px 13px;
    border-radius:10px; font-size:15px; outline:none; }
  button { background:var(--me); color:#fff; border:0; padding:0 18px; border-radius:10px;
    font-size:15px; cursor:pointer; } button:disabled { opacity:.5; }
</style></head>
<body>
  <header>ProcureOS <small>procurement assistant — describe what you need</small></header>
  <div id="log"></div>
  <form id="f"><input id="m" autocomplete="off"
     placeholder="e.g. On 8th July there's a snack party in office, 100 employees" autofocus>
     <button id="send">Send</button></form>
<script>
  const sid = "s_" + Math.random().toString(36).slice(2);
  const log = document.getElementById('log'), f = document.getElementById('f'),
        m = document.getElementById('m'), send = document.getElementById('send');
  function esc(s){ return (s||'').replace(/[&<>]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
  function bubble(who, html){ const r=document.createElement('div'); r.className='row '+who;
    r.innerHTML='<div class="bubble">'+html+'</div>'; log.appendChild(r); log.scrollTop=log.scrollHeight; return r; }
  function vendorsHtml(vs){ if(!vs||!vs.length) return '';
    return '<div class="vendors">'+vs.map((v,i)=>'<div class="v"><b>'+(i+1)+'. '+esc(v.name)+'</b>'
      +' <span class="meta">— '+(v.phone?esc(v.phone):'no phone')+' · ⭐'+(v.rating??'?')+' ('+(v.reviews??0)+')</span>'
      +(v.address?'<div class="meta">'+esc(v.address)+'</div>':'')+'</div>').join('')+'</div>'; }
  bubble('bot', "Hi! Tell me what you need to procure and I'll find vetted vendors.");
  f.onsubmit = async (e) => { e.preventDefault(); const text=m.value.trim(); if(!text) return;
    bubble('me', esc(text)); m.value=''; send.disabled=true;
    const t = bubble('bot', '…');
    try {
      const r = await fetch('/chat/message', {method:'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({session:sid, message:text})});
      const d = await r.json();
      let html = esc(d.reply);
      if (d.vendors) html += vendorsHtml(d.vendors);
      if (d.rfq) html += '<div class="rfq"><span class="tag">drafted RFQ</span><br>'+esc(d.rfq)+'</div>';
      t.querySelector('.bubble').innerHTML = html;
    } catch(err){ t.querySelector('.bubble').textContent = 'Error: '+err; }
    send.disabled=false; m.focus(); log.scrollTop=log.scrollHeight;
  };
</script></body></html>"""
