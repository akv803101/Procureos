# ProcureOS — Live Demo Runbook

Run one real goal end-to-end: **submit → discover → RFQ on WhatsApp → quote → rank → approve in Slack → pay with Razorpay (test) → deliver → rate → score.**

Demo-safe by design: with `DEMO_MODE=true`, discovery returns only **seeded** vendors, so RFQs reach your own number(s) — never real strangers. Razorpay runs in **test mode** — no real money moves.

---

## 0 · Prerequisites (accounts/keys)

| Need | Get it from | `.env` var(s) |
|---|---|---|
| Supabase project | supabase.com | `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `SUPABASE_JWT_SECRET`, `SUPABASE_POSTGRES_URL` |
| LLM | console.groq.com (free) + console.anthropic.com | `GROQ_API_KEY`, `ANTHROPIC_API_KEY` |
| WhatsApp (Chat Mitra BSP) | Chat Mitra dashboard | `CHAT_MITRA_API_KEY`, `CHAT_MITRA_WABA_NUMBER`, `META_WEBHOOK_SECRET`, `META_WEBHOOK_VERIFY_TOKEN` |
| Slack app | api.slack.com/apps | `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET` |
| Razorpay (test) | dashboard.razorpay.com → API Keys (Test) + Webhooks | `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET` |
| Public tunnel | ngrok / cloudflared | — |

> Google Places isn't needed in demo mode (seeded vendors are used). Redis is provided by `docker compose`.

---

## 1 · Configure
```bash
cp .env.example .env
# fill the values above, and add:
echo "DEMO_MODE=true" >> .env
```

## 2 · Database
```bash
python -m migrations.apply            # applies 001–017
python -m scripts.verify_supabase     # smoke-test every store method (optional but recommended)
```
Deploy the JWT-claims Edge Function + storage + indexes — see `supabase/README.md`.

## 3 · Seed the demo tenant
```bash
DEMO_PHONE=+91XXXXXXXXXX \
DEMO_VENDOR_PHONES="+91XXXXXXXXXX,+91YYYYYYYYYY" \
DEMO_SLACK_CHANNEL=#procurement \
python -m scripts.seed_demo
```
Copy the printed **company_id** and **employee_id**. (Use two vendor numbers so ranking has ≥2 quotes; if you only have one, you'll reply twice.)

## 4 · Start the app + tunnel
```bash
docker compose up        # api :8000, worker, redis
ngrok http 8000          # note the https URL, e.g. https://abc123.ngrok.io
```

## 5 · Register the webhooks (use the ngrok URL)
- **Slack** → your app → *Interactivity & Shortcuts* → Request URL = `https://abc123.ngrok.io/webhook/slack`. Add bot scope `chat:write`; install to the workspace; invite the bot to your approval channel.
- **WhatsApp (Chat Mitra)** → inbound webhook = `https://abc123.ngrok.io/webhook/whatsapp` (verify token = `META_WEBHOOK_VERIFY_TOKEN`).
- **Razorpay** → Settings → Webhooks → `https://abc123.ngrok.io/webhook/payment`, secret = `RAZORPAY_WEBHOOK_SECRET`, events: `payment_link.paid`.

## 6 · Run a goal
```bash
curl -s localhost:8000/goals -H 'Content-Type: application/json' -d '{
  "raw_input": "order snacks for 50 people Koramangala",
  "company_id": "<company_id from step 3>",
  "employee_id": "<employee_id from step 3>"
}'
# -> { "data": { "goal_id": "...", "status": "processing" } }
```

Then watch it flow:
1. **RFQs** arrive on the seeded vendor WhatsApp number(s). Reply to each with a price, e.g. `15000 all inclusive with GST, delivery Tuesday`. Keep the `REF:XXXXXXXX` in the thread (or just reply in the same chat).
2. Once ≥2 quotes are parsed, an **approval card** posts to your Slack channel. Tap **Approve** on an option.
3. Approval fires a **Razorpay payment link** (test mode); open it and pay with a [test card](https://razorpay.com/docs/payments/payments/test-card-details/) → the `payment` webhook confirms.
4. Mark delivery (the worker advances the order; send the vendor's `delivered`, or the delivery confirmation button).
5. A **rating prompt** (👍/👎) goes to the employee number. Tap it → the **vendor score** updates.

Check status any time:
```bash
curl -s localhost:8000/goals/<goal_id> | python -m json.tool
```

---

## Troubleshooting
- **Goal stuck in `processing`** → check the worker/app logs; `_run_process_goal` escalates to `operator_escalated` on failure. Usually a missing LLM key or no seeded vendors for that category/city.
- **No Slack card** → fewer than 2 quotes parsed (ranking needs ≥2), or quotes hit the confidence gate (ambiguous price → operator). Reply with a clear price.
- **WhatsApp webhook 401** → `META_WEBHOOK_SECRET` mismatch, or Chat Mitra's inbound shape differs — adjust `normalize_inbound` in `services/whatsapp.py` (only that function changes).
- **Payment webhook 401** → `RAZORPAY_WEBHOOK_SECRET` mismatch.
- **`SupabaseStore... not set`** → `SUPABASE_POSTGRES_URL` missing; without it the app silently uses the in-memory store (fine for a local dry-run, but data won't persist across processes).
