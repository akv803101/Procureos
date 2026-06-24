"""PlacesAgent — query building, dedup (Fix 08), vendor-graph ranking."""
from agents.specialist.places_agent import PlacesAgent


async def test_search_builds_query_dedups_and_ranks():
    captured = {}

    async def fake_search(query):
        captured["query"] = query
        return [
            {"google_place_id": "p1", "name": "A", "google_rating": 4.0},
            {"google_place_id": "p2", "name": "B", "google_rating": 4.8},
            {"google_place_id": "p1", "name": "A duplicate", "google_rating": 4.0},  # dup
            {"google_place_id": "p3", "name": "C", "google_rating": 3.5},
        ]

    async def known(category, city):
        # p3 is a known, highly-rated vendor in our graph.
        return {"p3": {"score": 90.0, "band": "preferred", "id": "vend-3"}}

    agent = PlacesAgent(search_fn=fake_search, known_vendors_fn=known)
    out = await agent.search({"category": "fb", "location": "Koramangala"}, limit=3)

    # Query carries the category term + location.
    assert "caterers" in captured["query"].lower()
    assert "koramangala" in captured["query"].lower()

    ids = [v["google_place_id"] for v in out]
    assert ids.count("p1") == 1                 # deduped
    assert ids[0] == "p3"                        # known/scored vendor ranks first
    assert ids[1] == "p2"                        # then highest Google rating
    assert out[0]["composite_score"] == 90.0
    assert out[0]["vendor_id"] == "vend-3"
    assert out[1]["score_band"] == "unproven"   # not in the graph


async def test_search_respects_limit():
    async def fake(q):
        return [{"google_place_id": f"p{i}", "name": str(i), "google_rating": 4.0} for i in range(10)]

    agent = PlacesAgent(search_fn=fake)
    out = await agent.search({"category": "water", "location": "BLR"}, limit=3)
    assert len(out) == 3
