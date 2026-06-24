"""PlacesAgent — vendor discovery for non-flight categories (Phase 2).

Builds a Places query from the parsed intent, dedups by google_place_id
(Fix 08), cross-checks the internal vendor graph (rated/known vendors rank
first), and returns the top-N candidates for RFQ dispatch.

The Google Places call and the vendor-graph lookup are both injectable so the
ranking logic is unit-testable without live credentials or a database.
"""
from __future__ import annotations

import logging

from services.google_places import search_places

log = logging.getLogger(__name__)

# Map our category enum to a natural-language Places search term.
CATEGORY_SEARCH_TERMS = {
    "fb": "corporate caterers and snack suppliers",
    "water": "packaged drinking water suppliers",
    "stationery": "office stationery suppliers",
    "it_hardware": "computer hardware and laptop dealers",
    "hotel": "business hotels",
    "generic": "B2B suppliers",
}


class PlacesAgent:
    def __init__(self, *, search_fn=None, known_vendors_fn=None):
        # search_fn(query) -> list[normalized vendor dicts]; defaults to live Places.
        self._search_fn = search_fn
        # known_vendors_fn(category, city) -> {place_id: {"score","band","id"}}
        # (the internal vendor graph; wired to the DB in a later increment).
        self._known_vendors_fn = known_vendors_fn

    async def search(self, intent: dict, *, limit: int = 3) -> list[dict]:
        category = intent.get("category") or "generic"
        location = intent.get("location") or intent.get("destination") or ""
        term = CATEGORY_SEARCH_TERMS.get(category, CATEGORY_SEARCH_TERMS["generic"])
        query = f"{term} in {location}".strip()

        if self._search_fn is not None:
            candidates = await self._search_fn(query)
        else:
            candidates = await search_places(query, city=location)

        # Dedup by google_place_id (Fix 08).
        seen: set = set()
        deduped: list[dict] = []
        for c in candidates:
            pid = c.get("google_place_id")
            if pid and pid in seen:
                continue
            if pid:
                seen.add(pid)
            deduped.append(c)

        # Cross-check the internal vendor graph — attach known score/band.
        known = await self._known_vendors_fn(category, location) if self._known_vendors_fn else {}
        for c in deduped:
            k = known.get(c.get("google_place_id"))
            c["composite_score"] = k.get("score") if k else None
            c["score_band"] = k.get("band") if k else "unproven"
            c["vendor_id"] = k.get("id") if k else None

        # Rank: scored/known vendors first (highest score), then unproven by
        # Google rating. (Matches the core-flow rule: rated vendors first.)
        def sort_key(c: dict):
            score = c.get("composite_score")
            return (0 if score is not None else 1, -(score or 0), -(c.get("google_rating") or 0))

        deduped.sort(key=sort_key)
        log.debug("PlacesAgent: %d candidates -> top %d for %s/%s", len(deduped), limit, category, location)
        return deduped[:limit]
