-- 012_create_approval_tokens.sql
-- Magic-link approval tokens (Fix 12): one-click approve/reject for approvers
-- with no login. 4-hour TTL, one-time use. RLS (014) restricts to service_role.
-- Schema source: PRD Section 23 (magic link) + Fix 12.

CREATE TABLE approval_tokens (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token       TEXT NOT NULL UNIQUE,                       -- secrets.token_urlsafe(32)
    goal_id     UUID NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    approver_id UUID,
    expires_at  TIMESTAMPTZ NOT NULL,                       -- created_at + 4 hours (Fix 12)
    used_at     TIMESTAMPTZ,                                -- non-null once consumed (one-time use)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_approval_tokens_token ON approval_tokens(token);
