# ProcureOS — Progress

_Last updated: 2026-06-28 · 152 tests passing · see also [capability_map.html](capability_map.html)_

## TL;DR
The **agent brain + backend are built and battle-tested** (survived 4 adversarial
red-team rounds). The **chat now creates real, persisted procurement goals**. The
end-to-end live loop is **one step from closing** — gated only on a **Chat Mitra plan
upgrade** (to unlock inbound webhooks) + **Meta template approval**.

---

## The core loop — status by stage
| # | Stage | Status | Notes |
|---|-------|--------|-------|
| 1 | Conversational intake | ✅ live | Claude (Groq fallback); asks decision-relevant attributes |
| 2 | Intent parsing | ✅ live | multi-model router, confidence gate, circuit breakers |
| 3 | Vendor discovery | ✅ live | Google Places; category-aware queries |
| 4 | Vetting & ranking | ✅ live | credibility rank + Google review-summary sentiment + 0–100 service-risk score; high-risk auto-excluded |
| 5 | Draft RFQ | ✅ live | category-aware (delivery/hotel/flight); budget reveal is user-selectable |
| 6 | Save goal (chat→pipeline) | ✅ live | on confirm: real persisted goal + per-goal REF, → `pending_rfq` |
| 7 | Outreach (send) | 🟡 gated | WhatsApp template send built; **dispatch flip gated on Chat Mitra upgrade** |
| 8 | Collect quotes (inbound) | 🟡 gated | Chat Mitra adapter built (`X-Webhook-Signature`/`hmac_sha256`/`message.received`); **needs webhook live** |
| 9 | Rank options | ✅ live | + `rfq_timeout` worker so a single/slow reply never dead-ends |
| 10 | Approve | ✅ live | Slack Block Kit, HMAC, magic-link tokens |
| 11 | Pay | 🟡 test-mode | Razorpay test client; LIVE keys pending |
| 12 | Deliver → rate → vendor score | ✅ live | delivery worker + 5-signal score |

---

## Live-loop plan (A0 → A4)
- **A0 — provisioning (USER):** Chat Mitra plan upgrade + submit Meta template `rfq_first_contact_v1`; fill `META_WEBHOOK_SECRET`, Slack, Razorpay. _in progress_
- **A0b — Chat Mitra inbound spike:** ✅ scheme known + adapter built; awaiting one real webhook to pin the exact payload (route logs it).
- **A1 — chat → real goal pipeline:** ✅ done (verified live).
- **A2 — split-brain fix + single-vendor dead-end:** ✅ done (lazy `get_store`; `rfq_timeout` worker).
- **A3 — live reply hub (replies stream into chat):** ⏳ gated on inbound being live.
- **A4 — tunnel + webhook registration + one real round-trip:** ⏳ gated on A0.

**Gated on the Chat Mitra upgrade:** flip "queued" → actual WhatsApp dispatch · the live reply hub · paste `META_WEBHOOK_SECRET`.

---

## Hardening (done — 4 red-team rounds)
Anti-hallucination · grounded sentiment (never invents reviews/complaints) ·
read-only / no false "sent" · address gate · prompt-injection resistant ·
5 money/concurrency Fixes · multi-model fallback (Claude → Groq).

## The moat (planned)
Per-company partner graph (cold → tried → partner → preferred, reuse-first) ·
per-vendor channel learning · aggregator coverage (flights/events) · analytics ·
monetization (vendor subscriptions + labeled sponsored ranking) · fine-tuning.

## Provisioning (`.env`, gitignored)
- **Live:** Google Places · Anthropic · Groq · Supabase · Redis (local) · Chat Mitra API key
- **Pending:** `META_WEBHOOK_SECRET` (after Chat Mitra upgrade) · Slack · Razorpay (live)

## Deferred / known (low priority)
- Lazy-store fix on `delivery`/`rating`/`vendor_scorer` (not on the worker path).
- Confirm phrasing: "send the RFQ" works; "create the order" hits the can't-place-orders guard.
- `_clean_name` doesn't trim keyword-stuffed Google names without a `|`/` - ` separator.
- Email channel (outbound + inbound) for aggregators.
