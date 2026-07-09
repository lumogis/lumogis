# ADR-147: Admin SPA update-available banner (LUM-524)

**Status:** Finalised

**Created:** 2026-06-30

**Last updated:** 2026-06-30

**Decided by:** `/explore --headless` LUM-524; implemented and verified 2026-06-30

**Issue:** [LUM-524](https://linear.app/lumogis/issue/LUM-524)

**Related:** [ADR-123](123-lum-187-update-mechanism.md) (LUM-187 update mechanism — parent programme); [ADR-098](098-lum-185-backup-restore.md) (DR backup card pattern reused)

## Context

LUM-187 / ADR-123 shipped the read-only `GET /api/v1/admin/diagnostics/update-status` endpoint (`UpdateStatusResponse`, fail-soft) and operator `make update` / rollback scripts, but explicitly deferred the admin SPA banner to **LUM-524**. The remaining work is purely Lumogis Web (AGPL client): surface a discoverable, fail-soft "update available" indicator for the household operator and point them at the documented update path — with **no** in-browser update execution and **no** new backend endpoints.

## Decision

Render an **"Software updates" card inside `AdminSystemStatusView`** (`/admin/system-status`), the existing operator health-and-action surface. Fetch via `clients/lumogis-web/src/api/adminUpdateStatus.ts` (`fetchAdminUpdateStatus` → `client.getJson<UpdateStatusResponse>(…)`, mirroring `adminBackupStatus.ts`) consumed with `@tanstack/react-query` `useQuery` (**mount-once**: `refetchInterval: false`, `staleTime: 1h`, `refetchOnWindowFocus: false` — backend hits uncached GitHub per request). **Card shell** renders when data loads (mirror DR-backup card); **`role="alert"` styling** only when `checked && update_available` (dismissible per-version via `localStorage`). Up-to-date and fail-soft states use muted informational copy inside the card. Display current→latest version plus an optional `release_url` link. Degrade gracefully when `checked === false` — informational, never blocking. Query gated on stack-status success (same as backup card). No backend changes in this chunk; backend GitHub TTL cache deferred to a Linear child of LUM-187/LUM-524.

## Alternatives Considered

- **Global admin-shell banner in `AdminPage`** — best discoverability but adds an always-mounted query + app-shell touchpoint; kept as follow-up.
- **Banner in `AdminDiagnosticsView`** — acceptable but Diagnostics is read-only overview, not the operator-action surface.
- **Dedicated `/admin/updates` tab** — disproportionate to one boolean + version string.
- **Toast / blocking modal** — wrong notification class for a persistent, non-blocking informational state.

## Consequences

- **Easier:** Operators learn about new releases inside the panel they already use for stack/storage/backup health; one new api file + one changed view; seven Vitest cases cover available / up-to-date / `checked:false` / dismissal / fetch error.
- **Harder / foreclosed:** Visibility is tab-local (System status), not app-wide; a future shared banner / notification-inbox surface (e.g. alongside LUM-424) would supersede the card via a small refactor. Without backend TTL cache, multiple concurrent consumers can still hit GitHub rate limits — acceptable for v1.1 with mount-once fetch.

## Revisit conditions

- If tab-local visibility proves insufficient for operators, promote to a global admin-shell banner (Option 2).
- If LUM-424 (notification inbox) ships a general operator-alert surface, route update-availability through it instead of a standalone card.
- If `UpdateStatusResponse` gains fields (e.g. security severity), revisit card tone for security releases.

## Status history

- 2026-06-30: Draft created by `/explore --headless` LUM-524.
- 2026-06-30: Revised during `/review-plan --arbitrate` R1 — card shell when data loads; mount-once fetch; stack-status gating; backend cache deferred.
- 2026-06-30: Finalised by `/verify-plan` — implementation confirmed on `agent/lum-524`.
