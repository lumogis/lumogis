# ADR-107: Web UX — loading skeletons, error states, and service health (LUM-212 / 211 / 512 / 420)

**Status:** Finalised  
**Created:** 2026-06-19  
**Last updated:** 2026-06-19  
**Decided by:** as-shipped implementation (retrospective)  
**Finalised by:** /record-retro 2026-06-19 (Composer)  
**Plan:** none — Claude Code web batch (`claude/youthful-carson-84uh2i`)  
**Exploration:** `.cursor/explorations/web_ux_cluster_retro.md`  
**Draft mirror:** `.cursor/adrs/web-ux-cluster.md`  
**Builds on:** ADR-074 (LUM-178 stack health — admin surface), ADR-101 (LUM-160 document library)

## Context

Lumogis Web needed consistent loading and error UX across document library, audit log, entity detail, and chat; plus non-admin visibility into Ollama/Qdrant/graph degradation for chat (LUM-512). Work shipped on `claude/youthful-carson-84uh2i` without a formal plan/verify cycle.

## Decision

1. **LUM-212 — Loading primitives** — Shared `Skeleton` / `SkeletonText` / `LoadingPlaceholder` (`src/features/_shared/Skeleton.tsx`) with `role="status"`, `aria-busy`, reduced-motion tokens. Wired into `DocumentsPage`, `AdminAuditView`, `ChatPage` typing indicator, and `EntityCard`/`EntityCardPanel`.
2. **LUM-211 — Error surfaces** — `ErrorState` (`role="alert"`, plain language, optional Retry, `lumogis doctor` hint) and `AppErrorBoundary` (outer shell + inner around `<Routes>`). Document library, audit log, and entity detail use `ErrorState` with refetch/reload.
3. **LUM-512 — Service health** — `GET /api/v1/health` (`require_user`, non-admin) returns whitelisted `{overall, services:{ollama,qdrant,graph}}` via `services/user_health.py` (serve-stale ~10s TTL, `build_service_states()` without admin-only storage/model probes). Frontend: `useServiceHealth` (~20s poll), `ServiceDegradationBanner` on `ChatPage` (Ollama `role="alert"`; Qdrant/graph `role="status"`). Chat error paths call `refreshHealth()` immediately.
4. **LUM-420** — Playwright `chat-sidebar-mobile.spec.ts` for responsive sidebar collapse (mobile stack vs desktop side-by-side).

**Explicitly out of scope (documented on tickets):** LUM-511 ingest/transcription progress; send-disable when Ollama down (LUM-194); server-side KG-only retrieval fallback marker; cloud-LLM fallback action.

## Alternatives considered

- Reuse admin `GET /api/v1/admin/diagnostics/stack-status` from the web client — rejected (admin gate + sensitive fields).
- Inline per-page spinners only — rejected (inconsistent a11y and layout).

## Consequences

- Non-admin users see only three service ids; internal topology stays admin-only.
- Degradation UI is ready before orchestrator emits retrieval `degraded` markers (follow-up on LUM-512).
- `openapi.snapshot.json` must stay in sync when health contract changes.

## Revisit conditions

- Add send-disable or cloud fallback → coordinate with LUM-194 privacy-mode gates.
- Server emits KG-only/vector-only degraded retrieval → extend banner copy and chat stream metadata handling.
- New surfaces needing loading/error patterns → reuse `_shared` primitives (LUM-161, LUM-111 backlog).

## Linear linkage (Product OS)

- **LUM-212**, **LUM-211**, **LUM-512**, **LUM-420** — implementation complete on branch; `/linear-update apply-closure` after merge to `dev`.
- **LUM-511** — deferred (no code); remains Backlog.

## Testing retrospective

| Layer | Result |
| --- | --- |
| Vitest | **321 passed** (56 files) on branch worktree after `npm ci` |
| `tsc -b` + eslint | Clean after `npm run codegen` |
| Pytest health | **19 passed** (`test_api_v1_health.py`, `test_stack_status_service.py`, doctor subset filtered) in orchestrator container |
| Playwright | **Blocked here** — dev stack missing Caddy on `localhost` (`ERR_CONNECTION_REFUSED`); run `chat-degradation` + `chat-sidebar-mobile` with full compose + smoke creds before merge |

**Gaps:** Real container-kill degradation drive (handover §6 item 3); `make compose-test` full suite on merge host.

## Status history

- 2026-06-19: Finalised by `/record-retro` (web UX cluster, `claude/youthful-carson-84uh2i`).
