-- 005_create_goals.sql
-- A goal is one procurement request, tracked through the state machine.
-- Schema source: PRD v1.7 Data Model + the Redis state machine section.
--
-- STATE ENUM RECONCILIATION (decision): the spec names goal states across
-- several sections that don't fully agree. We take the dedicated Redis
-- state-machine lifecycle as the spine and add the stray states referenced by
-- the API contract / escalation / governance sections. The CHECK below is the
-- full canonical set; the AUTHORITATIVE allowed *transitions* live in code
-- (core/state_machine.py — Fix 05), not in this CHECK. The CHECK is only a
-- backstop against a typo'd state ever being persisted.
--   Happy path : processing -> pending_rfq -> quotes_received -> pending_approval
--                -> approved -> payment_queued -> ordered -> in_transit
--                -> delivered -> rated
--   Exceptions : rfq_timeout, approval_expired, payment_failed, delivery_failed,
--                operator_escalated, governance_hold
--   Terminal   : completed, cancelled, failed

CREATE TABLE goals (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id       UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    employee_id      UUID REFERENCES employees(id) ON DELETE SET NULL,

    raw_input        TEXT NOT NULL,
    -- {category, quantity, location, destination, budget_hint, urgency, gst_required, ...}
    parsed_intent    JSONB NOT NULL DEFAULT '{}'::jsonb,
    category         TEXT CHECK (category IN
                         ('flights', 'hotel', 'fb', 'water', 'stationery', 'it_hardware', 'generic')),

    status           TEXT NOT NULL DEFAULT 'processing' CHECK (status IN (
                         'processing', 'pending_rfq', 'quotes_received', 'pending_approval',
                         'approved', 'payment_queued', 'ordered', 'in_transit',
                         'delivered', 'rated',
                         'rfq_timeout', 'approval_expired', 'payment_failed', 'delivery_failed',
                         'operator_escalated', 'governance_hold',
                         'completed', 'cancelled', 'failed')),

    budget_limit     NUMERIC(12,2),

    -- Ranked options shown to the approver (snapshot at sign-off time).
    options          JSONB,

    -- Approval routing + timing (used by escalation ladder and Fix 04 TTL).
    -- approver_id / company_admin_id are soft references to a member/employee:
    -- no FK is declared because the spec's escalation code treats them as
    -- denormalized recipient ids; the FK target (employees vs company_members)
    -- is settled when auth/roles land in Phase 3.
    approver_id      UUID,
    approver_email   TEXT,
    company_admin_id UUID,
    approval_sent_at TIMESTAMPTZ,
    approved_at      TIMESTAMPTZ,
    rejection_reason TEXT,

    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
