-- 007_create_vendor_ratings.sql
-- The CROWN JEWEL: every rated order feeds the cross-company vendor score.
-- Schema source: PRD v1.7 Data Model (authoritative).
-- overall_rating is 1-5, but in V1 the employee tap maps to 5 (satisfied) or
-- 2 (issue) only — the full 1-5 scale is reserved for later. The three boolean
-- signals are SYSTEM-calculated, not user-entered:
--   delivered_on_time = delivered_at <= promised_eta
--   price_accurate    = final_price <= quoted_price
--   is_repeat_order   = this company has ordered from this vendor before

CREATE TABLE vendor_ratings (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vendor_id         UUID NOT NULL REFERENCES vendors(id),
    order_id          UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    company_id        UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,

    overall_rating    SMALLINT CHECK (overall_rating BETWEEN 1 AND 5),
    satisfied         BOOLEAN,                 -- true = 👍, false = 👎
    delivered_on_time BOOLEAN,                 -- system-calculated
    price_accurate    BOOLEAN,                 -- system-calculated
    response_time_mins INTEGER,               -- vendor RFQ response latency
    comment           TEXT,
    is_repeat_order   BOOLEAN NOT NULL DEFAULT false,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
