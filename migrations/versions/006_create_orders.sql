-- 006_create_orders.sql
-- One order = the executed transaction for a goal against a chosen vendor.
-- Schema source: PRD v1.7 Data Model (authoritative).
-- Note: orders.status is a SHORT lifecycle (placed|in_transit|delivered|failed),
-- distinct from goals.status (the full state machine). Keep them separate.

CREATE TABLE orders (
    id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    goal_id          UUID NOT NULL REFERENCES goals(id) ON DELETE CASCADE,
    vendor_id        UUID NOT NULL REFERENCES vendors(id),
    company_id       UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,

    quoted_price     NUMERIC(12,2),                  -- price the vendor quoted (pre-GST basis)
    final_price      NUMERIC(12,2),                  -- actual settled amount from the invoice
    gst_invoice_url  TEXT,                           -- Supabase storage path to the GST invoice PDF

    volopay_card_id  TEXT,                           -- virtual card issued for this order (Fix 03)
    payment_ref      TEXT,                           -- Volopay transaction id

    promised_eta     TIMESTAMPTZ,                    -- vendor's promised delivery time
    delivered_at     TIMESTAMPTZ,                    -- actual delivery (drives delivered_on_time signal)

    status           TEXT NOT NULL DEFAULT 'placed'
                         CHECK (status IN ('placed', 'in_transit', 'delivered', 'failed')),
    rating_sent      BOOLEAN NOT NULL DEFAULT false,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
