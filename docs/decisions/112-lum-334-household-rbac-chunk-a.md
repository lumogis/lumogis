# ADR-112: Household RBAC backend — enforcement audit and last-active (LUM-334 Chunk A)

**Status:** Finalised
**Created:** 2026-06-22
**Last updated:** 2026-06-22
**Decided by:** `/create-plan` + `/review-plan` R1 LUM-334; implemented `claude/lum-334-household-rbac-impl`; finalised by `/verify-plan` 2026-06-22
**Plan:** `.cursor/plans/archived/LUM-334-household-auth-rbac.plan.md`
**Linear:** [LUM-334](https://linear.app/lumogis/issue/LUM-334/household-auth-admin-and-member-roles-rbac-enforcement-user-management)

## Context

LUM-334 programmes household auth: admin vs member roles, RBAC on every endpoint, and a user-management panel. Chunk A (backend) found most enforcement already shipped (ADR-012, LUM-29 sessions, `admin_users` CRUD) but three gaps remained:

1. **Scope publish routes** — twelve `routes/scope.py` publish/unpublish handlers used `Depends(get_user)`, which does not 401 unauthenticated callers.
2. **`last_seen_at`** — the admin panel (Chunk B) needs a throttled “last active” timestamp on `users`.
3. **ADR-101 commercialisation reconciliation** — multi-user is **free** AGPL core; no licence/`402` gate on user creation (ticket premise superseded).

Migration **035** was already taken by LUM-511 ingest progress; this chunk ships **`postgres/migrations/036-users-last-seen-at.sql`**.

## Decision

1. **Enforcement** — upgrade all twelve scope publish/unpublish routes to `Depends(require_user)`; lock with `orchestrator/tests/test_rbac_enforcement_matrix.py` (role 403 matrix, scope route introspection via `_iter_api_routes`, `get_user`-without-gate guard).
2. **`users.last_seen_at`** — nullable `TIMESTAMPTZ`; `services/users.py::touch_last_seen` as a single conditional `UPDATE` throttled by `USER_LAST_SEEN_THROTTLE_SECONDS` (default 300); fire-and-forget dispatch from `auth.py` with bounded per-worker LRU skip cache.
3. **Admin API** — thread `last_seen_at` through `InternalUser` → `_row_to_internal` → `_to_admin_view` → `UserAdminView`; exposed on `GET /api/v1/admin/users`.
4. **Commercialisation** — confirm **no** seat cap or licence gate on `POST /api/v1/admin/users`; document alignment with **ADR-101** (`docs/decisions/101-lum-442-commercialisation-ecosystem-model.md` — multi-user free).

**Out of scope (Chunk A):** web admin panel UI (LUM-520 / Chunk B); `guest` role; invite flow (LUM-186).

## Alternatives considered

- **Keep `get_user` on scope routes** — rejected; data-mutating routes must fail closed at the dependency layer.
- **Read-before-write throttle for `last_seen_at`** — rejected; conditional `UPDATE` is race-safe across workers.
- **Rename wire role `user` → `member`** — rejected (breaking JWT/rows); UI label only in Chunk B.

## Consequences

**Positive:** LUM-473 appliance multi-user on loopback can rely on audited enforcement; Chunk B consumes `last_seen_at`; scope publish cannot silently fail open.

**Limits:** Plan-listed `last_seen_at` API/throttle/migration tests were not all shipped in the branch — P2 follow-up under LUM-334 or Chunk B verify (see verify-plan adequacy notes). Route introspection tests must use `_iter_api_routes` (FastAPI `_IncludedRouter` nesting).

**Coordination:** Unblocks LUM-473; Chunk B (LUM-520) surfaces “Member” / “Last active” in Lumogis Web.

## Revisit conditions

- New `/api/v1` routes — extend enforcement matrix / `test_auth_phase2` guards.
- `guest` role — separate chunk with its own enforcement tests.
- Multi-worker `--workers N` at scale — revisit in-process LRU throttle vs shared store.

## Status history

- 2026-06-22: Plan ready after arbitration R1 (`p0=0`, `p1=0`).
- 2026-06-22: Implemented on `claude/lum-334-household-rbac-impl`; merged to `dev` with migration renumber **036**.
- 2026-06-22: Finalised by `/verify-plan` — ADR-112; test fix for `_iter_api_routes`.
