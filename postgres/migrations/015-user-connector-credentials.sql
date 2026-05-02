-- Migration 015: per-user connector credentials.
--
-- Per-user, per-connector encrypted credential bundles. See ADR
-- `per_user_connector_credentials.md` and plan
-- `.cursor/plans/per_user_connector_credentials.plan.md`.
--
-- Sequencing: assumes 010-014 (users-and-roles, per-user-file-index,
-- entity-relations-evidence-uniq, memory-scopes, mcp-tokens) are already
-- live. Idempotent and re-runnable: the CREATE statements all use
-- IF NOT EXISTS so a partial apply followed by a retry lands cleanly.
--
-- D1: connector ids are lowercase [a-z0-9_]+ (1..64 chars). The
--     authoritative registry is orchestrator/connectors/registry.py;
--     these CHECK constraints are a defence-in-depth safety net.
--
-- D6c: key_version is a stable 32-bit *unsigned* fingerprint of the
--      household MultiFernet key (LUMOGIS_CREDENTIAL_KEY[S]) that
--      sealed the row -- specifically
--      int.from_bytes(SHA256(key_bytes)[:4], "big"). It is order-
--      independent, survives prepending a new primary key, and serves
--      as both a diagnostic tag AND the rotation skip predicate.
--      `scripts/rotate_credential_key.py` re-seals every row whose
--      key_version != current primary fingerprint via
--      MultiFernet.rotate() (a fresh IV per call means ciphertext
--      equality cannot answer "already current" -- key_version can).
--      Stored as BIGINT (signed 64-bit) NOT INTEGER (signed 32-bit) --
--      the unsigned 32-bit range (0..2^32-1) overflows INTEGER for any
--      fingerprint with the top bit set (~50% of all keys). BIGINT
--      trivially holds the full unsigned 32-bit range with headroom.
--
-- D2:  user_export deliberately OMITS this table; raw pg_dump
--      backups still carry ciphertext. Restore requires the
--      matching key -- losing it makes credentials unrecoverable.
--
-- D10 (mcp_tokens precedent): if a parallel branch lands 015 first
--      slide to next free integer in the same commit and update the
--      plan + topic index. Nothing in the schema FK-references
--      the migration number.

BEGIN;

CREATE TABLE IF NOT EXISTS user_connector_credentials (
    user_id     TEXT        NOT NULL,
    connector   TEXT        NOT NULL,
    ciphertext  BYTEA       NOT NULL,
    key_version BIGINT      NOT NULL,                                -- D6c: unsigned 32-bit fingerprint stored in signed 64-bit BIGINT
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by  TEXT        NOT NULL,
    updated_by  TEXT        NOT NULL,
    PRIMARY KEY (user_id, connector),
    CHECK (CHAR_LENGTH(connector) BETWEEN 1 AND 64),
    CHECK (connector ~ '^[a-z0-9_]+$'),
    -- Tightened from `LIKE 'admin:%'` (which accepts the empty actor
    -- 'admin:') to a regex requiring 1..64 safe chars after the prefix;
    -- matches the Python `_actor_str` regex in services/connector_credentials.py.
    CHECK (created_by IN ('self','system')
           OR created_by ~ '^admin:[A-Za-z0-9_\-]{1,64}$'),
    CHECK (updated_by IN ('self','system')
           OR updated_by ~ '^admin:[A-Za-z0-9_\-]{1,64}$')
);

COMMENT ON COLUMN user_connector_credentials.ciphertext IS
  'Fernet/MultiFernet token bytes. NEVER plaintext. Decryption '
  'happens only in services/connector_credentials.py with the '
  'household LUMOGIS_CREDENTIAL_KEY[S].';

COMMENT ON COLUMN user_connector_credentials.key_version IS
  'Stable 32-bit UNSIGNED fingerprint of the LUMOGIS_CREDENTIAL_KEY[S] '
  'entry that sealed this row: int.from_bytes(SHA256(key_bytes)[:4], '
  '"big"). Range 0..2^32-1. Stored as BIGINT (NOT INTEGER) because the '
  'unsigned 32-bit range overflows signed INTEGER for any fingerprint '
  'with the top bit set. Order-independent (survives key-list '
  'reordering and prepend-on-rotate). Both a diagnostic tag AND the '
  'rotation skip predicate: scripts/rotate_credential_key.py re-seals '
  'every row whose key_version differs from the current primary key '
  'fingerprint. Re-encryption itself uses MultiFernet.rotate(), which '
  'always emits a fresh IV, so ciphertext equality cannot answer '
  '"already current" -- key_version is the only sound predicate.';

COMMENT ON COLUMN user_connector_credentials.created_by IS
  'Normative values: ''self'' (user themselves), ''system'' '
  '(migration / startup), ''admin:<actor_user_id>'' (admin acting '
  'on behalf). NEVER NULL.';

COMMENT ON COLUMN user_connector_credentials.updated_by IS
  'Same vocabulary as created_by. Updated on every put/rotate.';

-- Per-user enumeration (the GET /api/v1/me/connector-credentials hot path).
-- Cheap; PK already indexes (user_id, connector) for point lookups.
CREATE INDEX IF NOT EXISTS user_connector_credentials_user_idx
    ON user_connector_credentials (user_id);

COMMIT;
