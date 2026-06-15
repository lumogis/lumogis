# ADR-074: Stack Health Dashboard — service status, storage, Ollama management

> Status: Active (numbering conflict)
> Last reviewed: 2026-06-14
> Verified against commit: a36f022
> Notes: **`docs/decisions/074-lum-162-conversation-history-ui.md`** also claims **ADR 074** in its title; **[ADR 085](085-lum-439-conversation-put-upsert-fix.md)** amends **074-lum-162** as canonical **ADR 074** — renumber **this** file to **`098-lum-178-stack-health-dashboard.md`** in a coordinated pass (**096** is **LUM-477** cold-start resync; **097** is **LUM-470** pip hash-pinning). Filename prefixes **049–097** are already in use under `docs/decisions/` (duplicate clusters on **053**, **059**, **060**, **061**, **063**, **064**, **072**, **074**, plus **`065-lum-320-*.md`** through **`097-lum-470-pip-dependency-hash-pinning.md`**). Pick a **non-colliding** new slug (for example **`098-lum-178-*.md`**) when renumbering—coordinate with any **`034-linear-evidence-index.md`** / **046** / **072** rename in the same pass—see `docs/_librarian/docs-inventory.md`.

**Status:** Finalised
**Created:** 2026-06-01
**Last updated:** 2026-06-08
**Decided by:** /explore --headless (LUM-178); slice 1 implemented and verified 2026-06-01

## Context

Household operators have no single in-product view of stack health. The data exists but is scattered: `GET /admin/health` (raw counts, 503 on Postgres down), `GET /api/v1/admin/diagnostics` (curated read-only admin DTO), `make doctor` v1/v2 JSON (host-side CLI), and the legacy `/settings` dashboard (Ollama discovery/pull/delete). The defining constraint is that the **orchestrator container has no Docker socket**, so it can ping stores but cannot report container run-state, restart counts, or per-volume disk usage. The only component holding `/var/run/docker.sock` is the `stack-control` sidecar (token-auth, allowlisted `docker compose` ops, internal-network only). LUM-322 already decided to extend the admin health surface rather than add a parallel in-process `orchestrator.doctor` (ADR-061 gates).

## Decision

Build the dashboard as a **hybrid, sliced** feature in core (AGPL):

- **Slice 1 (shipped):** sibling admin sub-resource `GET /api/v1/admin/diagnostics/stack-status` returning `StackStatusResponse` on the existing `admin_diagnostics` router (same auth/audit posture as other diagnostics read-only routes; does **not** embed into `AdminDiagnosticsResponse`).
- **stack-control:** read-only `GET /status` returning `docker compose ps` + `docker system df` JSON, proxied by the orchestrator via `RESTART_SECRET` / `X-Lumogis-Restart-Token` with TTL cache and single-flight per worker.
- **Lumogis Web:** `AdminSystemStatusView` at `/admin/system-status` (services, storage bars, Ollama list read-only).
- **Slice 2 (shipped):** in-SPA Ollama pull/delete via existing unprefixed `/settings/ollama-*` admin routes; registry-alias and embedding badges; `GET /settings/ollama-discovery` extended with `embedding_model` and `default_model` (see `.cursor/adrs/LUM-423-ollama-admin-actions.md`); `POST /settings/ollama-pull` returns nullable `qdrant_init_warning` when embedding pull succeeds but Qdrant collection init fails (LUM-452).
- **Slice 2b (shipped, LUM-449):** SPA uses async pull — `POST /settings/ollama-pull/async` (202 + `job_id`), `GET /settings/ollama-pull/jobs/{job_id}` poll, `GET /settings/ollama-pull/jobs/active` for tab refresh; Postgres `ollama_pull_jobs`; progress bar in `AdminSystemStatusView`. Legacy sync `POST /settings/ollama-pull` unchanged for HTML dashboard.
- **Slice 2c (shipped, LUM-451):** Lumogis Web SPA calls typed **`/api/v1/admin/ollama/*`** (discovery, async pull, job poll/active, delete); shared `services/admin_ollama.py`; legacy `/settings/ollama-*` thin delegates for HTML dashboard; no v1 sync pull — see [ADR 088](088-lum-451-ollama-api-v1-promotion.md).
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
- 2026-06-08: Slice 2 planned and implemented (LUM-423) — Ollama pull/delete in `AdminSystemStatusView`; discovery response extended with `embedding_model` + `default_model`
- 2026-06-08: LUM-452 — `qdrant_init_warning` on pull response + admin UI warning when Qdrant init fails after embedding pull
- 2026-06-08: LUM-449 — async Ollama pull jobs + SPA progress bar (poll contract stable for LUM-450/LUM-451)
- 2026-06-08: LUM-451 — SPA migrated to `/api/v1/admin/ollama/*`; legacy `/settings/ollama-*` delegates retained (ADR-088)
