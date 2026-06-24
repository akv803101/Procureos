# Migrations

## How the schema is applied (Phase 1 decision)

The schema is authored as **15 numbered raw `.sql` files** in `versions/`, applied
in filename order by a small runner:

```bash
python -m migrations.apply           # apply all pending
python -m migrations.apply --status  # list applied vs pending
```

### Why raw SQL instead of Alembic autogenerate

1. `bootstrap.md` specifies the schema as an explicit ordered list of `.sql`
   files — we honor that list 1:1.
2. The Supabase layer (RLS policies, helper functions, storage buckets) is raw
   SQL anyway, and `company_members.user_id` references `auth.users`, a table
   Supabase manages — Alembic autogenerate would fight both.
3. Karpathy rule: the simplest thing that works. Plain SQL keeps the schema
   readable and reviewable.

`alembic.ini` + `env.py` + `script.py.mako` are kept so future *Python* revisions
remain possible, but `migrations.apply` is the operative path for V1.

The runner records applied files in a `schema_migrations` ledger table, so it is
safe to re-run (already-applied files are skipped). Each file runs in its own
transaction.

## Order (FK dependencies require exactly this sequence)

```
001_create_companies            # root tenant
002_create_auth_members         # company_members  → companies, auth.users
003_create_employees            # → companies
004_create_vendors              # independent (shared graph)
005_create_goals                # → companies, employees
006_create_orders               # → goals, vendors, companies
007_create_vendor_ratings       # → vendors, orders, companies
008_create_vendor_score_history # → vendors
009_create_operator_logs        # → goals, vendors
010_create_notification_logs    # → goals
011_create_llm_usage_logs       # → companies, goals
012_create_approval_tokens      # → goals
013_create_invite_tokens        # → companies
014_enable_rls_all_tables       # Fix 17 — RLS + policies (runs last but one)
015_create_views                # reporting views
```

## Full Supabase setup order (what to run, in order)

These steps require live Supabase credentials in `.env`:

1. `python -m migrations.apply` — applies 001–015 (tables, RLS, views).
2. `supabase functions deploy custom-claims --no-verify-jwt` — JWT claims hook.
3. Apply `../supabase/storage.sql` — the `gst-invoices` private bucket + policy.
4. Apply `../supabase/indexes.sql` — performance indexes.
5. Register the Edge Function as the **Custom Access Token Hook** in the Supabase
   dashboard (Auth → Hooks), events: Login + Token Refresh.

See `../supabase/README.md` for details.
