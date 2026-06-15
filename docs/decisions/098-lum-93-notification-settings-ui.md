# ADR-098: Notification settings UI — editable per-type × per-channel matrix (LUM-93)

**Status:** Finalised
**Created:** 2026-06-14
**Last updated:** 2026-06-14
**Decided by:** `/explore --headless LUM-93`; implementation verified `/verify-plan --headless LUM-93`
**Linear issue:** LUM-93
**Exploration:** `.cursor/explorations/LUM-93-notification-settings-ui.md`
**Parent architecture:** [ADR 077](077-lum-189-notification-architecture.md) (LUM-189) — backend dispatcher, prefs store, channel adapters

## Context

ADR 077 locked the notification backend and sequenced implementation as LUM-93 → LUM-144 → LUM-28 → LUM-174. LUM-93 ships **chunk 1**: Postgres preference tables, in-process dispatcher with ntfy / Web Push / in-app SSE channel adapters, user and admin preference HTTP APIs, producer migration off direct `get_notifier()` / Web Push hooks, and Lumogis Web UI for editing routing preferences.

The open UI decision for LUM-93 was how to render the read-only Me Notifications status table into an **editable** per-notification-type × per-channel preference editor within the framework-light Lumogis Web SPA (React 18 + react-query, no component library).

## Decision

1. **Backend (ADR 077 chunk 1):** Implement as specified in ADR 077 — `services/notifications/` dispatcher, `NotificationChannel` protocol, migration `031`, `GET/PATCH /api/v1/me/notification-preferences`, admin tier-policy API, producer migration (`signal_processor`, `signals/digest`, `ROUTINE_ELEVATION_READY` hook bridge), SSE dedup via removal of legacy notification handlers from `events.register_hooks()`.

2. **Web UI (LUM-93-specific):** Render the editable preferences surface as a **native HTML `<table>` matrix** — rows = `NotificationType`, columns = `ChannelId` — with labelled native checkboxes per cell, header bulk toggles (`aria-checked="mixed"` for partial state), and out-of-tier channels disabled with explanation. Reads from `GET /api/v1/me/notification-preferences`; toggles use react-query **optimistic PATCH** (`onMutate` snapshot → `setQueryData` → rollback on error → `invalidateQueries` on settle with `isMutating` guard). Implemented in `MeNotificationsView.tsx` + `NotificationPrefsEditor.tsx` + `api/notificationPreferences.ts` — **no new npm dependency**.

3. **Read-only status façade:** `GET /api/v1/me/notifications` unchanged; ntfy credentials remain under Connectors with deep-link preserved in copy.

## Alternatives Considered

- **ARIA `role="grid"` widget:** over-engineered for single-toggle cells — rejected.
- **Channel-first grouped panels:** obscures per-event routing — reserved as responsive fallback only.
- **UI/component library:** disproportionate for one settings table — rejected.
- **Re-opening ADR 077 backend:** rejected — implemented per ADR 077.

Full exploration detail: `.cursor/explorations/LUM-93-notification-settings-ui.md`.

## Consequences

**Easier:** Household operators can edit per-type × per-channel routing in Lumogis Web; signals/digest/routine elevation flow through one dispatcher; Web Push OR-collapse seeder preserves prior opt-in posture; `ApprovalsPage.tsx` SSE `routine_elevation_ready` invariant preserved.

**Harder:** Tier-policy admin changes are instance-wide; orphan sparse rows after tier shrink show `effective=false`; Web Push skips unmapped notification types until LUM-28 templates ship.

**Downstream:** LUM-144 (quiet-hours UI), LUM-28 (`ACTION_EXECUTED` templates), LUM-174 (inbox), LUM-424 (`security_alert` producer), LUM-221 (timezone from profile), admin tier-policy SPA — out of scope for LUM-93.

## Status history

- 2026-06-14: Draft created by `/explore --headless LUM-93`.
- 2026-06-14: Finalised by `/verify-plan --headless LUM-93` — implementation confirmed; UI pattern + ADR 077 chunk 1 backend shipped.
