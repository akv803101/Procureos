-- 010_create_notification_logs.sql
-- Every notification send is logged here by notification_dispatcher.notify().
-- Schema source: PRD Section 20 (notification_logs DDL), with added CHECK
-- constraints on channel/status and NOT NULL on sent_at for integrity.
-- Read-only after insert (no updates/deletes by convention).
-- recipient_id may be an employee_id or a vendor_id (not FK-constrained for that
-- reason — it is polymorphic across employees and vendors).

CREATE TABLE notification_logs (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event        TEXT NOT NULL,
    channel      TEXT NOT NULL CHECK (channel IN ('slack', 'email', 'whatsapp', 'call')),
    recipient_id UUID NOT NULL,
    goal_id      UUID REFERENCES goals(id) ON DELETE SET NULL,
    status       TEXT NOT NULL CHECK (status IN ('sent', 'failed', 'delivered', 'read')),
    error        TEXT,
    sent_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Indexes specified alongside the table in PRD Section 20.
CREATE INDEX idx_notif_goal      ON notification_logs(goal_id);
CREATE INDEX idx_notif_recipient ON notification_logs(recipient_id, sent_at);
