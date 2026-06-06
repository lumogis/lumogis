# ADR-074: Stack Health Dashboard — service status, storage, Ollama management

> Status: Active (numbering conflict)
> Last reviewed: 2026-06-06
> Verified against commit: 4c22088
> Notes: **`docs/decisions/074-lum-162-conversation-history-ui.md`** also claims **ADR 074** in its title. Resolve by renumbering one document and sweeping references. Filename prefixes **049–082** are already in use under `docs/decisions/` (duplicate clusters on **053**, **059**, **060**, **061**, **063**, **064**, **072**, **074**, plus **`065-lum-320-*.md`** through **`082-lum-433-search-overlay-public-ci.md`**). Pick a **non-colliding** new slug (for example **`083-*.md`**) when renumbering—coordinate with any **`034-linear-evidence-index.md`** / **046** / **072** rename in the same pass—see `docs/_librarian/docs-inventory.md`.

**Status:** Finalised
**Created:** 2026-06-01
**Last updated:** 2026-06-01
**Decided by:** /explore --headless (LUM-178); slice 1 implemented and verified 2026-06-01

## Context

Household operators have no single in-product view of stack health. The data exists but is scattered: `GET /admin/health` (raw counts, 503 on Postgres down), `GET /api/v1/admin/diagnostics` (curated read-only admin DTO), `make doctor` v1/v2 JSON (host-side CLI), and the legacy `/settings` dashboard (Ollama discovery/pull/delete). The defining constraint is that the **orchestrator container has no Docker socket**, so it can ping stores but cannot report container run-state, restart counts, or per-volume disk usage. The only component holding `/var/run/docker.sock` is the `stack-control` sidecar (token-auth, allowlisted `docker compose` ops, internal-network only). LUM-322 already decided to extend the admin health surface rather than add a parallel in-process `orchestrator.doctor` (ADR-061 gates).

## Decision

Build the dashboard as a **hybrid, sliced** feature in core (AGPL):

- **Slice 1 (shipped):** sibling admin sub-resource `GET /api/v1/admin/diagnostics/stack-status` returning `StackStatusResponse` on the existing `admin_diagnostics` router (same auth/audit posture as other diagnostics read-only routes; does **not** embed into `AdminDiagnosticsResponse`).
- **stack-control:** read-only `GET /status` returning `docker compose ps` + `docker system df` JSON, proxied by the orchestrator via `RESTART_SECRET` / `X-Lumogis-Restart-Token` with TTL cache and single-flight per worker.
- **Lumogis Web:** `AdminSystemStatusView` at `/admin/system-status` (services, storage bars, Ollama list read-only).
- **Slice 2 (deferred):** in-SPA Ollama pull/delete via existing unprefixed `/settings/ollama-*` admin routes.
- **Slice 3 (deferred, blocked by LUM-174):** storage-threshold inbox notifications.

The service-status JSON contract is **runtime-agnostic** (`runtime_kind`, allowlisted `runtime_detail`) so LUM-396 can supply process-manager rows without Docker-shaped top-level fields.

## Alternatives Considered

- **Orchestrator-only in-process (no socket):** lowest effort but cannot show which container is actually down — only that a port pings. Becomes slice 1 partial signal only, not the whole feature.
- **New dedicated `docker-socket-proxy` sidecar:** duplicates the privileged-socket boundary `stack-control` already owns.
- **Bundle an external monitor (Beszel/Netdata/cAdvisor+Prometheus+Grafana):** separate UI, no Ollama management, off-persona.
- **Surface `make doctor --json` per request:** too slow for live status; complements the panel as an optional deep scan later.

Full detail: `.cursor/explorations/LUM-178-stack-health-dashboard.md`.

## Consequences

**Easier:** Single in-product operator surface; reuse of diagnostics pings, Ollama client, and stack-control trust boundary; no new Docker service.

**Harder / constrained:** `docker system df` is expensive — TTL cache, sidecar df lock, and explicit HTTP/subprocess timeouts required; wire contract must stay runtime-agnostic for LUM-396.

**Future chunks must know:** LUM-211 consumes `StackStatusServiceItem` / `meta.overall_status`; LUM-187 shares admin shell patterns; LUM-342 owns restart UX before dashboard restart buttons; LUM-174 unblocks slice 3 alerts.

## Revisit conditions

- LUM-396 bundled/no-Docker track: replace stack-control `compose ps` source while keeping the same DTO.
- If `docker system df` latency remains unacceptable with caching, revisit statvfs-only storage or a background sampler.
- If a second component needs the Docker socket, revisit consolidating read-only socket access.
- Operator demand for in-panel restart: reconcile with LUM-342 before widening stack-control beyond read-only `/status`.

## Status history

- 2026-06-01: Draft created by /explore --headless (LUM-178)
- 2026-06-01: Revised during /review-plan --arbitrate R1 — sibling sub-resource wording, unprefixed Ollama paths for slice 2
- 2026-06-01: Finalised by /verify-plan — slice 1 implementation confirmed (LUM-178)
