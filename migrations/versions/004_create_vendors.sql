-- 004_create_vendors.sql
-- Vendors are SHARED across all companies (the cross-company intelligence graph).
-- Independent table — no company_id. RLS (014) makes it read-all / write-service.
-- Schema source: PRD v1.7 Data Model (authoritative). Per the resolved schema
-- conflict, PRD wins over the architecture doc: split contact columns (not a
-- contact_info JSONB), source enum google_places|skyscanner|agent_found, and
-- score_band includes 'flagged'.
--
-- Fix 08 (binding): google_place_id is the vendor identity / dedup key — NEVER
-- the phone number (a phone can be a vendor for one company and an employee for
-- another). UNIQUE enforces dedup at the DB level.

CREATE TABLE vendors (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name            TEXT NOT NULL,
    category        TEXT NOT NULL,
    sub_category    TEXT,

    phone           TEXT,           -- primary WhatsApp number (90% of Indian SMBs)
    email           TEXT,           -- scraped from website where available
    website         TEXT,
    gst_number      TEXT,
    gst_verified    BOOLEAN NOT NULL DEFAULT false,
    gst_verified_at TIMESTAMPTZ,    -- last successful GSTIN verification (Fix 14)

    google_place_id TEXT UNIQUE,    -- Fix 08: identity + dedup key
    google_rating   NUMERIC(2,1),
    review_count    INTEGER,
    source          TEXT CHECK (source IN ('google_places', 'skyscanner', 'agent_found')),
    city            TEXT,
    state           TEXT,

    -- composite_score is NULL until a vendor has >= 3 rated orders (unproven).
    composite_score NUMERIC(5,2) DEFAULT NULL,
    score_band      TEXT NOT NULL DEFAULT 'unproven'
                        CHECK (score_band IN ('unproven', 'provisional', 'reliable', 'preferred', 'flagged')),
    total_orders    INTEGER NOT NULL DEFAULT 0,

    opted_out       BOOLEAN NOT NULL DEFAULT false,   -- vendor sent STOP/OPTOUT (Fix 06)
    opted_out_at    TIMESTAMPTZ,
    flag_reason     TEXT,                              -- why a vendor was flagged for review

    score_updated_at TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);
