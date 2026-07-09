# ADR-110: Ingest job progress UX (LUM-511 Phases A–B)

**Status:** Finalised  
**Created:** 2026-06-21  
**Last updated:** 2026-06-21  
**Decided by:** /explore LUM-511; implemented and verified 2026-06-21

## Context

LUM-212 delivered loading skeletons and coarse `indexing | indexed | failed` document status (ADR-101). Operators and household users uploading or re-ingesting documents had no per-stage visibility (extract, chunk, embed, graph) and no multi-file batch counter during library uploads.

The orchestrator already enqueued `ingest_upload` / `ingest_watch_file` jobs on `user_batch_jobs` (ADR-025) and returned `job_id` for re-ingest, but `POST /api/v1/ingest/upload` discarded the enqueue id and `ingest_file` had no progress callbacks. Lumogis Web maintained one authenticated SSE connection to `GET /api/v1/events` and polled document lists while rows were `indexing`.

## Decision

Ship **Phases A–B** of LUM-511:

1. **Postgres-backed progress** on `user_batch_jobs` (`progress_stage`, `progress_pct`, `progress_message`; migration **035**).
2. **Poll endpoints:** `GET /api/v1/ingest/jobs/{job_id}`, `GET /api/v1/ingest/batches/{batch_id}` (aggregate counters only — **no server `total`**).
3. **SSE:** `ingest_progress` events on the existing global bus via `enqueue_user_sse`; payload **byte-identical** to poll JSON (`UPDATE … RETURNING` before emit).
4. **Upload contract:** `POST /api/v1/ingest/upload` returns `{ status, file_id, job_id }`; optional `X-Lumogis-Batch-Id` stored in job payload.
5. **Worker stages:** `ingest_file` fires `extracting` → `chunking` → `embedding` → `graph` (when `get_graph_mode() != "disabled"`) → `done` via batch wrapper; terminal `failed` only when job becomes `dead` (not on retry).
6. **Web:** `DocumentUploadPanel` (multi-file loop + client-owned denominator), `IngestProgressBar`, detail progress for re-ingest / `in_flight_job_id`.

**Deferred (explicit out of scope for this ADR):**

- **Phase C** — async capture transcribe (`transcription_progress` / poll) → separate Linear child under LUM-511.
- **Phase D** — KG route spinner → LUM-196.
- Dedicated per-job SSE URL; Redis replay buffers; server-side batch `total`.

## Alternatives considered

- **Per-ingest SSE URL** — rejected for v1 (extra connections; duplicates global bus).
- **Poll-only without SSE** — rejected (ignores established invalidation path).
- **Server `total` in batch summary** — rejected (racy with sequential client upload loop).

## Consequences

- `batch_queue._run_one_tick` passes `job_id` to all handlers; ingest kinds call progress helpers on success/failure.
- OpenAPI snapshot + web codegen updated when routes ship.
- Clients must treat SSE as **best-effort** (stuck sweeper marks `dead` without progress SSE; poll converges).
- Fresh upload progress lives in upload panel only until list rows carry `job_id` on detail open (v1 intentional gap).

## Revisit conditions

- Batch uploads regularly exceed ~50 parallel files and SSE backpressure appears.
- Product mandates ticket literal `GET /api/v1/ingest/{id}/progress`.
- LUM-196 ships — add graph route spinner (web-only).

## Status history

- 2026-06-21: Draft created by `/explore` LUM-511 (full ticket scope including transcription).
- 2026-06-21: Finalised by `/verify-plan` — scope locked to Phases A–B as implemented; Phase C/D deferred per plan.
