# Supabase setup

Run **after** the database migrations (`python -m migrations.apply`). All steps
need live Supabase credentials in the project `.env`. Source: `supabase.md`.

## 1. Link the project (one time)
```bash
npm install -g supabase
supabase login
supabase link --project-ref YOUR_PROJECT_REF
```

## 2. Deploy the JWT-claims Edge Function
```bash
supabase functions deploy custom-claims --no-verify-jwt
```
Then register it in the dashboard: **Auth → Hooks → Custom Access Token Hook**
→ Function: `custom-claims` → Events: Login, Token Refresh.

Without this, the JWT carries no `company_id`/`role`/`spend_limit` claims and
every RLS policy (migration 014) returns zero rows.

## 3. Apply storage + indexes
```bash
psql "$SUPABASE_POSTGRES_URL" -f supabase/storage.sql
psql "$SUPABASE_POSTGRES_URL" -f supabase/indexes.sql
```

## 4. Verify
```sql
-- RLS enabled everywhere?
SELECT tablename, rowsecurity FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename;
-- Storage bucket exists?
SELECT * FROM storage.buckets WHERE id = 'gst-invoices';
```

## Files
| File | Purpose |
|---|---|
| `functions/custom-claims/index.ts` | Injects `company_id`, `role`, `spend_limit` into the JWT |
| `storage.sql` | Private `gst-invoices` bucket + per-company access policy |
| `indexes.sql` | Performance indexes (RLS + table DDL live in `migrations/`) |

RLS policies themselves are in `migrations/versions/014_enable_rls_all_tables.sql`
(they run as part of the migration sequence, not here).
