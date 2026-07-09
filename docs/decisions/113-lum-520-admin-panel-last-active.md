# ADR-113: Household admin panel — Last active column and Member labels (LUM-520 Chunk B)

**Status:** Finalised
**Created:** 2026-06-22
**Last updated:** 2026-06-22
**Decided by:** as-shipped implementation (`claude/lum-520-admin-panel-last-active`)
**Finalised by:** `/record-retro` 2026-06-22 (Composer)
**Plan:** none — Chunk B of LUM-334 shipped without a separate plan file
**Exploration:** `.cursor/explorations/lum-520-admin-panel-last-active_retro.md`
**Draft mirror:** `.cursor/adrs/lum-520-admin-panel-last-active.md`
**Builds on:** ADR-112 (LUM-334 Chunk A — `last_seen_at` on `GET /api/v1/admin/users`)

**Linear:** [LUM-520](https://linear.app/lumogis/issue/LUM-520) (child of LUM-334)

## Context

LUM-334 Chunk A (ADR-112) added `users.last_seen_at` and backend RBAC hardening. Chunk B needed a Lumogis Web admin panel update: show **Last active** from `last_seen_at`, surface wire role `user` as **Member** (not rename the JWT value), and keep last-admin safety UX from FP-046.

Implementation landed on `claude/lum-520-admin-panel-last-active` (2 commits) and merged to `dev` without `/create-plan` → `/verify-plan`.

## Decision

1. **`AdminUsersView`** — add `last_seen_at` to the row type; new table column **Last active** via `formatLastActive()` (`never` when null).
2. **Role labels** — export `roleLabel()`: `admin` → "Admin", `user` → "Member"; unknown roles pass through verbatim.
3. **Create/import forms** — option text uses "Member" while wire value stays `user`.
4. **Tests** — extend `AdminUsersView.test.tsx` for helpers + last-active column rendering; retain sole-active-admin disable guards.

No new API routes or backend changes in this chunk.

## Alternatives considered

- **Rename wire role to `member`** — rejected in LUM-334 plan (breaking JWT/rows).
- **Use `last_login_at` only** — rejected; Chunk A intentionally tracks throttled request activity via `last_seen_at`.

## Consequences

**Positive:** Operators see household member activity in the existing admin users table; UI vocabulary matches product language ("Member") without protocol churn.

**Limits:** Vitest only (13 tests); no Playwright E2E for admin users table in this retro slice.

## Revisit conditions

- Playwright admin-users smoke when LUM-334 programme closes end-to-end.
- `guest` role UI — separate chunk when role ships.

## Testing retrospective

| Layer | Command | Result |
|-------|---------|--------|
| Vitest | `npm test -- --run tests/features/admin/AdminUsersView.test.tsx` | **13 passed** |

## Status history

- 2026-06-22: Merged to `dev` from `claude/lum-520-admin-panel-last-active`.
- 2026-06-22: Finalised by `/record-retro`.
