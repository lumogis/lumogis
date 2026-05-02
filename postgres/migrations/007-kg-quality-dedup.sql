-- Migration 007: KG Quality Pipeline Pass 4a — manual dedup tables
-- known_distinct_entity_pairs: operator-confirmed non-matches (suppresses future review_queue entries)
-- review_decisions: audit trail for all operator actions on the review queue

CREATE TABLE IF NOT EXISTS known_distinct_entity_pairs (
    user_id         TEXT NOT NULL DEFAULT 'default',
    entity_id_a     UUID NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
    entity_id_b     UUID NOT NULL REFERENCES entities(entity_id) ON DELETE CASCADE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, entity_id_a, entity_id_b),
    CHECK (entity_id_a < entity_id_b)
);

CREATE TABLE IF NOT EXISTS review_decisions (
    decision_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         TEXT NOT NULL DEFAULT 'default',
    item_type       TEXT NOT NULL,
    item_id         TEXT NOT NULL,
    action          TEXT NOT NULL,
    payload         JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_review_decisions_user_time
    ON review_decisions (user_id, created_at DESC);
