"""Seed a demo tenant for the live end-to-end run.

Creates one company (with a budget + Slack approval channel), one employee
(whose WhatsApp number receives the rating prompt), and demo vendors whose phone
numbers receive the RFQs. With DEMO_MODE=true, discovery returns ONLY these
seeded vendors — so RFQs go to your own number(s), never real strangers.

    cp .env.example .env                 # fill SUPABASE_POSTGRES_URL etc.
    python -m migrations.apply
    DEMO_PHONE=+91XXXXXXXXXX DEMO_SLACK_CHANNEL=#procurement python -m scripts.seed_demo

Prints the company_id / employee_id to use in POST /goals. Vendor phones default
to DEMO_PHONE; pass DEMO_VENDOR_PHONES="+91...,+91..." for two distinct vendors
(recommended — ranking needs >= 2 quotes).
"""
import asyncio
import os
import sys

from core.config import settings
from core.db import SupabaseStore


async def main() -> None:
    if not settings.supabase_postgres_url:
        sys.exit("SUPABASE_POSTGRES_URL not set — seed targets the live DB. Fill .env first.")

    phone = os.getenv("DEMO_PHONE")
    if not phone:
        sys.exit("Set DEMO_PHONE=+91XXXXXXXXXX (your WhatsApp number) and re-run.")
    channel = os.getenv("DEMO_SLACK_CHANNEL", "#procurement")
    vendor_phones = [p.strip() for p in os.getenv("DEMO_VENDOR_PHONES", phone + "," + phone).split(",") if p.strip()]
    city = os.getenv("DEMO_CITY", "Bengaluru")

    store = SupabaseStore()
    try:
        company_id = str(await store._val(
            "INSERT INTO companies (name, gst_number, budget_policies, slack_approval_channel, waba_number) "
            "VALUES ($1, $2, $3::jsonb, $4, $5) RETURNING id",
            "Demo Co", "29ABCDE1234F1Z5", {"fb": 20000, "water": 10000, "default": 5000},
            channel, settings.chat_mitra_waba_number or None))
        employee_id = str(await store._val(
            "INSERT INTO employees (company_id, name, whatsapp, role) "
            "VALUES ($1::uuid, $2, $3, 'employee') RETURNING id",
            company_id, "Demo Employee", phone))

        vendor_ids = []
        for i, vp in enumerate(vendor_phones, start=1):
            vid = await store.upsert_vendor({
                "name": f"Demo Caterer {i}", "category": "fb", "phone": vp,
                "google_place_id": f"demo_gp_{i}", "city": city, "google_rating": 4.5,
                "review_count": 100, "source": "agent_found",   # must satisfy the vendors.source CHECK
            })
            vendor_ids.append(vid)

        print("\n✅ Demo tenant seeded:")
        print(f"  company_id  = {company_id}")
        print(f"  employee_id = {employee_id}")
        print(f"  vendors     = {len(vendor_ids)} (fb, {city}) -> phones {vendor_phones}")
        print(f"  approvals post to Slack channel: {channel}")
        print("\nEnsure DEMO_MODE=true in .env, start the app, then submit a goal:\n")
        print(f"""  curl -s localhost:8000/goals -H 'Content-Type: application/json' -d '{{
    "raw_input": "order snacks for 50 people {city}",
    "company_id": "{company_id}",
    "employee_id": "{employee_id}"
  }}'\n""")
    finally:
        if store._pool:
            await store._pool.close()


if __name__ == "__main__":
    asyncio.run(main())
