-- Migration 014: per-user MCP tokens (`lmcp_…` opaque bearers).
--
-- Adds the `mcp_tokens` credential store keyed to `users.id`. Each row is
-- a long-lived per-user MCP bearer in the form `lmcp_<base32(28 bytes)>`.
-- See plan `mcp_token_user_map` and ADR `mcp_token_user_map.md`.
--
-- Sequencing: assumes 010-users-and-roles, 011-per-user-file-index,
-- 012-entity-relations-evidence-uniq and 013-memory-scopes are already
-- live. Idempotent and re-runnable: the CREATE statements all use
-- IF NOT EXISTS so a partial apply followed by a retry lands cleanly.
--
-- D2: token_prefix is the first 16 chars of the base32 body (~80 bits).
--     Lookup is by (token_prefix, revoked_at IS NULL) — the partial
--     unique index `mcp_tokens_active_prefix_uniq` enforces collision
--     safety on the active set while letting revoked rows freely share a
--     prefix value with a freshly-minted active token.
--
-- D3: scopes is `TEXT[] NULL` with NO default. The triad is load-bearing:
--       NULL          = unrestricted (the v1 default, what mint() inserts)
--       non-empty[]   = explicit allowlist for future per-tool enforcement
--       empty[]       = NO ACCESS (intentionally distinct from NULL —
--                       never treat empty as "all")
--     v1 verifier ignores this column entirely; the future enforcement
--     chunk wires the gate. The COMMENT below pins the contract in-DB.
--
-- D4: expires_at is forward-compat scaffolding. v1 verifier does NOT
--     read it; v1 mint API does NOT accept it as a body field
--     (MintMcpTokenRequest uses ConfigDict(extra="forbid") per D16).
--     A future hygiene chunk wires both the runtime check and the API.
--
-- D5: last_used_at is hygiene metadata. verify() updates it through a
--     5-minute in-process write throttle (`_LAST_STAMP_CACHE` LRU in
--     services/mcp_tokens.py). NULL until the first successful verify().
--
-- D7: cascade revocation on `users.disabled = TRUE` is implemented in
--     services/users.py::set_disabled (single UPDATE … WHERE user_id …
--     RETURNING *). Hard-deletion of revoked rows on user delete is
--     deliberately NOT implemented in v1 — the rows survive for
--     auditability. A future privacy-rule chunk can flip the policy.
--
-- D9: token_hash is SHA-256 hex (64 chars) computed over the plaintext
--     bearer. No argon2 — the plaintext is 224 bits of CSPRNG output, so
--     argon2's slow-hash property is irrelevant. Constant-time compare
--     happens in app code via hmac.compare_digest.
--
-- D10: this is migration 014. 010-013 are taken (see db_migrations.py
--      history). If parallel work has landed 014 first by the time this
--      lands, slide to the next free integer in the same commit and
--      update the plan + topic index. Nothing in the schema FK-references
--      the migration number.

BEGIN;

CREATE TABLE IF NOT EXISTS mcp_tokens (
    id              TEXT        PRIMARY KEY,                                -- uuid4 hex
    user_id         TEXT        NOT NULL,                                   -- FK-implied to users.id (no FK in v1, matches users + refresh_token_jti convention)
    token_prefix    TEXT        NOT NULL,                                   -- 16-char base32 lowercase; non-secret lookup handle (D2)
    token_hash      TEXT        NOT NULL,                                   -- SHA-256 hex (64 chars) of the plaintext (D9)
    label           TEXT        NOT NULL CHECK (length(label) BETWEEN 1 AND 64),
    scopes          TEXT[]      NULL,                                       -- D3 triad: NULL=unrestricted, non-empty=allowlist, empty=no access
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_used_at    TIMESTAMPTZ NULL,                                       -- NULL until first successful verify() (D5)
    expires_at      TIMESTAMPTZ NULL,                                       -- D4 forward-compat; v1 verifier ignores
    revoked_at      TIMESTAMPTZ NULL                                        -- NULL = active
);

COMMENT ON COLUMN mcp_tokens.scopes IS
  'NULL = unrestricted (v1 default). Non-empty array = explicit allowlist '
  'for future per-tool enforcement. Empty array = NO ACCESS (intentionally '
  'distinct from NULL — never treat empty as "all"). v1 verifier does not '
  'enforce; future chunk wires the gate.';

COMMENT ON COLUMN mcp_tokens.expires_at IS
  'Forward-compat scaffolding. v1 verifier ignores this column entirely. '
  'A future hygiene chunk adds the runtime check and exposes a mint-time '
  'expires_at API field. Until then, mint API does not accept expires_at.';

COMMENT ON COLUMN mcp_tokens.last_used_at IS
  'Hygiene/audit metadata. Updated by verify() with a 5-minute in-process '
  'write-throttle (per plan D5). NULL until first successful verify.';

-- Active-token uniqueness on the lookup prefix (collision-safe per D2).
-- Partial so revoked rows can freely share a prefix value with a freshly
-- minted active token if the universe is feeling vindictive.
CREATE UNIQUE INDEX IF NOT EXISTS mcp_tokens_active_prefix_uniq
    ON mcp_tokens (token_prefix) WHERE revoked_at IS NULL;

-- User-scoped enumeration for the user `GET /api/v1/me/mcp-tokens` route
-- AND the cascade revocation path. Partial so we don't pay for revoked
-- rows on the hot path.
CREATE INDEX IF NOT EXISTS mcp_tokens_user_active_idx
    ON mcp_tokens (user_id) WHERE revoked_at IS NULL;

-- Audit-style enumeration for "every token (active or revoked) for user X".
-- Cheap; a separate index because the partial above hides revoked rows.
CREATE INDEX IF NOT EXISTS mcp_tokens_user_all_idx
    ON mcp_tokens (user_id);

COMMIT;
