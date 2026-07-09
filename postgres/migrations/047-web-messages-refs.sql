-- Migration 047: citation refs + action proposal FK on web_messages (LUM-395).
-- SPDX-License-Identifier: AGPL-3.0-only
--
-- Additive columns on LUM-162 web_messages for persisted citation blobs
-- (source_refs) and nullable link to action_proposals (LUM-123).
--
-- Manual downgrade (operator-only):
--   ALTER TABLE web_messages
--     DROP COLUMN IF EXISTS action_proposal_id,
--     DROP COLUMN IF EXISTS source_refs;
--
-- Idempotent.

BEGIN;

ALTER TABLE web_messages
  ADD COLUMN IF NOT EXISTS source_refs JSONB NULL,
  ADD COLUMN IF NOT EXISTS action_proposal_id BIGINT NULL
    REFERENCES action_proposals(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS idx_web_messages_action_proposal_id
  ON web_messages (action_proposal_id)
  WHERE action_proposal_id IS NOT NULL;

COMMIT;
