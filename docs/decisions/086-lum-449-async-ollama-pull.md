# ADR-086: Async Ollama pull with progress (LUM-449)

**Status:** Finalised
**Created:** 2026-06-08
**Last updated:** 2026-06-08
**Decided by:** /explore LUM-449; finalised /verify-plan LUM-449

## Context

LUM-423 shipped blocking Ollama pull in the admin System status SPA via `POST /settings/ollama-pull`. Pulls can run up to 7200s with `stream=false`, holding a FastAPI worker thread and showing no download progress. LUM-449 addresses threadpool exposure and operator UX with async pull + progress.

## Decision

Adopt **Postgres-tracked pull jobs** (`ollama_pull_jobs`, migration `030`) with **HTTP 202 + poll**:

- `POST /settings/ollama-pull/async` → **202** + `job_id`
- `GET /settings/ollama-pull/jobs/{job_id}` — poll progress
- `GET /settings/ollama-pull/jobs/active` — tab-refresh resume
- **BackgroundTasks** worker streams Ollama `/api/pull` with `stream: true`, parses NDJSON into the job row (throttled updates), runs post-pull hooks (`finalize_ollama_pull`: LibreChat sync, Qdrant init, `qdrant_init_warning`) on success
- **409** when another job is `pending|running`
- SPA (`AdminSystemStatusView`) shows progress bar; sync `POST /settings/ollama-pull` **unchanged** for legacy HTML dashboard

SPA routes promoted to **`/api/v1/admin/ollama/*`** by **LUM-451** (ADR-088); legacy `/settings/ollama-*` delegates remain for the HTML dashboard.

## Alternatives Considered

- **SSE proxy** — connection-bound; poor tab-refresh UX (see exploration).
- **batch_queue / Celery** — overkill for rare admin pulls.
- **In-memory job map** — lost on orchestrator restart.

## Consequences

**Easier:** Real progress UX; 202 frees request thread; tab refresh tolerant; mirrors `deduplication_runs` pattern.

**Harder:** LUM-450 Playwright must target async poll contract; legacy dashboard still blocking.

**Future chunks:** LUM-451 migrates routes to typed OpenAPI; optional SSE (follow-up).

## Revisit conditions

- Multi-worker uvicorn by default — Postgres jobs still appropriate.
- Ollama native pull-status API — simplify parser.
- LUM-451 shipped — async contract on `/api/v1/admin/ollama/*` only (legacy delegates unchanged).

## Status history

- 2026-06-08: Draft created by /explore LUM-449
- 2026-06-08: Finalised by /verify-plan LUM-449 — implementation confirmed
