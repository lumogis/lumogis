# ADR 012: Family LAN multi-user (admin vs standard user)

**Status:** Finalised — bridge decision deferred (2026-04-18); implementation confirmed decision (2026-04-18); operational follow-ups closed by hardening pass (2026-04-18)
**Created:** 2026-04-18
**Finalised:** 2026-04-18 by `/verify-plan` (composer-2)
**Hardened:** 2026-04-18 by post-/verify-plan hardening pass (composer-2 / Opus 4.7)
**Source:** *(maintainer-local only; not part of the tracked repository)*
**Plan:** *(maintainer-local only; not part of the tracked repository)*
**Exploration:** *(maintainer-local only; not part of the tracked repository)*
**Audit:** `docs/private/MULTI-USER-AUDIT.md`

## Context

Lumogis targets **local-first** deployments. Households on a LAN need **multiple people** to share one instance: **operators** who configure ingest, permissions, backups, and graph maintenance, and **members** who should use **chat and search** without gaining destructive or instance-wide powers. Before this work, the codebase verified JWTs when `AUTH_ENABLED=true` but only extracted `sub`, left most routes **unauthenticated by design**, and collapsed many execution paths to `user_id="default"`. Full RBAC was explicitly **not** required for v1 — **two roles** suffice.

## Decision

Adopt **JWT-carried roles** (`admin` and `user`) verified in-process alongside existing HS256 verification, extend `UserContext` accordingly, and enforce **`require_admin`** (or equivalent) on destructive and instance-configuration endpoints.

**Core (the orchestrator) is the long-term authentication owner.** It exposes `/api/v1/auth/*` (login, refresh, logout, me) backed by a Postgres `users` table with argon2 hashes. Refresh tokens are HS256 JWTs (separate `JWT_REFRESH_SECRET`) carried in an `httpOnly Secure SameSite=Strict` cookie when same-origin. Server-side state is a single column — `users.refresh_token_jti` — holding the currently-active jti for that user; `/refresh` validates and rotates it atomically; `/logout` and admin disable/delete clear it. Single active session per user is the documented v1 constraint.

The first-party multi-user surface is **Lumogis Web / PWA**, same-origin, consuming `/api/v1/auth/*` directly. **LibreChat is not a supported multi-user surface.** It may continue to run in the stack as a single-user / admin-only chat front-end during the transition, but no per-user identity bridge (HMAC, JWT-shared-secret, or otherwise) is built in v1. Per-user isolation arrives via Lumogis Web; LibreChat-fronted requests are attributed to a single configured admin identity for the duration of the transition.

`AUTH_ENABLED` is bi-state and decided at startup: `false` (single-user dev — synthesised `UserContext("default", role="admin")`) or `true` (multi-user — login required, no anonymous default). The orchestrator refuses to boot in `false` if the `users` table contains more than one row, and refuses to boot in `true` if the table is empty and bootstrap env is unset.

`UserContext.role` is propagated through hot paths so Alice and Bob no longer share documents/entities/memory: `permissions.log_action`, `services/tools.run_tool` and friends, `mcp_server` (resolves user from JWT `sub` then `MCP_DEFAULT_USER_ID`), `routes/events.py` (no `user_id="default"`), `services/ingest.py` (resolved `INBOX_OWNER_USER_ID`), `routes/signals.py` (four sites), `actions/executor` (`ExecutionContext(user_id, role)`), `actions/audit` (caller default; admin can query any), and `services/lumogis-graph/auth.py` (KG mirror with admin gating on `/mgm` and write paths). A CI grep gate refuses any new `user_id="default"` literal in `orchestrator/{services,routes,actions,signals,plugins}/` or `services/lumogis-graph/`.

Treat **reverse-proxy / forward-auth** hardening as **optional** outer layering, not a substitute for application-level checks. Coordinate with the **cross-device web** plan (*(maintainer-local only; not part of the tracked repository)*) — both share the same `/api/v1/auth/*` surface and `UserContext.role` abstraction.

### Why the LibreChat bridge was deferred

The R1 arbitration revision specified an HMAC-headers + peer-socket + live-role-recheck bridge, replacing an earlier Bearer-JWT-shared-secret design. After Phase 3.1 acceptance, the project decided that **LibreChat does not need to remain a supported multi-user surface during the transition**. With the first-party Lumogis Web / PWA shell as the long-term per-user surface, building any bridge — even a removable one — only buys isolation inside the LibreChat-fronted path while permanently coupling `auth.py` and `librechat_config.py` to LibreChat's release cadence. Plan Phases 3.5 (spike) and 4 (bridge implementation) are **dropped**. The historical bridge design is preserved in plan §23 should the project ever reverse course. Until then, **no bridge code is shipped**.

## Alternatives Considered

- **Reverse-proxy-only access control** — Rejected as sole solution: bypassable if the orchestrator port is reachable; still no robust identity for data isolation.
- **Dedicated OIDC IdP for every household** — Viable long-term / for advanced homelabs; rejected as **mandatory for v1** due to operational weight and extra services.
- **Full RBAC framework** (Casbin, Cerbos, OPA) — Rejected for v1 scope; two static roles suffice.
- **LibreChat-as-multi-user-surface via HMAC bridge** — Specified in earlier revision; deferred 2026-04-18 in favour of Lumogis Web as the first-party surface.

## Consequences

**Easier:** Clear **operator vs member** semantics for LAN docs; `/api/v1/auth/*` is a stable contract for the future Lumogis Web client; data-plane functions take `user_id` explicitly with no hidden context; admin-vs-user boundaries are enforced at the route level via FastAPI dependencies; argon2 hashing + per-IP/per-email rate limit + single-active-jti rotation provide a defensible v1 security posture.

**Harder:** Single-uvicorn-worker assumption is now load-bearing for the in-process rate limiter (must move to Postgres/Redis if `--workers N` is enabled — flagged in plan §19); refresh-token revocation is single-active-jti, so logging in on a second device evicts the first (multi-device support requires a future `refresh_tokens` table); token revocation is otherwise TTL-only (≤15 min) until a `token_version` column is added; `connector_permissions` remains deployment-wide (per-user is deferred); `AUTH_SECRET` is **not** auto-rotated by the entrypoint and must be set explicitly when enabling `AUTH_ENABLED=true`; `LUMOGIS_PUBLIC_ORIGIN` Origin-header CSRF check on cookie-authenticated routes is documented but not yet enforced (relies on `SameSite=Strict` for v1).

**Future chunks must know:**

- `UserContext.role` is a stable contract — additional roles will arrive as **new string values**, never as a different field shape.
- `/api/v1/auth/*` is the long-term contract for browser auth. **No LibreChat bridge** is part of that contract. The `cross_device_lumogis_web` plan must consume these endpoints directly.
- `run_tool(name, input_, *, user_id)` is the new tool-loop contract; `loop.ask` / `loop.ask_stream` require `user_id` keyword.
- `ExecutionContext(user_id, role, request_id)` is the new action-handler contract.
- `MCP_DEFAULT_USER_ID` is a transitional setting; future MCP work should introduce a per-token user mapping. The MCP server already prefers JWT `sub` from a per-request `Authorization: Bearer …` over the env fallback (Phase 3.1).
- Refresh-token model is **single-active-jti per user**. Multi-device support requires a future `refresh_tokens` table; the column on `users` becomes legacy at that point.

## Revisit conditions

- The project decides to re-host **LibreChat** as a long-term per-user surface (i.e. Lumogis Web is abandoned or repositioned) — revisit the deferred bridge design preserved in plan §23.
- Product requires **per-user Ask/Do** or fine-grained graph ACLs — revisit RBAC libraries or permission matrices beyond two roles.
- A second authenticated client (beyond Lumogis Web) needs to attach to `/api/v1/auth/*` with different session semantics (multi-device active, mobile push refresh, etc.) — revisit the single-active-jti refresh model.
- The orchestrator scales beyond a single uvicorn worker — move the in-process rate limiter to a shared backing store.

## Status history

- 2026-04-18: Draft created by `/explore`.
- 2026-04-18: Revised during `/review-plan --arbitrate R1` (composer-2) — bridge approach changed from "Bearer JWT verified by aligned signing secret" to "HMAC headers + peer-socket gate + live role re-check". Refresh-token model added (single-active-jti per user, separate `JWT_REFRESH_SECRET`).
- 2026-04-18: Bridge decision superseded / deferred by the user post-Phase-3.1 acceptance.
- 2026-04-18: **Finalised by `/verify-plan` (composer-2).** Implementation confirmed the architectural decision; 103/103 family-LAN tests pass. Operational deviations (secret-sentinel string, missing `make secrets`, `AUTH_SECRET` not in entrypoint auto-rotation, `LUMOGIS_PUBLIC_ORIGIN` documented-but-not-enforced) are recorded as follow-ups in the plan and do not change the architectural decision.
- 2026-04-18: **Operational follow-ups closed by hardening pass (composer-2 / Opus 4.7).** All four prior deviations are closed:
  - Sentinel convention picked: `change-me-in-production` is canonical; both it and the legacy `__GENERATE_ME__` are rejected as placeholders. `make secrets` references removed.
  - `AUTH_SECRET` is now refused-on-boot at two layers (`orchestrator/main.py::_enforce_auth_consistency` and `orchestrator/docker-entrypoint.sh`) when `AUTH_ENABLED=true` and the value is empty / a placeholder. Auto-rotation is intentionally NOT applied — operators flip family-LAN mode on deliberately and own this secret.
  - `LUMOGIS_PUBLIC_ORIGIN` `Origin`-header CSRF check enforced via the new `orchestrator/csrf.py` on `POST /api/v1/auth/refresh` and admin POST/PATCH/DELETE; bypass matrix excludes Bearer callers, GET/HEAD/OPTIONS, dev mode, and unset `LUMOGIS_PUBLIC_ORIGIN`.
  - 18 new regression tests added across `test_auth_phase1.py`, `test_secret_sentinels.py`, and `test_csrf_origin_check.py`.
  - Final test counts: 728 passed / 6 failed / 6 skipped — the 6 failures are the same pre-existing failures owned by `capability_launchers_and_gateway` (4) and `lumogis_graph_service_extraction` (2). Zero regressions from this pass.
  The architectural decision recorded above is unchanged.
