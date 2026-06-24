"""SupabaseStore smoke test — run once after migrations are applied.

Exercises every SupabaseStore method against the live schema: budget, goal +
quote collection, order, spend ledger (Fix 02), ratings + vendor scoring,
approval tokens, and REF-code lookup. Seeds a throwaway company + vendor and
cleans them up (cascades remove the children).

    cp .env.example .env            # fill SUPABASE_POSTGRES_URL etc.
    python -m migrations.apply      # apply 001-017
    python -m scripts.verify_supabase

Exits non-zero on the first failed check.
"""
import asyncio
import sys
from datetime import datetime, timedelta, timezone

from core.config import settings
from core.db import ApprovalToken, Goal, Order, Rating, SupabaseStore
from core.refcodes import ref_code

PASS, FAIL = "  ✓", "  ✗"


def ok(msg):
    print(f"{PASS} {msg}")


def die(msg):
    print(f"{FAIL} {msg}")
    sys.exit(1)


async def main() -> None:
    if not settings.supabase_postgres_url:
        die("SUPABASE_POSTGRES_URL not set — fill .env first")

    store = SupabaseStore()
    company_id = vendor_id = None
    try:
        company_id = str(await store._val(
            "INSERT INTO companies (name, budget_policies, slack_approval_channel) "
            "VALUES ($1, $2::jsonb, $3) RETURNING id",
            "Smoke Co", {"fb": 20000, "default": 5000}, "#smoke"))
        vendor_id = str(await store._val(
            "INSERT INTO vendors (name, category, phone, google_place_id, city, source) "
            "VALUES ($1, $2, $3, $4, $5, 'google_places') RETURNING id",
            "Smoke Caterers", "fb", "+910000000000", "gp_smoke", "Bengaluru"))
        ok(f"seeded company={company_id[:8]} vendor={vendor_id[:8]}")

        budget = await store.get_budget(company_id, "fb")
        assert budget.limit == 20000, budget.limit
        ok("get_budget reads budget_policies JSONB")

        goal = Goal(id="", status="processing", category="fb", company_id=company_id,
                    raw_input="smoke snacks", parsed_intent={"gst_required": True}, budget_limit=20000)
        goal_id = await store.create_goal(goal)
        assert (await store.get_goal_state(goal_id)) == "processing"
        await store.add_collected_quote(goal_id, {"vendor_id": vendor_id, "price": 15000})
        assert len(await store.get_collected_quotes(goal_id)) == 1
        ok(f"create_goal + collected_quotes (goal={goal_id[:8]})")

        await store.set_goal_state(goal_id, "pending_rfq")
        found = await store.get_goal_by_partial_id(ref_code(goal_id))
        assert found and found.id == goal_id, "REF lookup failed"
        ok("get_goal_by_partial_id matches the hash REF")

        order = Order(id="", goal_id=goal_id, vendor_id=vendor_id, company_id=company_id,
                      quoted_price=15000, status="placed")
        order_id = await store.create_order(order)
        assert (await store.get_order(order_id)).quoted_price == 15000
        ok(f"create_order + get_order (order={order_id[:8]})")

        await store.record_spend(company_id, "fb", 15000, order_id)
        spent = await store.get_spent_this_period(company_id, "fb")
        assert spent == 15000, spent
        ok("record_spend + get_spent_this_period (Fix 02 ledger)")

        rating_id = await store.create_rating(Rating(
            id="", order_id=order_id, vendor_id=vendor_id, company_id=company_id,
            delivered_on_time=True, price_accurate=True, response_time_mins=20))
        await store.update_rating(rating_id, overall_rating=5, satisfied=True)
        assert len(await store.get_ratings_for_vendor(vendor_id)) == 1
        await store.update_vendor_score(vendor_id, 88.5, "preferred")
        await store.log_score_history(vendor_id, 88.5, {"satisfaction": 100}, 3)
        scores = await store.get_vendor_scores([vendor_id])
        assert scores[vendor_id] == 88.5, scores
        ok("ratings + vendor scoring + history + get_vendor_scores")

        tok = "smoke-" + ref_code(goal_id)
        await store.create_approval_token(ApprovalToken(
            token=tok, goal_id=goal_id, approver_id=None,
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1)))
        rec = await store.get_approval_token(tok)
        assert rec and rec.goal_id == goal_id
        await store.mark_approval_token_used(tok, datetime.now(timezone.utc))
        assert (await store.get_approval_token(tok)).used_at is not None
        ok("approval token create/get/consume (Fix 12)")

        known = await store.get_known_vendors("fb", "Bengaluru")
        assert "gp_smoke" in known and known["gp_smoke"]["score"] == 88.5
        ok("get_known_vendors (rated-vendors-first cross-check)")

        print("\nAll SupabaseStore checks passed ✅")
    finally:
        if company_id:
            await store._exec("DELETE FROM companies WHERE id=$1::uuid", company_id)  # cascades goals/orders/spend/ratings/tokens
        if vendor_id:
            await store._exec("DELETE FROM vendors WHERE id=$1::uuid", vendor_id)
        if store._pool:
            await store._pool.close()


if __name__ == "__main__":
    asyncio.run(main())
