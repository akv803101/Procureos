-- 009_create_operator_logs.sql
-- Append-only audit log of human-operator and exception actions (Fix 18).
-- INSERT only — UPDATE/DELETE are REVOKED for everyone in migration 014.
-- Schema source: PRD Section 19 (Fix 18) + API contract note "all actions
-- written to operator_logs (append-only)".

CREATE TABLE operator_logs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    action      TEXT NOT NULL,                              -- action type, e.g. 'quote_resolved', 'unrouted_message'
    goal_id     UUID REFERENCES goals(id) ON DELETE SET NULL,
    vendor_id   UUID REFERENCES vendors(id) ON DELETE SET NULL,
    note        TEXT,
    payload     JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
