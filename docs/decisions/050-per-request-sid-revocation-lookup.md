# ADR 050: Per-request `sid` revocation lookup (LUM-243)

**Status:** Accepted
**Created:** 2026-05-16
**Last updated:** 2026-05-17
**Decided by:** `/explore --headless` (draft); `/review-plan --arbitrate` R1; **`/verify-plan`** finalisation
**Linear:** [LUM-243](https://linear.app/lumogis/issue/LUM-243/p2-per-request-sid-lookup-for-immediate-per-device-access-token) (extends [ADR 041](041-jwt-access-token-revocation-multi-device-sessions.md) / **LUM-29**)

## Context

ADR **041** shipped hybrid `auth_sessions` + per-user `token_version`: access JWTs carry `sid` and `tv`, and `jwt_revocation_failure_reason` enforces `tv`. Cascades (password change, disable, logout-all) invalidate via `tv`. Single-session revoke (`DELETE /api/v1/me/sessions/{id}` and admin equivalent) did **not** invalidate access JWTs until `ACCESS_TOKEN_TTL_SECONDS` elapsed — **LUM-243** closes that window using the same staged-flag and in-process cache patterns as `tv`.

Constraints: local-first, single-process default, no new Docker services or pip dependencies, no Postgres migrations; household scale.

## Decision

1. Extend **`jwt_revocation_failure_reason`** ( **`orchestrator/auth.py`** ) after **`tv`** succeeds: when **`LUMOGIS_REQUIRE_SID_REVOCATION_CHECK`** is **`true`** (default **`false`**), evaluate bearer **`sid`** via **`services/auth_sessions.is_session_revoked(session_id, user_id)`** using **`WHERE id = %s AND user_id = %s`**. Missing or non-**`str`** **`sid`** does **not** add a new 401 (legacy mint parity with optional **`tv`**).

2. Cache **`sid → (deny, ts)`** in **`_SID_REVOCATION_CACHE`** implemented as **`_TTLSidLRU`** ( **`maxsize = _TOKEN_VER_CACHE_MAX`** ). TTL from **`LUMOGIS_SID_REVOCATION_CACHE_TTL_SECONDS`** (default **`30`**, clamp **`0…3600`**; **`0`** disables LRU). LRU key is **`sid`** only; **`user_id`** remains authoritative in SQL.

3. **`invalidate_sid_cache(sid)`** is exported from **`auth.py`** only — no **`invalidate_sid_cache_for_user`**. Every mutation that revokes or deletes **`auth_sessions`** rows calls **`invalidate_sid_cache`** post-commit / after visible revoke: **`revoke_session_for_user`**, **`revoke_session_admin`**, **`rotate_refresh`** (prior **`jti`**), **`_handle_reuse`** (before audits), **`bump_token_version_and_revoke_all_sessions`**, **`users.set_disabled`** / **`_apply_new_password`** loops after **`revoke_all_active_in_transaction_for_user`**, and **`delete_user`** (**`SELECT id …`** then invalidate each **`sid`** before **`DELETE`**).

4. Transient DB errors on the **`sid`** lookup path **fail closed** (**`return "invalid token"`**) and **must not** **`put`** into the LRU; log WARNING with **`sid[-4:]`** and exception type only.

Multi-worker coherence remains **LUM-30** (same class of problem as **`_TOKEN_VER_CACHE`**).

## Alternatives Considered

See draft history and `.cursor/explorations/archived/LUM-243-sid-revocation-lookup.md`: unconditional DB lookup (Option 1), Bloom/Cuckoo filters, **`LISTEN/NOTIFY`**, status quo until access TTL — rejected per exploration.

## Consequences

**Positive:** Per-device revoke takes effect within cache TTL (default 30 s), immediately on the worker that performed the revoke via **`invalidate_sid_cache`**.

**Trade-offs:** Cross-process staleness up to LRU TTL until **LUM-30** ships a shared invalidation story.

## Status history

- **2026-05-17:** Finalised by **`/verify-plan`** — implementation confirmed; canonical copy under **`docs/decisions/`**.
- **2026-05-17:** Draft revised during **`/review-plan --arbitrate` R1** — **`users.py`** / **`delete_user`** invalidation, **`_TTLSidLRU`**, exception-no-**`put`** rule; dropped **`invalidate_sid_cache_for_user`** from Decision text.
- **2026-05-16:** Draft created by **`/explore --headless`** — Option 2 (TTL LRU mirroring **`_TTLVersionLRU`**).
