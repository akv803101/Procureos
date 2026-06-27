"""Google Places API client — low-level vendor discovery (Phase 2).

Uses the Places API v1 Text Search. Gated on GOOGLE_PLACES_API_KEY. httpx is
imported lazily inside the default fetch so this module imports without httpx
installed; tests inject `fetch` and never touch the network.

Fix 08: the returned `google_place_id` is the vendor identity / dedup key — the
phone number is NEVER used as identity.
"""
from __future__ import annotations

import logging

from core.config import settings

log = logging.getLogger(__name__)

# Field mask: only request what we map below. NOTE: `places.reviewSummary` is an
# Atmosphere-tier field (higher per-call cost) — it's Google's AI digest of the
# place's reviews (sentiment on quality/service/timeliness) and is what powers
# grounded vetting. (Individual `places.reviews` isn't entitled on this key.)
_FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,places.rating,"
    "places.userRatingCount,places.internationalPhoneNumber,"
    "places.nationalPhoneNumber,places.websiteUri,places.businessStatus,places.reviewSummary"
)


def _normalize(place: dict, city: str | None) -> dict:
    name = (place.get("displayName") or {}).get("text") or place.get("name", "")
    phone = place.get("internationalPhoneNumber") or place.get("nationalPhoneNumber")
    review_summary = ((place.get("reviewSummary") or {}).get("text") or {}).get("text")
    return {
        "google_place_id": place.get("id"),
        "name": name,
        "address": place.get("formattedAddress"),
        "city": city,
        "google_rating": place.get("rating"),
        "review_count": place.get("userRatingCount"),
        "phone": phone,
        "website": place.get("websiteUri"),
        "business_status": place.get("businessStatus"),
        "review_summary": review_summary,   # Google's AI digest of reviews (sentiment signal)
        "source": "google_places",
    }


async def _default_fetch(query: str) -> dict:
    """Real Places API call. Returns the raw JSON body."""
    import httpx

    if not settings.google_places_api_key:
        raise RuntimeError("GOOGLE_PLACES_API_KEY not set — cannot call Google Places")
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": settings.google_places_api_key,
        "X-Goog-FieldMask": _FIELD_MASK,
    }
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(
            "https://places.googleapis.com/v1/places:searchText",
            headers=headers,
            json={"textQuery": query},
        )
        resp.raise_for_status()
        return resp.json()


async def search_places(query: str, *, city: str | None = None, fetch=None) -> list[dict]:
    """Run a text search and return normalized vendor dicts.

    `fetch(query) -> raw_json` is injectable for tests.
    """
    fetch = fetch or _default_fetch
    raw = await fetch(query)
    vendors = [_normalize(p, city) for p in raw.get("places", [])]
    log.debug("search_places(%r) -> %d results", query, len(vendors))
    return vendors
