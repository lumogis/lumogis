-- Migration 018: household + instance/system connector credential tiers.
--
-- Adds two operator/admin-owned credential tier tables that share the
-- same Fernet/MultiFernet crypto and `key_version` fingerprint scheme
-- as `user_connector_credentials` (migration 015). See ADR
-- `credential_scopes_shared_system.md` and plan
-- `.cursor/plans/credential_scopes_shared_system.plan.md` for the
-- full design contract.
--
-- Sequencing
-- ----------
-- 017-per-user-batch-jobs is the previous claimed slot. If a parallel
-- branch lands 018 first slide to the next free integer in the same
-- commit and update the plan + topic index. Nothing in the schema
-- FK-references the migration number. The Python migration runner
-- (orchestrator/db_migrations.py:_migration_files) reads the on-disk
-- ordering, so a slide is a one-line change there.
--
-- Idempotent and re-runnable: the CREATE statements all use IF NOT
-- EXISTS so a partial apply followed by a retry lands cleanly.
--
-- Design highlights
-- -----------------
-- * Same column shape as 015, MINUS `user_id`. Each row is identified
--   solely by `connector` (PK).
-- * Same crypto + key_version semantics as 015 -- the household
--   MultiFernet (LUMOGIS_CREDENTIAL_KEY[S]) seals all three tables.
-- * `created_by` / `updated_by` accept ONLY `'system'` or
--   `'admin:<actor_user_id>'`. The literal `'self'` is rejected
--   because no user owns these rows -- they are a household-shared
--   secret (household tier) or an operator-owned instance secret
--   (instance/system tier).
-- * Both tables are deliberately OMITTED from the per-user export
--   path (services/user_export.py:_OMITTED_NON_USER_TABLES). raw
--   pg_dump backups still carry ciphertext; restore requires the
--   matching LUMOGIS_CREDENTIAL_KEY[S] -- losing it makes
--   credentials unrecoverable.
-- * key_version uses the same stable 32-bit unsigned fingerprint
--   scheme as 015 (D6c) and is stored as BIGINT for the same
--   sign-overflow reason.

BEGIN;

CREATE TABLE IF NOT EXISTS household_connector_credentials (
    connector   TEXT        NOT NULL,
    ciphertext  BYTEA       NOT NULL,
    key_version BIGINT      NOT NULL,                                -- D6c: unsigned 32-bit fingerprint stored in signed 64-bit BIGINT
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by  TEXT        NOT NULL,
    updated_by  TEXT        NOT NULL,
    PRIMARY KEY (connector),
    CHECK (CHAR_LENGTH(connector) BETWEEN 1 AND 64),
    CHECK (connector ~ '^[a-z0-9_]+$'),
    -- 'self' deliberately rejected: no user owns a household row.
    -- Mirrors `_credential_internals._ACTOR_RE_TIERED` byte-for-byte.
    CHECK (created_by = 'system'
           OR created_by ~ '^admin:[A-Za-z0-9_\-]{1,64}$'),
    CHECK (updated_by = 'system'
           OR updated_by ~ '^admin:[A-Za-z0-9_\-]{1,64}$')
);

COMMENT ON COLUMN household_connector_credentials.ciphertext IS
  'Fernet/MultiFernet token bytes. NEVER plaintext. Decryption '
  'happens only in services/credential_tiers.py with the household '
  'LUMOGIS_CREDENTIAL_KEY[S] (the same MultiFernet that seals '
  'user_connector_credentials and instance_system_connector_credentials).';

COMMENT ON COLUMN household_connector_credentials.key_version IS
  'Stable 32-bit UNSIGNED fingerprint of the LUMOGIS_CREDENTIAL_KEY[S] '
  'entry that sealed this row: int.from_bytes(SHA256(key_bytes)[:4], '
  '"big"). Range 0..2^32-1. Stored as BIGINT (NOT INTEGER) because the '
  'unsigned 32-bit range overflows signed INTEGER for any fingerprint '
  'with the top bit set. Identical scheme to user_connector_credentials.';

COMMENT ON COLUMN household_connector_credentials.created_by IS
  'Normative values: ''system'' (migration / startup / rotation script) '
  'OR ''admin:<actor_user_id>'' (admin acting). NEVER NULL. ''self'' is '
  'rejected because no user owns these rows -- household credentials '
  'are shared across the household.';

COMMENT ON COLUMN household_connector_credentials.updated_by IS
  'Same vocabulary as created_by. Updated on every put/rotate.';

CREATE TABLE IF NOT EXISTS instance_system_connector_credentials (
    connector   TEXT        NOT NULL,
    ciphertext  BYTEA       NOT NULL,
    key_version BIGINT      NOT NULL,                                -- D6c: unsigned 32-bit fingerprint stored in signed 64-bit BIGINT
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by  TEXT        NOT NULL,
    updated_by  TEXT        NOT NULL,
    PRIMARY KEY (connector),
    CHECK (CHAR_LENGTH(connector) BETWEEN 1 AND 64),
    CHECK (connector ~ '^[a-z0-9_]+$'),
    -- 'self' deliberately rejected: no user owns an instance/system row.
    -- Mirrors `_credential_internals._ACTOR_RE_TIERED` byte-for-byte.
    CHECK (created_by = 'system'
           OR created_by ~ '^admin:[A-Za-z0-9_\-]{1,64}$'),
    CHECK (updated_by = 'system'
           OR updated_by ~ '^admin:[A-Za-z0-9_\-]{1,64}$')
);

COMMENT ON COLUMN instance_system_connector_credentials.ciphertext IS
  'Fernet/MultiFernet token bytes. NEVER plaintext. Decryption '
  'happens only in services/credential_tiers.py with the household '
  'LUMOGIS_CREDENTIAL_KEY[S] (the same MultiFernet that seals '
  'user_connector_credentials and household_connector_credentials).';

COMMENT ON COLUMN instance_system_connector_credentials.key_version IS
  'Stable 32-bit UNSIGNED fingerprint of the LUMOGIS_CREDENTIAL_KEY[S] '
  'entry that sealed this row: int.from_bytes(SHA256(key_bytes)[:4], '
  '"big"). Range 0..2^32-1. Stored as BIGINT (NOT INTEGER) because the '
  'unsigned 32-bit range overflows signed INTEGER for any fingerprint '
  'with the top bit set. Identical scheme to user_connector_credentials.';

COMMENT ON COLUMN instance_system_connector_credentials.created_by IS
  'Normative values: ''system'' (migration / startup / rotation script) '
  'OR ''admin:<actor_user_id>'' (admin acting). NEVER NULL. ''self'' is '
  'rejected because no user owns these rows -- instance/system '
  'credentials are operator-owned.';

COMMENT ON COLUMN instance_system_connector_credentials.updated_by IS
  'Same vocabulary as created_by. Updated on every put/rotate.';

COMMIT;
