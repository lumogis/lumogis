-- LUM-514: biography conflict audit table (standalone; biography_pins lands in LUM-515).
-- Idempotent: safe to re-run on environments that already applied 043.

CREATE TABLE IF NOT EXISTS biography_conflict_resolutions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    household_instance_id TEXT NOT NULL DEFAULT 'default',
    fact_group_key TEXT NOT NULL,
    category TEXT NOT NULL,
    domain TEXT,
    pin_ids UUID[] NOT NULL,
    detection_snapshot JSONB NOT NULL,
    requires_review BOOLEAN NOT NULL DEFAULT false,
    status TEXT NOT NULL DEFAULT 'open'
        CHECK (status IN ('open','resolved','dismissed')),
    resolution_action TEXT
        CHECK (resolution_action IS NULL OR resolution_action IN ('confirm_one','keep_both','dismiss')),
    chosen_pin_id UUID,
    archived_pin_ids UUID[] NOT NULL DEFAULT '{}',
    context_note TEXT,
    synthesis_revision_id UUID,
    resolved_by TEXT,
    resolved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_biography_conflicts_open_fact_group
    ON biography_conflict_resolutions (fact_group_key) WHERE status = 'open';

CREATE INDEX IF NOT EXISTS idx_biography_conflicts_status
    ON biography_conflict_resolutions (status) WHERE status = 'open';

CREATE INDEX IF NOT EXISTS idx_biography_conflicts_fact_group
    ON biography_conflict_resolutions (fact_group_key);
