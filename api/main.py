"""FastAPI application entrypoint.

Phase 1: a minimal, runnable app so `uvicorn api.main:app` and `docker-compose
up` work. Feature routers (auth, goals, approvals, orders, vendors, ratings,
company, webhooks) are registered in later phases as each is built — see the
build order in CLAUDE_CODE_START.md.

Non-negotiable rule (Fix 09): the APScheduler background worker runs as a
SEPARATE process (worker/main.py). This module must NEVER start the scheduler.
"""
import logging

from fastapi import FastAPI

from api.routes import approvals, goals, webhooks

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="ProcureOS API", version="0.1.0")


@app.get("/health")
async def health() -> dict:
    """Liveness probe. Confirms the API process is up."""
    return {"status": "ok", "service": "api", "phase": 2}


app.include_router(goals.router)
app.include_router(approvals.router)
app.include_router(webhooks.router)

# Phase 2+ will register more routers here (goals, orders, vendors, ...) and
# Phase 3 adds auth. The Slack webhook is HMAC-verified (Fix 11); the approval
# endpoints use magic-link tokens (Fix 12) until JWT auth lands.
