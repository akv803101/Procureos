"""Vendor scoring — 5-signal composite, bands, unproven gate."""
from core.db import InMemoryStore, Rating
from core.vendor_scorer import calculate_vendor_score


def _r(vendor="v1", overall=5, on_time=True, price_ok=True, resp=20, repeat=False) -> Rating:
    return Rating(id="", order_id="o", vendor_id=vendor, company_id="co1",
                  overall_rating=overall, delivered_on_time=on_time, price_accurate=price_ok,
                  response_time_mins=resp, is_repeat_order=repeat)


async def _store_with(ratings):
    store = InMemoryStore()
    for r in ratings:
        await store.create_rating(r)
    return store


async def test_unproven_below_three_ratings():
    store = await _store_with([_r(), _r()])
    res = await calculate_vendor_score("v1", store=store)
    assert res["band"] == "unproven"
    assert res["score"] is None


async def test_all_perfect_is_preferred():
    store = await _store_with([_r() for _ in range(3)])  # 5★, on-time, price-ok, fast, no repeat
    res = await calculate_vendor_score("v1", store=store)
    # 100*.35 + 100*.25 + 100*.20 + 100*.10 + 0*.10 = 90.0
    assert res["score"] == 90.0
    assert res["band"] == "preferred"
    assert res["components"]["satisfaction"] == 100.0
    assert store.score_history[-1]["order_count"] == 3


async def test_reliable_band():
    # overall 4 -> sat 80*.35=28; +25+20+10 = 83 -> reliable
    store = await _store_with([_r(overall=4) for _ in range(3)])
    assert (await calculate_vendor_score("v1", store=store))["band"] == "reliable"


async def test_provisional_band():
    # overall 3 -> 21; sla 25; price 0; resp 10 = 56 -> provisional
    store = await _store_with([_r(overall=3, price_ok=False) for _ in range(3)])
    assert (await calculate_vendor_score("v1", store=store))["band"] == "provisional"


async def test_flagged_band():
    # overall 2 ->14; sla 0; price 0; resp(300>=240->30)*.10=3 = 17 -> flagged
    store = await _store_with([_r(overall=2, on_time=False, price_ok=False, resp=300) for _ in range(3)])
    assert (await calculate_vendor_score("v1", store=store))["band"] == "flagged"


async def test_missing_response_data_is_neutral():
    store = await _store_with([_r(resp=None) for _ in range(3)])
    res = await calculate_vendor_score("v1", store=store)
    assert res["components"]["response"] == 60.0   # neutral when no response data


async def test_response_buckets():
    # response-only effect: 3 ratings at 45 mins -> bucket 80
    store = await _store_with([_r(resp=45) for _ in range(3)])
    assert (await calculate_vendor_score("v1", store=store))["components"]["response"] == 80.0
