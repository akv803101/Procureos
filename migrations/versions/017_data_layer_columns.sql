-- 017_data_layer_columns.sql
-- Columns the data layer reads/writes that weren't in the original schema, plus
-- the spend ledger the Fix 02 budget check relies on.
--
-- spend_records: orders are created BEFORE the payment fires, so summing orders
-- for get_spent_this_period would count the in-flight order against its own
-- budget check. The ledger is the authoritative committed-spend source; a row is
-- inserted inside the Fix 02 distributed lock right after a payment settles.

ALTER TABLE goals  ADD COLUMN IF NOT EXISTS selected_option_id TEXT;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS vendor_response_time_mins INTEGER;
ALTER TABLE orders ADD COLUMN IF NOT EXISTS description TEXT;

CREATE TABLE IF NOT EXISTS spend_records (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id  UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    category    TEXT NOT NULL,
    amount      NUMERIC(12,2) NOT NULL,
    order_id    UUID REFERENCES orders(id) ON DELETE SET NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_spend_company_category_month
    ON spend_records (company_id, category, created_at);

-- Backend-only table (written under the Fix 02 lock). RLS on with no
-- authenticated policy => only the service role (BYPASSRLS) can touch it.
ALTER TABLE spend_records ENABLE ROW LEVEL SECURITY;
