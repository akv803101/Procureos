-- 011_create_llm_usage_logs.sql
-- Per-call LLM usage + cost tracking, written by llm_router._log_usage().
-- Schema source: PRD Section 26 (llm_usage_tracking.sql), with NOT NULL added
-- on the always-present columns.
-- The monthly cost view that aggregates this table is created in migration 015.

CREATE TABLE llm_usage_logs (
    id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company_id    UUID REFERENCES companies(id) ON DELETE CASCADE,
    goal_id       UUID REFERENCES goals(id) ON DELETE SET NULL,
    task          TEXT NOT NULL,                 -- LLMTask value, e.g. 'quote_parsing'
    provider      TEXT NOT NULL,                 -- anthropic | openai | gemini | groq
    model         TEXT NOT NULL,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    latency_ms    INTEGER,
    success       BOOLEAN NOT NULL,
    fallback_used BOOLEAN NOT NULL DEFAULT false,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
