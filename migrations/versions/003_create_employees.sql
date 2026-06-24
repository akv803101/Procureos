-- 003_create_employees.sql
-- Operational employee records (goal submitters, approvers as seen by the agent).
-- Distinct from company_members (auth identities): an employee row carries the
-- WhatsApp number used for RFQ/rating routing and per-person spend limits.
-- Schema source: PRD v1.7 Data Model (authoritative).

CREATE TABLE employees (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id  UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    whatsapp    TEXT,                                   -- routing key for ratings (waba_router, Fix 06)
    role        TEXT CHECK (role IN ('employee', 'manager', 'finance', 'admin')),
    department  TEXT,
    spend_limit NUMERIC(12,2) DEFAULT 2000,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
