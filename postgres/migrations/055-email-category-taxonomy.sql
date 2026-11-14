-- Migration 055: user-editable email category taxonomy + classification rules (LUM-629).
--
-- Sparse per-user overlay over the built-in DEFAULT_SEED taxonomy: absence of a
-- row for a slug means "use the default" (no seed-on-first-read, no is_default
-- column). is_custom / is_protected are DERIVED (slug ∉ DEFAULT_SEED ⇒ custom;
-- slug ∈ PROTECTED_SLUGS ⇒ protected), never stored, so a future DEFAULT_SEED
-- change can't leave a stale flag. Mirrors notification_preferences (031).
--
-- No backfill: an untouched user has zero rows and classifies exactly as under
-- LUM-170's fixed six-category tuple. Idempotent / re-runnable.

BEGIN;

CREATE TABLE IF NOT EXISTS email_category_taxonomy (
    user_id     TEXT    NOT NULL DEFAULT 'default',
    slug        TEXT    NOT NULL,
    label       TEXT    NOT NULL,
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    sort        INTEGER NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, slug)
);
CREATE INDEX IF NOT EXISTS ix_email_category_taxonomy_user
    ON email_category_taxonomy (user_id);

CREATE TABLE IF NOT EXISTS email_classification_rule (
    user_id     TEXT    NOT NULL DEFAULT 'default',
    rule_id     TEXT    NOT NULL,
    priority    INTEGER NOT NULL DEFAULT 100,
    match       JSONB   NOT NULL DEFAULT '{}'::jsonb,
    category    TEXT    NOT NULL,
    enabled     BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, rule_id)
);
CREATE INDEX IF NOT EXISTS ix_email_classification_rule_user
    ON email_classification_rule (user_id, priority);

COMMIT;

-- Rollback: DROP TABLE email_classification_rule, email_category_taxonomy;
