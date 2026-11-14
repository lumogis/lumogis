-- SPDX-License-Identifier: AGPL-3.0-only
-- Copyright (C) 2026 Lumogis
--
-- LUM-834 chunk B — a connector you can turn off, and a card that can say
-- which fields are set without decrypting anything.
--
-- Rollback: ALTER TABLE user_connector_credentials
--             DROP COLUMN IF EXISTS enabled, DROP COLUMN IF EXISTS configured_keys;
-- No explicit BEGIN/COMMIT — the runner wraps each file in a transaction.
--
-- WHY `enabled` IS A NEW COLUMN AND NOT `delivery_paused`. That flag is
-- SYSTEM-set on delivery failure and cleared on recovery, and it is read at
-- exactly two notification-only sites. `enabled` is USER-set and gates ingest
-- and actions too. One column with a reason discriminator would let a system
-- auto-pause overwrite a user's deliberate off, and let recovery silently
-- switch a connector back on. Effective state is `enabled AND NOT
-- delivery_paused`.
--
-- WHY `DEFAULT TRUE` IS THE ONLY SAFE DEFAULT. Every existing credential is in
-- use right now. `DEFAULT FALSE` would silently disable every connector in
-- every household on upgrade — ingest stops, actions stop, and the symptom is
-- "nothing works" with no user action to explain it.

ALTER TABLE user_connector_credentials
  ADD COLUMN IF NOT EXISTS enabled BOOLEAN NOT NULL DEFAULT TRUE;

-- WHY `configured_keys` IS NULLABLE AND NULL IS A REAL STATE.
--
-- The card needs to say which fields a credential has without decrypting it —
-- ADR-199 removed decrypt-on-enumerate from the mailbox-host path and this
-- surface must not reintroduce it. So the key NAMES are written at PUT time,
-- from the payload being encrypted, in the same statement.
--
-- That leaves rows written before this migration. They cannot be populated
-- truthfully without decrypting every credential — the exact thing this design
-- exists to avoid. A `NOT NULL DEFAULT '{}'` would therefore make every
-- pre-migration credential report "no fields configured", i.e. render a working
-- connector as unconfigured: ADR-199's named failure mode arriving through a
-- migration default instead of through a decrypt failure.
--
-- So NULL means UNKNOWN, and it self-heals on the next PUT. The triad follows
-- `014-mcp-tokens.sql` D3 (`scopes TEXT[] NULL`), which makes the same
-- distinction and pins it with an in-DB COMMENT for the same reason.
ALTER TABLE user_connector_credentials
  ADD COLUMN IF NOT EXISTS configured_keys TEXT[];

COMMENT ON COLUMN user_connector_credentials.configured_keys IS
  'Payload key NAMES from the last PUT — all of them, including the names of '
  'secret-valued keys; never any value. NULL = unknown (row predates the '
  'column); empty = impossible for a real credential; non-empty = the keys. '
  'Never authoritative — display only. See LUM-834 chunk B.';
