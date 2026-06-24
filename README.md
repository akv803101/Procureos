# ProcureOS

AI commercial-execution platform. An employee states a procurement goal in plain
language; the agent finds vendors, collects quotes, presents ranked options for a
single human sign-off, pays, and files the GST invoice. India-first, 6 launch
categories (flights, hotels, F&B, water, stationery, IT hardware).

> Build order and rules live in `CLAUDE_CODE_START.md`. Coding standards in
> `coding_philosophy.md`. Both are binding.

## Status — Phase 1 (Foundation) complete

Built and verified:
- Project structure (`bootstrap.md` layout) + `requirements*.txt`, Docker, compose.
- 15 SQL migrations (`migrations/versions/`) + ordered runner (`migrations/apply.py`).
- Supabase setup: JWT-claims Edge Function, RLS (Fix 17), storage, indexes.
- **Fixes 01–05** (the prerequisites before any feature code), each in its
  canonical home with unit tests:
  | Fix | What | Home |
  |---|---|---|
  | 01 | Payment idempotency + safe retry | `services/payment.py` |
  | 02 | Atomic budget re-check (Redis lock) | `core/budget_engine.py` |
  | 03 | GST card buffer (×1.28) | `core/budget_engine.py` |
  | 04 | Approval TTL + price re-fetch | `core/approval_manager.py` |
  | 05 | Distributed state-transition lock | `core/state_machine.py` |

Gated on live Supabase credentials (not yet run): applying migrations, deploying
the Edge Function, applying RLS/storage/indexes.

## Run the tests (no external services needed)

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest          # 25 pass; the live-Redis test skips if no Redis
```

The Fix 02 / Fix 05 suites also have live-Redis variants that run automatically
when a Redis is reachable at `REDIS_URL` (e.g. `docker compose up redis`).

## Set up the database (needs live Supabase)

1. `cp .env.example .env` and fill in Supabase + Redis values.
2. `python -m migrations.apply`  ← applies migrations 001–015 in order.
   - **Note:** migrations are raw SQL run by this runner, *not* Alembic.
     `alembic upgrade head` is **not** the path for V1 (see `migrations/README.md`).
   - `python -m migrations.apply --status` shows applied vs pending.
3. Deploy the Edge Function + storage + indexes — see `supabase/README.md`.

## Run the app

```bash
docker compose up        # api on :8000 (health: GET /health), worker, redis
```

`api` (FastAPI) and `worker` (APScheduler) are **separate processes** — Fix 09.
The scheduler never runs inside the API.

## Layout (key directories)

```
api/        FastAPI app + routes (Phase 2+ routers)
agents/     LLM router, orchestrator, specialists, prompts (Phase 2)
services/   external integrations (payment = Fix 01 done; others Phase 2+)
core/       business logic: state_machine, budget_engine, approval_manager (Fixes 02–05)
models/     SQLAlchemy models (Phase 2 data-access layer)
migrations/ 15 SQL migrations + apply.py runner
supabase/   Edge Function, RLS notes, storage, indexes
worker/     APScheduler entrypoint (separate process)
tests/      unit tests for the fixes
```
