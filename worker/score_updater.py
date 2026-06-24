"""Vendor score reconciliation job (Fix 09 — worker process).

The immediate path recomputes a vendor's score inline when a rating arrives
(core.rating.on_rating_received). This batch reconciler exists to re-derive
scores for a set of vendors out-of-band (e.g. after a scoring-weight change or a
backfill).
"""
from __future__ import annotations

import logging

from core.db import Store, SupabaseStore
from core.vendor_scorer import calculate_vendor_score

log = logging.getLogger(__name__)


async def reconcile_vendor_scores(vendor_ids, *, store: Store | None = None) -> dict:
    store = store or SupabaseStore()
    results: dict[str, dict] = {}
    for vendor_id in vendor_ids:
        try:
            results[vendor_id] = await calculate_vendor_score(vendor_id, store=store)
        except Exception:
            log.exception("score reconcile failed for vendor %s", vendor_id)
    return results
