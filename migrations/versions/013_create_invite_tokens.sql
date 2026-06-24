-- 013_create_invite_tokens.sql
-- Team-invite tokens (PRD Section 23 — invite_team_member / accept_invite).
-- 7-day TTL; accept_invite returns HTTP 410 once expired. One-time use via
-- accepted_at. RLS keeps these service-role only.

CREATE TABLE invite_tokens (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    token       TEXT NOT NULL UNIQUE,                       -- secrets.token_urlsafe(32)
    company_id  UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    email       TEXT NOT NULL,
    role        TEXT NOT NULL CHECK (role IN ('admin', 'approver', 'finance', 'employee')),
    invited_by  UUID,
    expires_at  TIMESTAMPTZ NOT NULL,                       -- created_at + 7 days
    accepted_at TIMESTAMPTZ,                                -- non-null once consumed
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_invite_tokens_token ON invite_tokens(token);
