-- 001_create_companies.sql
-- Root tenant table. Everything else hangs off company_id for RLS isolation.
-- Schema source: PRD v1.7 Data Model (authoritative). The onboarding columns
-- (PRD Section 22 ALTERs) are folded in here at create time — this is a fresh
-- build, so there is no reason to add them via a later ALTER migration.

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- provides gen_random_uuid()

CREATE TABLE companies (
    id                       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name                     TEXT NOT NULL,
    gst_number               TEXT UNIQUE,
    employee_count           INTEGER,
    plan                     TEXT NOT NULL DEFAULT 'starter'
                                 CHECK (plan IN ('starter', 'growth', 'enterprise')),

    -- Per-category budget caps, e.g. {"flights": 15000, "it_hardware": 50000, "default": 5000}
    budget_policies          JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Approval thresholds, e.g. {"self_approve_limit": 2000, "finance_approval": 10000}
    approval_chain           JSONB NOT NULL DEFAULT '{}'::jsonb,

    accounting_tool          TEXT CHECK (accounting_tool IN ('zoho', 'tally', 'quickbooks')),
    slack_webhook_url        TEXT,
    approver_email           TEXT,
    waba_number              TEXT,

    -- Onboarding state machine (PRD Section 22). Default = first step.
    onboarding_step          TEXT DEFAULT 'company_profile',
    onboarding_completed_at  TIMESTAMPTZ,
    slack_connected          BOOLEAN NOT NULL DEFAULT false,
    accounting_connected     BOOLEAN NOT NULL DEFAULT false,

    created_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);
