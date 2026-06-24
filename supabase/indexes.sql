-- supabase/indexes.sql
-- Performance indexes for common query patterns. Apply after migrations.
-- Schema source: supabase.md Step 5 — verbatim. (idx_notif_* live in migration
-- 010 alongside notification_logs; the rest are here.)

CREATE INDEX idx_goals_company_status      ON goals(company_id, status);
CREATE INDEX idx_goals_employee            ON goals(employee_id);
CREATE INDEX idx_orders_company_status     ON orders(company_id, status);
CREATE INDEX idx_vendor_ratings_vendor     ON vendor_ratings(vendor_id);
CREATE INDEX idx_vendor_ratings_company    ON vendor_ratings(company_id);
CREATE INDEX idx_vendors_category_city     ON vendors(category, city);
CREATE INDEX idx_vendors_score_band        ON vendors(score_band);
CREATE INDEX idx_vendors_place_id          ON vendors(google_place_id);   -- Fix 08
CREATE INDEX idx_company_members_user      ON company_members(user_id);
CREATE INDEX idx_company_members_company   ON company_members(company_id);
-- supabase.md also lists idx_notification_logs_goal(goal_id); skipped here
-- because migration 010 already creates idx_notif_goal on the same column.
CREATE INDEX idx_llm_usage_company         ON llm_usage_logs(company_id, created_at);
