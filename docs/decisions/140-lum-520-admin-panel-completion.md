# ADR-140: Household admin panel completion — role confirm, self-guard, E2E (LUM-520 / LUM-545)

**Status:** Finalised
**Created:** 2026-06-26
**Last updated:** 2026-06-26
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-06-26 (Composer)
**Plan:** none — LUM-520 completion shipped on `claude/persona-c-household-launch-ly74ed` with verify-plan pass; LUM-545 test child
**Exploration:** `.cursor/explorations/lum_520_admin_panel_completion_retro.md`
**Draft mirror:** `.cursor/adrs/lum_520_admin_panel_completion.md`
**Builds on:** ADR-112 (LUM-334 Chunk A RBAC API), ADR-113 (LUM-520 Chunk B last-active / Member labels)

**Linear:** [LUM-520](https://linear.app/lumogis/issue/LUM-520) (parent chunk B), [LUM-545](https://linear.app/lumogis/issue/LUM-545) (mobile Playwright child)

## Context

ADR-113 recorded the first LUM-520 slice (Last active column, Member labels, 13 Vitest cases). Remaining household admin panel UX — promote/demote confirmation, signed-in admin self-guard on Disable/Delete, member-count summary, desktop + mobile Playwright smoke — landed on `claude/persona-c-household-launch-ly74ed` (3 commits) without a separate plan file. LUM-545 closed the P2 mobile E2E follow-up from ADR-113's revisit conditions.

Web-only: no backend, route, schema, or auth-gate changes.

## Decision

1. **`AdminUsersView`** — `window.confirm` before role PATCH (promote → "Make Admin", demote → "Make Member"); `willPromoteToAdmin` hoisted so button label and PATCH body share one source of truth.
2. **Self-guard** — `isSelf(u)` disables Disable and Delete for the signed-in admin with friendly `title` tooltips (mirrors backend 400 self-guard; no raw error surface).
3. **Member summary** — header shows `"<n> member(s) · <m> admin(s)"` (singular/plural aware) plus helper note steering admins to **Create user** for new members.
4. **Vitest** — `AdminUsersView.test.tsx` extended to **17** cases covering confirm accept/cancel branches, self-guard, and member summary.
5. **Desktop Playwright** — `tests/e2e/admin_users.spec.ts` (LUM-520): smoke-cred-gated table + member summary; dismiss-only confirm dialog (no live role mutation).
6. **Mobile Playwright** — `tests/e2e/admin_users_mobile.spec.ts` (LUM-545): **390×844** viewport, `chromium-smoke-shared-user` project; table + summary + no horizontal overflow; dismiss-only confirm dialog.
7. **Coverage matrix** — rows **2.2.10** (desktop admin users smoke) and **2.2.11** (mobile admin users smoke); catalog IDs registered in `scripts/feature-ids.json`.

## Alternatives considered

- **Route-mock Playwright** — rejected; admin panel smokes follow existing `smoke-auth` live-stack pattern (`admin_shell.spec.ts`).
- **Accept confirm in E2E** — rejected for shared smoke env; accept/cancel PATCH branches covered in Vitest; Playwright uses dismiss-only.

## Consequences

**Positive:** LUM-520 admin panel is operationally complete for household operators; mobile layout regressions on `/admin/users` are gated; ADR-113 Playwright revisit condition satisfied.

**Limits:** Playwright admin specs skip without `LUMOGIS_E2E_*` smoke credentials and when smoke user is not admin — CI path-gated via `E2E_REQUIRE_CREDS` / prove mode.

## Revisit conditions

- `guest` role UI when role ships (separate chunk).
- Parent **LUM-334** programme closure when all children Done.

## Linear linkage (Product OS)

- **LUM-520** — completion slice (this ADR).
- **LUM-545** — mobile E2E child; scope fully covered by § Decision item 6.
- **Parent LUM-334** — Chunk B complete; parent closure separate.

## Testing retrospective

| Layer | Command | Result |
|-------|---------|--------|
| Vitest (admin) | `npm test -- --run tests/features/admin/AdminUsersView.test.tsx` | **17 passed** |
| Vitest (full web) | `npm test -- --run` (lumogis-web) | **340 passed** |
| Coverage matrix | `make coverage-matrix-check` | **170/170 green** |
| Playwright desktop | `npm run e2e -- tests/e2e/admin_users.spec.ts` | **2 skipped** (no smoke creds locally) |
| Playwright mobile | `npm run e2e -- tests/e2e/admin_users_mobile.spec.ts` | **2 skipped** (no smoke creds locally) |

Accept/cancel role-change branches: Vitest. Live-stack Playwright: dismiss-only by design.

## Status history

- 2026-06-26: Shipped on `claude/persona-c-household-launch-ly74ed`; merged to `dev`.
- 2026-06-26: Finalised by `/record-retro`.
