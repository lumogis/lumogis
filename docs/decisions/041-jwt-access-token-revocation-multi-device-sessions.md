# ADR 041 — JWT access-token revocation and multi-device session management

**Status:** Finalised
**Created:** 2026-05-15
**Last updated:** 2026-06-23
**Decided by:** `/explore --headless` (claude-opus-4-7-thinking-medium); implementation verified `/verify-plan --headless` (Composer)
**Linear:** [LUM-29](https://linear.app/lumogis/issue/LUM-29/jwt-access-token-revocation-multi-device-session-management-and-token) (`priority:1`, `risk:security`, `milestone:household-ready`, parent **LUM-51**, blocks **LUM-65** + **LUM-51**)

## Context

ADR 012 (family-LAN multi-user) deferred two pieces of the auth design as future work:

> "refresh-token revocation is single-active-jti, so logging in on a second device evicts the first (multi-device support requires a future `refresh_tokens` table); token revocation is otherwise TTL-only (≤15 min) until a `token_version` column is added"

The current shape is:

- **Access JWT:** HS256, carries `sub`, `role`, `iat`, `exp`. Default TTL 900 s. **No `jti`, no `sid`.** Revocation is TTL-only.
- **Refresh JWT:** HS256 separate secret, carries `sub`, `jti`, `iat`, `exp`. Stored in `httpOnly Secure SameSite=Strict` cookie at `/api/v1/auth`.
- **Server-side state:** single column `users.refresh_token_jti`. Logging in on a second device overwrites the first device's jti — **eviction model**.
- **Cascade:** `services/users.py::set_disabled` clears `refresh_token_jti` and cascade-revokes MCP tokens (`services/mcp_tokens.py::cascade_revoke_for_user`) inside a single `ms.transaction()` block (ADR 017 D7/D14).
- **Password change** (`_apply_new_password`): clears `refresh_token_jti` only — **active access tokens survive ≤15 min**.

**LUM-29's `milestone:household-ready` requirement** is incompatible with the eviction model (Alice with phone + laptop + tablet must hold three live sessions) and with the password-reset gap (a reset must instantly invalidate every access token, not after 15 min).

The option space is bounded — ADR 012 already named the future direction (sessions table + `token_version`) — but the precise shape, claim semantics, cache strategy, and reuse-detection contract are open.

## Decision

Adopt a **hybrid model** that combines two industry-standard patterns, both Postgres-only and both reusing Lumogis's existing cascade-revocation pattern from `services/mcp_tokens.py`:

1. **`auth_sessions` table** (one row per device session; **distinct** from Postgres **`sessions`** used by chat/memory — migration **003**) replaces the single `users.refresh_token_jti` column for refresh-side state. Login inserts a row; refresh rotates within a `family_id` (OAuth 2.1 reuse-detection — **insert new row + revoke prior row**); logout revokes a single row; admin / password-reset cascades revoke all rows for the user inside the same `ms.transaction()` block as `set_disabled` already uses for MCP tokens.
2. **Per-user `users.token_version BIGINT NOT NULL DEFAULT 1`** column carries the immediate-revocation epoch. Access JWT now carries `sub`, `role`, `iat`, `exp`, `sid`, `tv`. `auth_middleware` rejects tokens whose `tv` is below the current `users.token_version` (cached in-process with a short TTL). Bumping `token_version` (on password reset, admin disable, explicit "log out everywhere") invalidates every currently-valid access token for that user.

`users.refresh_token_jti` becomes legacy: kept in the schema for one-release downgrade safety, marked dead via a CI grep gate that refuses any new code reading or writing it.

`services/auth_sessions.py` mirrors `services/mcp_tokens.py` (SHA-256 plaintext hash, `revoked_at` column, audit events emitted after commit). New routes:

- `GET /api/v1/me/sessions` — list my devices.
- `DELETE /api/v1/me/sessions/{id}` — revoke one device.
- `POST /api/v1/me/logout-all` — bump my `token_version` and revoke all my session rows.
- `GET /api/v1/admin/users/{user_id}/sessions` — admin device inventory.
- `DELETE /api/v1/admin/users/{user_id}/sessions/{id}` — admin per-device revoke.

The `_check_mcp_bearer` JWT-detection branch (ADR `mcp_token_user_map` D6 ordering) **must** run **`verify_token`** first; on success it **must** call the same **`token_version` / `tv` assertion** as `auth_middleware` **before** comparing to `MCP_AUTH_TOKEN` opaque credentials — otherwise `/mcp/*` accepts stale Lumogis access JWTs after password reset.

**Rollout:** Access JWTs gain **`tv`** / **`sid`** immediately; operators may stage strict **`tv` presence** enforcement via **`LUMOGIS_REQUIRE_TV_CLAIM`** (default **false** first ship). Stale **`tv`** vs database is always rejected when **`AUTH_ENABLED=true`**.

## Alternatives Considered

- **Status quo (TTL-only, single-active-jti):** rejected — does not satisfy `milestone:household-ready` or `risk:security` requirements.
- **`token_version` only:** rejected — closes the password-reset gap but leaves the multi-device gap.
- **Sessions table only:** rejected — closes the multi-device gap but leaves the password-reset access-revocation gap.
- **Per-token JTI denylist (Postgres):** rejected — granularity benefit is small for household scale; per-request DB lookup defeats JWT speed advantage.
- **Bloom filter denylist + Postgres LISTEN/NOTIFY:** rejected — premature optimisation for a single-uvicorn-worker household-LAN deployment.
- **Replace JWT with opaque session tokens:** rejected — too invasive (touches MCP JWT detection, every auth test); 15-min TTL window is acceptable per industry hybrid practice.
- **Aggressive short access TTL only (60-120 s):** rejected as the *only* solution — narrows the window but doesn't actually revoke; reasonable as defence-in-depth combined with this decision but kept at 900 s in v1 to avoid changing two things at once.
- **Add Redis service:** rejected — violates "no new Docker services unless justified"; Postgres handles the load comfortably at household scale.

See `.cursor/explorations/LUM-29-jwt-revocation-multi-device-sessions.md` for full detail.

## Consequences

**Easier:**

- Multi-device household supported (Alice's phone, laptop, tablet coexist).
- "Manage devices" UX (`/me/sessions`) becomes possible without a refactor.
- Password reset (LUM-65 unblocked) actually invalidates every access token immediately.
- Admin disable cascades atomically through MCP tokens **and** sessions inside the existing `set_disabled` transaction.
- Reuse-detection on refresh path adds a meaningful theft signal (rotated token presented twice → revoke entire family).
- Pattern reuse from `services/mcp_tokens.py` keeps the cognitive load low and audit/cascade contracts identical.

**Harder:**

- One additional per-request DB read for `users.token_version` unless cached. Mitigation: in-process `TTLCache` (default 30 s); cache invalidated in-process on `_apply_new_password` / `set_disabled`. Single-uvicorn-worker today → safe; multi-worker is LUM-30's scope (Postgres LISTEN/NOTIFY is the natural extension, but **not** required by this ADR).
- Per-device immediate access-token kick is **not** in scope: revoking a single session leaves that device's access token alive ≤15 min until TTL. Documented as a limitation; follow-up issue tracks the `sid` lookup option.
- Migration touches a load-bearing column. Dual-write window for one release while `users.refresh_token_jti` is retained for downgrade safety. **Closed (LUM-244):** the column was dropped in `postgres/migrations/037-drop-users-refresh-token-jti.sql` after six releases (0.5.0 → 0.8.0); the CI grep gate (`scripts/check_refresh_token_jti_guard.py`) remains to prevent reintroduction.
- Personal data export (LUM-188) must explicitly exclude session metadata (operational, not user data) — coordinate at plan time.

**Future chunks must know:**

- Access JWT now carries `sid` and `tv` claims. Any code that mints or reads access JWTs must respect this contract.
- `auth_middleware` consults `users.token_version`; downstream handlers must not assume a verified JWT means "still valid" without going through `auth_middleware`.
- `services/auth_sessions.py` is the canonical surface for refresh-side state. Do not read or write `users.refresh_token_jti` in new code (CI grep gate).
- `set_disabled` and `_apply_new_password` are the canonical revocation primitives at the user level. Always go through them — direct DB updates bypass the cascade and audit trail.
- Multi-worker rollout (LUM-30) must extend the `token_version` cache to a coherent cross-worker mechanism (Postgres LISTEN/NOTIFY or short TTL polling); this ADR documents the extension point but does not implement it.

## Revisit conditions

- **Per-device immediate access-token revocation** becomes a household requirement (e.g. lost-device threat model demands kick within seconds, not 15 minutes) → revisit `sid` lookup on every access verify with caching.
- **Multi-uvicorn rollout (LUM-30)** ships → revisit `token_version` cache invalidation strategy across workers.
- **External IdP (OIDC) adoption** for households with operator-managed identity → revisit refresh path entirely; **`auth_sessions`** likely becomes a session-mirror, not the source of truth.
- **Postgres LISTEN/NOTIFY adoption elsewhere** in the codebase → consolidate cache invalidation onto the same mechanism.
- **MCP token model changes** (e.g. mcp_tokens gain refresh semantics) → re-evaluate whether **`auth_sessions`** and mcp_tokens should converge to a single `credentials` table.
- **Compliance / audit requirements** demand per-token immutable audit (e.g. SOC 2-style) → revisit per-token JTI denylist for the audit trail benefit.

## Status history

- 2026-05-15: Draft created by `/explore --headless` (claude-opus-4-7-thinking-medium) for LUM-29.
- 2026-05-15: Revised during `/review-plan --arbitrate` R1 — table renamed **`auth_sessions`** (avoid collision with **`003` `sessions`**); **`token_version` → BIGINT**; explicit **`tv`** enforcement on **`_check_mcp_bearer`** after decode and before opaque MCP token compare; rotation semantics = **insert-new + revoke-prior** (OAuth 2.1 reuse path).
- 2026-05-15: Finalised by `/verify-plan --headless` — implementation confirmed against plan; canonical copy at this path.
- 2026-06-23: Dual-write downgrade window closed (LUM-244) — `users.refresh_token_jti` dropped in migration 037 after six releases with zero production references; CI grep gate retained.
