"""RFQ-timeout job (Fix 09 worker, separate process).

Two failure modes this closes:
  1. A goal got fewer than MIN_QUOTES_TO_RANK quotes (e.g. only ONE vendor replied —
     the norm for cold outreach), so on_quote_collected left it in quotes_received
     forever with no approval card. Past a deadline we rank with whatever we have.
  2. A goal got NO reply at all (still pending_rfq). Past a longer deadline we move it
     to rfq_timeout (the canonical no-reply state) instead of stranding it.

Runs every few minutes; all state writes go through the Fix 05 CAS lock, and the
store is injected (get_store) so the worker shares state with the API process.
"""
from __future__ import annotations

import logging

from agents.orchestrator import _rank_and_request_approval
from core.clients import get_redis
from core.state_machine import GoalState, transition_goal_state
from core.store import get_store

log = logging.getLogger("worker.rfq_timeout")

RANK_AFTER_MIN = 30        # quotes_received with >=1 quote -> rank with what we have
ESCALATE_AFTER_MIN = 120   # pending_rfq with 0 quotes -> escalate to a human


async def run_rfq_timeout(*, store=None, redis=None) -> dict:
    store = store or get_store()
    redis = redis or get_redis()
    ranked = timed_out = 0

    # 1) stuck below the quote threshold -> rank now (single-vendor / slow-reply case)
    for g in await store.get_stale_goals([GoalState.QUOTES_RECEIVED.value], RANK_AFTER_MIN):
        quotes = await store.get_collected_quotes(g.id)
        if quotes and await _rank_and_request_approval(g.id, store=store, redis=redis):
            ranked += 1

    # 2) no reply at all -> move to rfq_timeout (canonical no-reply state) for follow-up
    for g in await store.get_stale_goals([GoalState.PENDING_RFQ.value], ESCALATE_AFTER_MIN):
        if not await store.get_collected_quotes(g.id):
            if await transition_goal_state(g.id, GoalState.PENDING_RFQ, GoalState.RFQ_TIMEOUT,
                                           store=store, redis=redis):
                timed_out += 1

    if ranked or timed_out:
        log.info("rfq_timeout: ranked=%d timed_out=%d", ranked, timed_out)
    return {"ranked": ranked, "timed_out": timed_out}
