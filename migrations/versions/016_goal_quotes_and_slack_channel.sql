-- 016_goal_quotes_and_slack_channel.sql
-- GoalProcessor capstone additions:
--   goals.collected_quotes  — parsed vendor replies accumulated before ranking
--   companies.slack_approval_channel — where the approval card is posted (bot postMessage)
-- Appended after the original 15 (migrations are append-only; the runner applies
-- any file not yet in schema_migrations).

ALTER TABLE goals     ADD COLUMN IF NOT EXISTS collected_quotes JSONB NOT NULL DEFAULT '[]'::jsonb;
ALTER TABLE companies ADD COLUMN IF NOT EXISTS slack_approval_channel TEXT;
