-- 002_create_auth_members.sql
-- company_members: links Supabase auth.users to a company with a role.
-- Schema source: supabase.md (multi-user-schema.sql) — used verbatim.
-- The Edge Function (custom-claims) reads this table to build JWT claims, so
-- the column names here MUST match supabase/functions/custom-claims/index.ts:
--   company_id, role, spend_limit, is_active, created_at.
-- auth.users is auto-managed by Supabase — never create it manually.

CREATE TABLE company_members (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID REFERENCES auth.users(id) ON DELETE CASCADE,
    company_id    UUID REFERENCES companies(id) ON DELETE CASCADE,
    role          TEXT NOT NULL CHECK (role IN ('admin', 'approver', 'finance', 'employee')),
    slack_user_id TEXT,
    whatsapp_phone TEXT,
    spend_limit   NUMERIC(12,2) DEFAULT 2000,     -- self-approve threshold (INR)
    department    TEXT,
    is_active     BOOLEAN DEFAULT true,
    invited_by    UUID REFERENCES auth.users(id),
    invited_at    TIMESTAMPTZ DEFAULT now(),
    accepted_at   TIMESTAMPTZ,                     -- NULL = invite still pending
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id, company_id)
);
