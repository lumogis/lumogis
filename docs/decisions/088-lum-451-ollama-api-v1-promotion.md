# ADR-088: Ollama admin API v1 promotion (LUM-451)

**Status:** Finalised
**Created:** 2026-06-08
**Last updated:** 2026-06-08
**Decided by:** /create-plan LUM-451; finalised /verify-plan LUM-451

## Context

LUM-423 shipped Ollama discovery, pull, and delete in the admin System status SPA on legacy unprefixed `/settings/ollama-*`. LUM-449 added async pull with Postgres job tracking and stable poll JSON (ADR-086). The SPA still called legacy paths, which are not in the typed OpenAPI surface and bypass the `admin_diagnostics`-style v1 contract.

## Decision

Promote **SPA-facing** Ollama admin operations to **`/api/v1/admin/ollama/*`** with Pydantic `response_model`s and OpenAPI snapshot coverage:

| Method | Path |
|--------|------|
| GET | `/api/v1/admin/ollama/discovery` |
| POST | `/api/v1/admin/ollama/pull/async` (202 + `job_id`) |
| GET | `/api/v1/admin/ollama/pull/jobs/active` |
| GET | `/api/v1/admin/ollama/pull/jobs/{job_id}` |
| POST | `/api/v1/admin/ollama/delete` |

Shared logic lives in **`services/admin_ollama.py`**; **`routes/admin_ollama.py`** is the typed façade; legacy **`/settings/ollama-*`** handlers in **`routes/admin.py`** remain as **thin delegates** (including sync blocking `POST /settings/ollama-pull` for the HTML dashboard only).

**No** `POST /api/v1/admin/ollama/pull` (sync) on the typed surface — zero SPA consumers; sync pull retires with the HTML dashboard.

Discovery v1 uses `response_model_exclude_none=True` so optional fields match legacy JSON parity (`test_v1_discovery_json_matches_legacy`).

## Alternatives Considered

- **410 Gone on legacy immediately** — breaks HTML dashboard; rejected per Q1.
- **Promote sync pull to v1** — rejected (Thomas Q2 override); no typed consumer.
- **Duplicate job logic in v1 router** — rejected; v1 delegates to `ollama_pull_jobs` helpers.

## Consequences

**Easier:** Lumogis Web uses typed paths behind existing Caddy `/api/*` proxy; OpenAPI guard + auth path list include v1 routes; poll contract unchanged for LUM-450 Playwright.

**Harder:** Dual-route maintenance until HTML dashboard retirement; legacy and v1 must stay delegate-identical.

**Future chunks:** Retire `/settings/ollama-*` when `orchestrator/dashboard/index.html` is removed; optional `openapi-breaking-check` triage on snapshot growth.

## Revisit conditions

- HTML dashboard removed — drop legacy aliases and sync pull in a follow-up chunk.
- Poll JSON shape change — requires ADR-086 amendment and LUM-450 e2e update.

## Status history

- 2026-06-08: Planned by /create-plan LUM-451 (P3 child of LUM-423)
- 2026-06-08: Finalised by /verify-plan LUM-451 — implementation confirmed
