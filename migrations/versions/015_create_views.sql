-- 015_create_views.sql
-- Reporting views (runs last). Per bootstrap.md: "Monthly cost view, vendor
-- stats view". We create three.
--
-- SECURITY MODEL (important): a plain Postgres view runs as its OWNER, which
-- bypasses RLS on the underlying tables. For per-company views that would leak
-- other companies' data to an authenticated user. So the two per-company views
-- are declared WITH (security_invoker = true) (PG15+, which Supabase runs) so
-- RLS is evaluated as the QUERYING user — a tenant sees only its own rows, the
-- service_role (BYPASSRLS) sees all. vendor_stats is deliberately left
-- owner-invoked because it is the cross-company intelligence aggregate (the
-- moat); it exposes only aggregates, never another company's raw rows.

-- ── LLM cost per company per month (PRD Section 26) — per-tenant ─────────────
CREATE VIEW monthly_llm_cost_by_company
WITH (security_invoker = true) AS
SELECT
    company_id,
    DATE_TRUNC('month', created_at)            AS month,
    provider,
    SUM(input_tokens)                          AS total_input_tokens,
    SUM(output_tokens)                         AS total_output_tokens,
    COUNT(*)                                    AS total_calls,
    SUM(CASE WHEN fallback_used THEN 1 ELSE 0 END) AS fallback_calls
FROM llm_usage_logs
WHERE success = true
GROUP BY company_id, month, provider;

-- ── Procurement spend per company per month per category — per-tenant ───────
CREATE VIEW monthly_spend_by_company
WITH (security_invoker = true) AS
SELECT
    g.company_id,
    DATE_TRUNC('month', o.created_at)          AS month,
    g.category,
    COUNT(*)                                   AS order_count,
    SUM(COALESCE(o.final_price, o.quoted_price)) AS total_spend
FROM orders o
JOIN goals g ON g.id = o.goal_id
WHERE o.status <> 'failed'
GROUP BY g.company_id, month, g.category;

-- ── Per-vendor performance stats — CROSS-COMPANY (owner-invoked on purpose) ──
CREATE VIEW vendor_stats AS
SELECT
    v.id                                       AS vendor_id,
    v.name,
    v.category,
    v.city,
    v.composite_score,
    v.score_band,
    v.total_orders,
    COUNT(r.id)                                AS rating_count,
    ROUND(AVG(r.overall_rating)::numeric, 2)   AS avg_rating,
    ROUND(AVG(CASE WHEN r.delivered_on_time THEN 1.0 ELSE 0.0 END), 2) AS on_time_rate,
    ROUND(AVG(CASE WHEN r.price_accurate   THEN 1.0 ELSE 0.0 END), 2) AS price_accuracy_rate
FROM vendors v
LEFT JOIN vendor_ratings r ON r.vendor_id = v.id
GROUP BY v.id;

-- Views inherit no grants from migration 014's "ALL TABLES" (they didn't exist
-- yet), so grant read access explicitly.
GRANT SELECT ON monthly_llm_cost_by_company, monthly_spend_by_company, vendor_stats
    TO authenticated, service_role;
