-- 014_enable_rls_all_tables.sql
-- Fix 17 (binding): Row Level Security on every table, enforcing company
-- isolation via the JWT 'company_id' claim that the custom-claims Edge Function
-- injects. Schema source: supabase.md Step 3 — reproduced and extended to cover
-- invite_tokens, plus explicit GRANTs (see note) and append-only REVOKEs.
--
-- NOTE on GRANTs: supabase.md omits GRANTs because hosted Supabase pre-grants
-- public-schema privileges to the anon/authenticated/service_role roles. We add
-- them explicitly so (a) the append-only REVOKEs below are actually meaningful,
-- and (b) the schema is self-describing about who can do what. They are
-- idempotent/harmless on hosted Supabase.
--
-- The service_role key has BYPASSRLS, so backend operations bypass these
-- policies; the policies govern the 'authenticated' role (end users via RLS).

-- ── ENABLE RLS ──────────────────────────────────────────────────────────────
ALTER TABLE companies            ENABLE ROW LEVEL SECURITY;
ALTER TABLE company_members      ENABLE ROW LEVEL SECURITY;
ALTER TABLE employees            ENABLE ROW LEVEL SECURITY;
ALTER TABLE goals                ENABLE ROW LEVEL SECURITY;
ALTER TABLE orders               ENABLE ROW LEVEL SECURITY;
ALTER TABLE vendor_ratings       ENABLE ROW LEVEL SECURITY;
ALTER TABLE operator_logs        ENABLE ROW LEVEL SECURITY;
ALTER TABLE notification_logs    ENABLE ROW LEVEL SECURITY;
ALTER TABLE llm_usage_logs       ENABLE ROW LEVEL SECURITY;
ALTER TABLE approval_tokens      ENABLE ROW LEVEL SECURITY;
ALTER TABLE invite_tokens        ENABLE ROW LEVEL SECURITY;
ALTER TABLE vendors              ENABLE ROW LEVEL SECURITY;
ALTER TABLE vendor_score_history ENABLE ROW LEVEL SECURITY;

-- ── HELPERS: read claims out of the JWT ─────────────────────────────────────
CREATE OR REPLACE FUNCTION current_company_id()
RETURNS UUID AS $$
  SELECT NULLIF(
    current_setting('request.jwt.claims', true)::json->>'company_id',
    ''
  )::UUID;
$$ LANGUAGE sql STABLE;

CREATE OR REPLACE FUNCTION current_user_role()
RETURNS TEXT AS $$
  SELECT current_setting('request.jwt.claims', true)::json->>'role';
$$ LANGUAGE sql STABLE;

-- ── BASE GRANTS (explicit; see NOTE above) ──────────────────────────────────
GRANT USAGE ON SCHEMA public TO authenticated, service_role;
-- Authenticated users reach tables through RLS — row visibility is still
-- governed by the policies below; these grants only open the table-level gate.
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO authenticated;
GRANT ALL ON ALL TABLES IN SCHEMA public TO service_role;

-- ── COMPANY-SCOPED TABLES: own company only ─────────────────────────────────
CREATE POLICY "companies_own_only"       ON companies      FOR ALL USING (id = current_company_id());
CREATE POLICY "members_own_company"      ON company_members FOR ALL USING (company_id = current_company_id());
CREATE POLICY "employees_own_company"    ON employees      FOR ALL USING (company_id = current_company_id());
CREATE POLICY "goals_company_isolation"  ON goals          FOR ALL USING (company_id = current_company_id());
CREATE POLICY "orders_company_isolation" ON orders         FOR ALL USING (company_id = current_company_id());
CREATE POLICY "ratings_own_company"      ON vendor_ratings FOR ALL USING (company_id = current_company_id());
CREATE POLICY "llm_logs_own_company"     ON llm_usage_logs FOR ALL USING (company_id = current_company_id());

-- ── VENDORS: shared read intelligence, service-only writes ──────────────────
CREATE POLICY "vendors_read_authenticated"  ON vendors FOR SELECT USING (auth.role() = 'authenticated');
CREATE POLICY "vendors_write_service_only"  ON vendors FOR INSERT WITH CHECK (auth.role() = 'service_role');
CREATE POLICY "vendors_update_service_only" ON vendors FOR UPDATE USING (auth.role() = 'service_role');

CREATE POLICY "score_history_read"          ON vendor_score_history FOR SELECT USING (auth.role() = 'authenticated');
CREATE POLICY "score_history_write_service" ON vendor_score_history FOR INSERT WITH CHECK (auth.role() = 'service_role');

-- ── OPERATOR_LOGS: append-only, service-only (Fix 18) ───────────────────────
CREATE POLICY "operator_logs_insert_service" ON operator_logs FOR INSERT WITH CHECK (auth.role() = 'service_role');
CREATE POLICY "operator_logs_read_service"   ON operator_logs FOR SELECT USING (auth.role() = 'service_role');

-- ── SECURITY-CRITICAL TOKEN TABLES: service_role only ───────────────────────
CREATE POLICY "approval_tokens_service_only" ON approval_tokens FOR ALL USING (auth.role() = 'service_role');
CREATE POLICY "invite_tokens_service_only"   ON invite_tokens   FOR ALL USING (auth.role() = 'service_role');

-- notification_logs: no authenticated policy → only service_role (BYPASSRLS)
-- can read/write. Notifications are produced and inspected by the backend.

-- ── APPEND-ONLY ENFORCEMENT (after grants, so REVOKE actually bites) ─────────
-- operator_logs is an immutable audit trail (Fix 18): no UPDATE/DELETE for
-- anyone, including service_role.
REVOKE UPDATE, DELETE ON operator_logs FROM authenticated;
REVOKE UPDATE, DELETE ON operator_logs FROM service_role;
-- vendor_score_history is also an audit trail — once a score snapshot is
-- written it must never be altered (defense-in-depth beyond the spec).
REVOKE UPDATE, DELETE ON vendor_score_history FROM authenticated;
REVOKE UPDATE, DELETE ON vendor_score_history FROM service_role;
