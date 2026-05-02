# ADR: Per-user connector credentials table

**Status:** Finalised 2026-04-20
**Created:** 2026-04-18
**Last updated:** 2026-04-20
**Finalised copy:** `docs/decisions/018-per-user-connector-credentials.md`
**Decided by:** composer-2 (Opus 4.7) via /explore; **final doc pass** 2026-04-20 — closed remaining product/API choices + implementation-facing contract (aligned with *(maintainer-local only; not part of the tracked repository)*). **Revision R1 (2026-04-20):** redefined `key_version` from "version index" to a stable per-key fingerprint (SHA-256 prefix over the raw Fernet key bytes) after critique-round-1 (gpt-5.4) showed that the index-based definition silently breaks key rotation when a new primary key is prepended to the front of `LUMOGIS_CREDENTIAL_KEYS`. See `key_version` row below + plan §`reencrypt_all_to_current_version()` contract. **Revision R2 (2026-04-20):** widened `key_version` storage from `INTEGER` (signed 32-bit) to `BIGINT` (signed 64-bit) after a post-R1 rebrief found that the unsigned 32-bit fingerprint range (`0 .. 2³² − 1`) overflows Postgres `INTEGER` for any value with the top bit set — roughly half of all valid keys would have failed to insert. Also reconciled the stale-connector lifecycle: read-metadata and delete service paths (`get_record`, `list_records`, `delete_payload`) drop the registry-membership check while keeping format validation; write and runtime paths (`put_payload`, `get_payload`, `resolve`) stay registry-strict. Routes drop the **422 unknown connector** mapping on GET-single and DELETE; PUT keeps it. See plan §What this builds, §Public surface, §Routes table.

## Context

Audit **B4** calls for per-user connector credential storage. Today, global env and **`app_settings`** cover many secrets; multi-user households need **`(user_id, connector)`**-scoped, **encrypted-at-rest** rows. **No new Docker service**; **`cryptography`** already transitive via **`mcp>=1.10.0`**.

## Decision

**Table `user_connector_credentials`** — **one row per `(user_id, connector)`**:

- **`PRIMARY KEY (user_id, connector)`** — the natural unique key; no separate surrogate PK required for v1.
- **`user_id TEXT NOT NULL`**, **`connector TEXT NOT NULL`** with **`CHECK (connector ~ '^[a-z0-9_]+$')`** and **`CHECK (CHAR_LENGTH(connector) BETWEEN 1 AND 64)`**.
- **`ciphertext BYTEA NOT NULL`** — **only** Fernet/MultiFernet token bytes on disk; **plaintext JSON** exists **only in memory** after decrypt inside **`services/connector_credentials.py`** (exact symbols in `/create-plan`).
- **`created_at`**, **`updated_at`** — `TIMESTAMPTZ NOT NULL` (defaults as usual).
- **`created_by TEXT NOT NULL`**, **`updated_by TEXT NOT NULL`** — normative values: **`self`**, **`system`**, or **`admin:<actor_user_id>`**; use the literal **`system`** for migration/system/bootstrap writes (**not** SQL `NULL`).
- **`key_version BIGINT NOT NULL`** — **stable per-key fingerprint** of the household `MultiFernet` key that sealed the row. Specifically `int.from_bytes(hashlib.sha256(<raw_fernet_key_bytes>).digest()[:4], "big")`, an **unsigned** 32-bit value in `0 .. 2³² − 1`. Stored as `BIGINT` (signed 64-bit) NOT `INTEGER` (signed 32-bit) because the unsigned 32-bit range overflows `INTEGER` for any fingerprint with the top bit set (~50% of all keys). Order-independent — survives `LUMOGIS_CREDENTIAL_KEYS` reorderings and prepend-on-rotate. The rotation script uses `cryptography.fernet.MultiFernet.rotate()` to **re-encrypt** rows that need rotation but uses **`row.key_version == current_fp`** as the **skip predicate** — Fernet uses a fresh random IV on every encrypt, so comparing rotate()'s output to the original ciphertext is unreliable; fingerprint comparison is the correct predicate now that the fingerprint is stable per-key. `key_version` therefore serves a dual role: diagnostic tag ("which household key sealed this row?") and rotation skip predicate. <!-- ADR-R1 (2026-04-20): redefined from "version index" after critique-round-1 (gpt-5.4) showed the index-based definition was indistinguishable across key prepends and would silently skip rotation. --> <!-- ADR-R2 (2026-04-20): widened from INTEGER to BIGINT after rebrief found INTEGER overflows for unsigned values ≥ 2³¹; ~50% of all valid fingerprints would have failed to insert. --> <!-- ADR-IMPL-R1 (2026-04-20): corrected the rotation predicate. R1 said "compares ciphertexts" — that does not work because Fernet allocates a fresh random IV per encrypt, so MultiFernet.rotate() always returns a different ciphertext. Caught during implementation by `test_rotation_skips_already_current`. The stable fingerprint IS the predicate; rotate() is the re-encryption primitive. -->

**Application crypto:** **`cryptography.fernet.MultiFernet`** keyed from **`LUMOGIS_CREDENTIAL_KEY`** (+ legacy keys in the list when rotating). **Canonical `connector` strings** live in **Python constants / registry** (source of truth); DB `CHECK` constraints are the safety net. **No SQL `ENUM`**, **no FK** to a connector catalog in v1.

**AUTH (locked):**

- **`AUTH_ENABLED=true`:** **no** env-based secret fallback **at request time** for this subsystem; missing row ⇒ **`connector_not_configured`** (see HTTP mapping below).
- **`AUTH_ENABLED=false`:** env fallback **permitted** for legacy single-user installs until consumers migrate.

**Export & backup (locked):**

- **Standard per-user zip export** **omits** this table entirely — **no ciphertext** in the user export bundle. Manifest includes: **`connector_credentials: excluded (sensitive, non-portable in standard export)`**.
- **Raw database backups** (`pg_dump`, volume snapshots, etc.) **still contain `ciphertext`** as stored (encrypted bytes, not plaintext).
- **Disaster recovery:** restore **requires** the **`LUMOGIS_CREDENTIAL_KEY`** (or **matching active rotation key set**) that was used to encrypt those rows; **if the key is lost, encrypted credentials are unrecoverable**.
- **Admin-only sealed backup/restore** as a dedicated product flow: **deferred**, **out of scope** for this substrate chunk.

**Migration:** **`postgres/migrations/{NNN}-user-connector-credentials.sql`** with **`NNN` = next free on branch HEAD at implementation**; **same commit** updates ***(maintainer-local only; not part of the tracked repository)*** and any plan text pinning `NNN`.

**Rollout order (locked for planning):** **`testconnector`** → **ntfy** → **CalDAV** → **LLM provider keys last** (LLM touches **`get_llm_provider` / `is_model_enabled` hot paths**).

**Domain errors (never conflate):**

- **`connector_not_configured`** — no row for `(user_id, connector)`.
- **`credential_unavailable`** — decrypt failure, `InvalidToken`, malformed ciphertext.
- **`connector_access_denied`** — **`permissions` / executor** denial (Ask/Do, routine, etc.) — **not** a credential-store miss.

**HTTP mapping (locked):**

| Surface | `connector_not_configured` | `credential_unavailable` | `connector_access_denied` |
|--------|----------------------------|--------------------------|---------------------------|
| **Credential-management** routes (CRUD/read of the stored secret) | **404** | **503** | **403** |
| **Runtime action** routes (depend on a connector secret, not credential CRUD) | **424 Failed Dependency** | **503** | **403** |

Sentinel wiring: **`docker-entrypoint.sh`** + refuse-to-boot when **`AUTH_ENABLED=true`** and **`LUMOGIS_CREDENTIAL_KEY`** empty/placeholder (same family as other auth secret checks).

**Permanent ADR reconciliation:** Some **`docs/decisions/*.md`** files may still carry **stale provisional migration numbers** for this chunk. **Reconcile before or during implementation** (via **`/verify-plan`** or an explicit docs edit pass) so **`docs/decisions/`** and this draft are not a **long-term split source of truth** — exploration-only passes did not edit `docs/decisions/` per workspace rules.

## Alternatives Considered

See exploration v2: two-table split, generic KV, envelope DEK, pgcrypto, Vault-class, `app_settings` plaintext — all **rejected** for v1 default.

## Consequences

**Easier:** Ordered consumer rollout; one service owns crypto; `MultiFernet` rotation without schema churn.

**Explicit costs:** Operators must understand **export vs raw backup** (export omits secrets; **DB dumps retain ciphertext**); **key loss = credential loss**; **sealed admin backup** remains a **future** chunk.

**Siblings:** A2 (`connector_permissions`), B10 (`mcp_tokens`) unchanged by this ADR.

## Revisit conditions

- Ship **admin sealed backup** product.  
- Household scale warrants per-user DEK / envelope encryption.  
- Payload size forces external blob storage.  
- `cryptography` deprecates Fernet.

## Status history

- 2026-04-18: Draft created.  
- 2026-04-20: v2 exploration + migration next-free rule.  
- 2026-04-20: Tightening (registry, AUTH, export).  
- 2026-04-20: **Final doc pass** — sealed backup **deferred**; **CHAR_LENGTH 1–64**; **`created_by`/`updated_by` NOT NULL** + `self` / `system` / `admin:<id>`; **HTTP + domain codes** (404/424/503/403); **DR + key-loss** wording; **`key_version`** definition; **`PRIMARY KEY` explicit**; **BYTEA-only** at rest; **`docs/decisions/` reconciliation** note.
- 2026-04-20 (R1): **Critique-driven revision** — `key_version` redefined from "version index of the household MultiFernet key" to a stable per-key fingerprint (`int.from_bytes(SHA256(key_bytes)[:4], "big")`). Rotation algorithm pinned to `MultiFernet.rotate()` (compares ciphertexts, ignores `key_version`). Triggered by `gpt-5.4` critique round 1: position-based `key_version` would silently skip rotation when a new primary key was prepended to `LUMOGIS_CREDENTIAL_KEYS`. See *(maintainer-local only; not part of the tracked repository)* (D1.1 / D8.2) and the arbitration log appended to *(maintainer-local only; not part of the tracked repository)*.
- 2026-04-20 (R2): **Rebrief-driven revision** — three findings from a post-R1 read of the revised plan: (1) `key_version` storage column widened from `INTEGER` to `BIGINT` because the unsigned 32-bit fingerprint range overflows signed `INTEGER` (~50% of valid keys would have failed to insert); (2) stale-connector lifecycle reconciled — read-metadata and delete service paths drop the registry-membership check (operators can inspect and clean up rows for connectors that were previously registered and have since been removed) while keeping format validation; write and runtime paths stay registry-strict; routes drop **422 unknown connector** on GET-single and DELETE; (3) wording cleanups in the plan's error table, admin-route header, and `What this builds` summary so the public surface is described identically everywhere. See the rebrief log appended to *(maintainer-local only; not part of the tracked repository)* for per-finding rationale.
- 2026-04-20 (IMPL-R1): **Implementation-driven revision** — the rotation skip predicate was changed from "compare rotated ciphertext to old ciphertext" to "compare `row.key_version` to `current_fp`". `cryptography.fernet.MultiFernet.rotate()` allocates a fresh random IV per call, so the rotated ciphertext is always different from the original even when re-sealing under the same key — making ciphertext comparison unworkable. The stable per-key fingerprint adopted in R1 is the correct predicate. Caught by `test_rotation_skips_already_current` in `orchestrator/tests/test_connector_credentials_service.py`. ADR `key_version` row + plan §`reencrypt_all_to_current_version()` updated; no schema change.
- 2026-04-20: **Finalised by /verify-plan** — implementation confirmed the decision. All planned files present, 91 dedicated tests + 987 orchestrator suite pass, 32/32 definition-of-done items met. R1 + R2 + IMPL-R1 revisions are recorded in this status history and reflected in the BIGINT column type, registry-strictness model, and `key_version`-based rotation skip predicate. Finalised copy written to `docs/decisions/018-per-user-connector-credentials.md`.
