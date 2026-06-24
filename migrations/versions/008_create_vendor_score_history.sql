-- 008_create_vendor_score_history.sql
-- Audit trail of vendor composite scores over time. Written by score_updater
-- after each recalculation; lets us explain why a vendor's band changed.
-- Schema source: architecture doc vendor_score_history (consistent with PRD).

CREATE TABLE vendor_score_history (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vendor_id       UUID NOT NULL REFERENCES vendors(id) ON DELETE CASCADE,
    composite_score NUMERIC(5,2),
    -- snapshot of each of the 5 normalized signals at recalculation time
    components      JSONB,
    order_count     INTEGER,
    recorded_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
