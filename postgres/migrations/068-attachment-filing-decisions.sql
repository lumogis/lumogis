-- SPDX-License-Identifier: AGPL-3.0-only
-- Copyright (C) 2026 Lumogis
--
-- LUM-727 — attachment filing becomes a PER-ATTACHMENT decision.
--
-- LUM-702 shipped an opt-in per mailbox: enable it and every readable attachment
-- was filed automatically, with undo as the safety net. That assumes filing
-- everything is acceptable BECAUSE undo exists, which only holds when most
-- attachments are worth keeping. Owner feedback: the share not worth filing is
-- large enough that a global rule does not work at all.
--
-- Additive only. No data migration: `attachment_ingest_enabled` keeps its column
-- and changes MEANING, from "file everything from this mailbox" to "show me
-- candidates from this mailbox". That change is safe in this direction only —
-- strictly LESS happens without the user, so an existing `true` cannot cause a
-- surprise filing. The reverse would have required a rename.
--
-- Rollback (manual DDL; the runner is apply-only / filename-tracked):
--   ALTER TABLE email_attachment_ingest_log DROP COLUMN filed_by_rule_id;
--   DROP TABLE IF EXISTS email_attachment_rule;

COMMENT ON COLUMN sources.attachment_ingest_enabled IS
    'LUM-727: propose this mailbox''s attachments for review. Was LUM-702''s '
    '"file everything automatically"; nothing is filed now without a per-file '
    'accept or a matching email_attachment_rule.';

-- ---------------------------------------------------------------------------
-- What the user has told us to always file, without being asked again.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS email_attachment_rule (
    id          UUID        PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     TEXT        NOT NULL DEFAULT 'default',
    source_id   UUID        NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    -- 'sender'   -> match_value is a lowercased, trimmed from_addr
    -- 'category' -> match_value is a taxonomy SLUG (email_taxonomy), never a
    --               label: labels are user-editable (LUM-629) and renaming one
    --               must not silently detach every rule that used it.
    match_type  TEXT        NOT NULL,
    match_value TEXT        NOT NULL,
    action      TEXT        NOT NULL DEFAULT 'file',
    -- No `active` column, deliberately diverging from email_sender_rule.
    -- Deleting a rule is a HARD delete there (services/email_sender_rules.py),
    -- which leaves its own `active` flag permanently TRUE — vestigial, and a
    -- column that is always TRUE is one a later reader will eventually treat as
    -- meaningful. Hard delete here too, and no flag.
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, source_id, match_type, match_value),
    CONSTRAINT email_attachment_rule_match_ck CHECK (match_type IN ('sender','category')),
    CONSTRAINT email_attachment_rule_action_ck CHECK (action IN ('file'))
);

CREATE INDEX IF NOT EXISTS idx_email_attachment_rule_lookup
    ON email_attachment_rule (user_id, source_id);

COMMENT ON TABLE email_attachment_rule IS
    'LUM-727: per-sender / per-category "always file this" rules. Deliberately '
    'SEPARATE from email_sender_rule: that table answers "what to do with the '
    'MESSAGE" (v1: hide), this one answers "what to do with its ATTACHMENTS". '
    'One table would make the hide+file contradiction LIVE — two rows, both '
    'consulted, no defined precedence. Split, hide simply wins (a suppressed '
    'message never gets a signals row, so it never produces an attachment row '
    'for the file rule to match) and the file rule is inert.';

-- ---------------------------------------------------------------------------
-- Which rule filed a document, so the review surface can say so and offer to
-- stop. NULL for anything the user picked by hand.
--
-- ON DELETE SET NULL, and the alternative is not merely worse — it is the
-- orphaned-document hole LUM-702 closed, in a new place. Under CASCADE,
-- deleting a rule would delete the LEDGER ROWS of everything it filed while the
-- Qdrant chunks stay. Undo works off the ledger, so those documents would be
-- searchable with nothing able to remove them. Deleting a rule must stop future
-- filing and change nothing already filed.
-- ---------------------------------------------------------------------------
ALTER TABLE email_attachment_ingest_log
    ADD COLUMN IF NOT EXISTS filed_by_rule_id UUID
        REFERENCES email_attachment_rule(id) ON DELETE SET NULL;

COMMENT ON COLUMN email_attachment_ingest_log.filed_by_rule_id IS
    'LUM-727: the rule that filed this without being asked, or NULL when the '
    'user picked it by hand. Lets the review surface say which rule acted and '
    'offer to stop — a rule matching more than the user expected is the failure '
    'mode, and it is only ever noticed by seeing it happen.';

-- `skipped_reason` gains 'declined'. The column has no CHECK (067), so no DDL
-- is needed — but the value is new and every consumer that enumerates reasons
-- must learn it, including the web SkipReason union.
