-- Migration 054: multi-instance connector credentials (LUM-170).
--
-- Allow more than one credential row per (user_id, connector), keyed by an
-- `instance` discriminator. This is what lets a user configure several IMAP
-- mailboxes (personal / work / shared), each keyed by instance = sources.id.
--
-- The `instance = ''` default keeps every existing single-instance connector
-- (caldav / paperless / ntfy / llm_*) byte-for-byte unchanged: an old row
-- becomes (user_id, connector, '') under the new 3-column primary key, which is
-- identical to the old (user_id, connector) key. No data change.
--
-- Idempotent-ish: the ADD COLUMN is guarded; the PK swap is safe to re-run only
-- if the new PK is not already present (Postgres names it
-- `user_connector_credentials_pkey` in both shapes, so a re-run would error on
-- the DROP if already applied — run once, in order, like every other migration).

BEGIN;

ALTER TABLE user_connector_credentials
  ADD COLUMN IF NOT EXISTS instance TEXT NOT NULL DEFAULT '';

ALTER TABLE user_connector_credentials
  DROP CONSTRAINT user_connector_credentials_pkey;

ALTER TABLE user_connector_credentials
  ADD PRIMARY KEY (user_id, connector, instance);

-- Match the application-layer instance charset (services.connector_credentials
-- ._INSTANCE_FORMAT) as a defence-in-depth safety net.
ALTER TABLE user_connector_credentials
  ADD CONSTRAINT user_connector_credentials_instance_charset
  CHECK (instance ~ '^[a-z0-9_-]{0,64}$');

COMMENT ON COLUMN user_connector_credentials.instance IS
  'LUM-170 multi-instance discriminator. Empty for single-instance connectors; '
  'for imap it is the mailbox sources.id. Part of the primary key.';

COMMIT;
