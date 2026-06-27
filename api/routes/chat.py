"""Conversational intake (demo) — chat with the agent in the browser.

GET  /chat          -> a minimal chat UI.
POST /chat/message  -> {session, message} -> {reply, done, vendors?, rfq?}

The agent parses the goal, asks for any MANDATORY missing field (place + delivery
address; budget optional), then runs live discovery + drafts the RFQ. Read-only:
it does NOT send WhatsApp, write to the DB, or move money.

Session state is in-memory (demo only) keyed by a client-generated session id.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from agents.orchestrator import (
    _human_needed_by,
    _human_requirement,
    _rfq_template_params,
    clarifying_questions,
    parse_intent,
    ref_code_for_goal,
)
from agents.specialist.places_agent import PlacesAgent
from core.store import get_store

router = APIRouter(tags=["chat"])

COMPANY_CITY = "Bengaluru"
TOP_N = 5
TEMPLATE_BODY = (
    "Hi {0}, this is IntelliBridge Procurement. We're sourcing {1} for delivery "
    "in {2}, needed by {3}. If you can supply, please reply here with your best "
    "price (incl. GST) and availability. Quote ref: {4}. Thanks!"
)

_SESSIONS: dict[str, dict] = {}


def _parse_budget(s: str):
    t = s.lower().replace("rs", "").replace("inr", "").replace("₹", "").replace(",", "").strip()
    mult = 1
    if t.endswith("k"):
        mult, t = 1000, t[:-1].strip()
    elif "lakh" in t or t.endswith("l"):
        mult, t = 100000, t.replace("lakh", "").rstrip("l").strip()
    try:
        return float(t) * mult
    except ValueError:
        return s


def _next_prompt(state: dict) -> str | None:
    """The next question to ask, or None when all mandatory fields are present."""
    intent = state["intent"]
    required = [q for q in clarifying_questions(intent) if q["required"]]
    if required:
        state["pending"] = required[0]["field"]
        return required[0]["ask"]
    if not state["budget_asked"] and not intent.get("budget_hint"):
        state["pending"] = "budget"
        state["budget_asked"] = True
        return "Any budget cap for this? (optional — reply 'skip')"
    state["pending"] = None
    return None


async def _run_pipeline(intent: dict) -> dict:
    """Live discovery + RFQ draft for the top vendor. No messages sent."""
    agent = PlacesAgent(known_vendors_fn=get_store().get_known_vendors)
    vendors = await agent.search(intent, limit=TOP_N)
    out = [{"name": v.get("name"), "phone": v.get("phone"),
            "rating": v.get("google_rating"), "reviews": v.get("review_count"),
            "address": v.get("address")} for v in vendors]
    rfq = None
    reachable = [v for v in vendors if v.get("phone")]
    if reachable:
        params = _rfq_template_params(reachable[0]["name"], intent, ref_code_for_goal("chat-demo"))
        rfq = TEMPLATE_BODY.format(*params)
    return {"vendors": out, "rfq": rfq}


@router.post("/chat/message")
async def chat_message(request: Request) -> dict:
    body = await request.json()
    session = body.get("session", "default")
    text = (body.get("message") or "").strip()
    state = _SESSIONS.setdefault(session, {"intent": None, "pending": None, "budget_asked": False})

    ack = None
    if state["intent"] is None:
        intent = await parse_intent(text, COMPANY_CITY)
        state["intent"] = intent
        ack = (f"Got it — {_human_requirement(intent)}, needed by {_human_needed_by(intent)}, "
               f"GST {'required' if intent.get('gst_required', True) else 'not needed'}.")
    else:
        field = state["pending"]
        if field == "budget":
            if text.lower() != "skip":
                state["intent"]["budget_hint"] = _parse_budget(text)
        elif field:
            state["intent"][field] = text

    nxt = _next_prompt(state)
    if nxt:
        return {"reply": (ack + "\n\n" if ack else "") + nxt, "done": False}

    result = await _run_pipeline(state["intent"])
    _SESSIONS.pop(session, None)  # reset so the next goal starts fresh
    intro = (ack + "\n\n" if ack else "") + "Here are the best-matched, verified vendors:"
    return {"reply": intro, "done": True, **result}


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
  .v a { color:#7aa2ff; text-decoration:none; }
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
