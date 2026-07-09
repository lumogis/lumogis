# ADR-156: Member-facing audit log UI and enriched audit read API (LUM-197)

**Status:** Finalised
**Created:** 2026-07-06
**Last updated:** 2026-07-07
**Decided by:** /explore --headless LUM-197; finalised by /verify-plan (Composer)

## Context

Lumogis records action executions in an append-only Postgres `audit_log`, exposed historically only through an admin-only `AdminAuditView` at `/admin/audit` over `GET /api/v1/audit` (connector/action_type/limit; admin `as_user`; reverse). Household members need a filterable, paginated trust surface at `/audit` without admin access. ADR 019 established application-level structured audit with Postgres as the system of record; `audit_log.scope` is forward-compat scaffolding — every v1 row is `personal`.

## Decision

Extend the existing application-level audit read path (no new store, no triggers, no external framework):

1. **Backend:** `orchestrator/services/audit_taxonomy.py` derives stable namespaced `event_type` keys (`resource.subresource.verb`) from `action_name` + `connector`. `get_audit` / `count_audit` gain `after`/`before`, `event_type` (SQL-side reverse predicates with exact `IN` lists and an `action.executed` exclusion predicate), `offset`, and `scope` in `SELECT`. `GET /api/v1/audit` returns enriched `AuditEntryDTO` fields (`event_type`, `scope`, `source`, `description`) plus `total`/`limit`/`offset`.
2. **Web:** Member read-only `/audit` (top-level route, `MeSubshell` chrome, Settings nav link) with date presets, event-type chips, offset pagination, row expand, and privacy/cloud markers. `/admin/audit` keeps reverse + `as_user`; shared `AuditTable` + `api/audit.ts`.
3. **Phased emission:** Ingest/graph writers and stored `event_type` column deferred to **LUM-38**; refusal surface taxonomy alignment to **LUM-137**.

## Alternatives Considered

- **UI-only over current API** — cannot meet date/taxonomy/pagination acceptance. Rejected.
- **External audit framework** — immature/heavy; duplicates shipped subsystem. Rejected.
- **DB trigger audit** — rejected by ADR 019. Rejected.

Full detail: `.cursor/explorations/LUM-197-audit-log-ui.md`.

## Consequences

- **Easier:** member trust surface on a reviewed store; one read contract + taxonomy for LUM-38/LUM-137; AGPL Web + core only; no new infra.
- **Harder:** maintain taxonomy reverse-map as writers evolve; v1 scope filter UI is forward-compat only (shared/system disabled until writers exist); members can still call reverse API for own rows (UI hides button).
- **Future chunks must know:** filterable `event_type` must reverse-map to `action_name`/`connector` only until LUM-38 stored column; LUM-38 emission must use this vocabulary.

## Status history

- 2026-07-06: Draft created by /explore --headless LUM-197.
- 2026-07-07: Finalised by /verify-plan — implementation confirmed decision.
