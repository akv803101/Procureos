"""Vendor scoring — the cross-company intelligence asset (PRD Section 12).

calculate_vendor_score recomputes a vendor's composite score from its rating
history after every new rating. Five signals, each normalized 0-100, combined by
fixed weights, then mapped to a score band. A vendor with fewer than 3 rated
orders is 'unproven' (no score) — distinct from 'flagged' (a scored vendor below
50).

DENOMINATOR MODEL (design decision vs the spec's two-collection reference):
exactly ONE rating row is created per delivered order (core.rating.on_delivery_
confirmed), with the system signals (on-time, price-accurate, repeat) filled at
delivery time regardless of whether the employee later taps 👍/👎. So the rating
set IS the delivered-order set: the SLA/price/repeat signals and the unproven
gate use len(ratings) as the delivered-order count, matching the spec's
len(orders). Only the satisfaction signal differs — it averages over the rows
that were actually tapped (overall_rating set), since an un-tapped row carries no
satisfaction signal.
"""
from __future__ import annotations

import logging
import statistics

from core.db import Store, SupabaseStore

log = logging.getLogger(__name__)

_default_store: Store = SupabaseStore()

MIN_ORDERS_FOR_SCORE = 3

# Signal weights (sum to 1.0).
W_SATISFACTION = 0.35
W_SLA = 0.25
W_PRICE = 0.20
W_RESPONSE = 0.10
W_REPEAT = 0.10


def _response_score(median_mins: float) -> float:
    """Buckets from the spec: <30m=100, 30-60m=80, 60-240m=60, >=240m=30."""
    if median_mins < 30:
        return 100.0
    if median_mins < 60:
        return 80.0
    if median_mins < 240:
        return 60.0
    return 30.0


def _band(composite: float) -> str:
    if composite >= 85:
        return "preferred"   # auto-selectable if within budget
    if composite >= 70:
        return "reliable"
    if composite >= 50:
        return "provisional"
    return "flagged"         # < 50 — human review


async def calculate_vendor_score(vendor_id: str, *, store: Store | None = None) -> dict:
    """Recompute and persist a vendor's composite score. Returns
    {"score", "band", "components"} (score/components None when unproven)."""
    store = store or _default_store
    ratings = await store.get_ratings_for_vendor(vendor_id)
    n = len(ratings)
    if n < MIN_ORDERS_FOR_SCORE:
        await store.update_vendor_score(vendor_id, None, "unproven")
        return {"score": None, "band": "unproven", "reason": "Insufficient data", "components": None}

    # Signal 1 — user satisfaction: average ONLY over orders the employee actually
    # rated (overall_rating set). Coalescing un-tapped rows to 0 would wrongly drag
    # the heaviest signal down. Neutral 60 until any feedback arrives.
    tapped = [r.overall_rating for r in ratings if r.overall_rating is not None]
    satisfaction = (sum(tapped) / len(tapped) / 5 * 100) if tapped else 60.0
    # Signals 2, 3, 5 — system-calculated for EVERY delivered order; denominator is
    # the full rating set (= delivered-order count; see DENOMINATOR MODEL above).
    sla = sum(1 for r in ratings if r.delivered_on_time) / n * 100
    price = sum(1 for r in ratings if r.price_accurate) / n * 100
    repeat = sum(1 for r in ratings if r.is_repeat_order) / n * 100
    # Signal 4 — response speed (median RFQ response). Neutral 60 when no data
    # (the spec's median([]) would raise; we treat no-data as neutral).
    resp_vals = [r.response_time_mins for r in ratings if r.response_time_mins is not None]
    response = _response_score(statistics.median(resp_vals)) if resp_vals else 60.0

    raw = (satisfaction * W_SATISFACTION + sla * W_SLA + price * W_PRICE
           + response * W_RESPONSE + repeat * W_REPEAT)
    band = _band(raw)             # band the UNROUNDED composite (boundary-correct)
    composite = round(raw, 1)     # round only for persistence/return
    components = {
        "satisfaction": round(satisfaction, 1), "sla": round(sla, 1),
        "price": round(price, 1), "response": response, "repeat": round(repeat, 1),
    }

    await store.update_vendor_score(vendor_id, composite, band)
    await store.log_score_history(vendor_id, composite, components, n)
    log.debug("vendor %s score=%.1f band=%s (n=%d)", vendor_id, composite, band, n)
    return {"score": composite, "band": band, "components": components}
