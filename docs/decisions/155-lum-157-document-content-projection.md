# ADR-155: Shared-document content projection mechanism (LUM-157)

**Status:** Finalised
**Created:** 2026-07-06
**Last updated:** 2026-07-06
**Decided by:** /explore (Opus 4.8); finalised by /verify-plan (Opus 4.8)

## Context

Sharing a personal document with the household must make its **content** retrievable by other members (search + document-chat), not just show it in the library. `services/projection.py::project_file` previously wrote only the Postgres `file_index` projection and never mirrored the document's chunks into Qdrant, so shared documents were content-invisible to other members. The `VectorStore` **port** exposes no `retrieve`/`scroll` (why `project_note`/`project_entity` re-embed), but the Qdrant **adapter** already exposes stored vectors via `scroll_collection(with_vectors=True)`. The decision: how to get a shared document's chunks into Qdrant at `scope='shared'`.

## Decision

**Reuse existing vectors via the adapter's `scroll_collection(with_vectors=True)` and upsert shared-scoped copies.** On share, the background job scrolls the owner's chunks for the document (`user_id` + a `file_path` filter on `scroll_collection`), and for each upserts a shared-scoped projection point — deterministic id (`projection_point_id("documents", f"{src_id}:{chunk_ix}", "shared")`), **the retrieved vector reused (no re-embed)**, payload `{scope:"shared", user_id: owner, published_from: src_id, file_path, chunk_ix, text}`. On unshare (and inside `purge_document`), remove them with `delete_where("documents", {"must":[{"key":"published_from","match":{"value":src_id}},{"key":"user_id","match":{"value":owner}},{"key":"scope","match":{"value":"shared"}}]})`. No `VectorStore` Protocol change; the vector-reuse stays inside the Qdrant adapter.

## Alternatives Considered

- **Re-embed via scroll** — same shape but re-embeds each chunk; needless embedding cost when the vector is already retrievable. Kept only as a per-chunk fallback when a stored vector is missing. (See exploration.)
- **In-place `set_payload` scope flip** — mutates the owner's canonical personal points and breaks the source/projection `published_from` duality every other resource uses; needs a new port method. Rejected.
- **Re-chunk + re-embed from the source file** — expensive; risks chunk-boundary drift. Rejected.
- **Query-time join / separate collection** — impossible (payload-only filter) / against Qdrant single-collection multitenancy guidance. Ruled out.

Full detail: `.cursor/explorations/LUM-157-document-content-projection.md`.

## Consequences

**Easier:** shared documents are genuinely retrievable (search + document-chat) with a cheap, reversible, pattern-consistent mechanism; no Protocol churn; clean unshare/purge via `delete_where(published_from)`.
**Harder / cost:** shared documents cost ≈2× vector storage (duplicated points) — the recommended Qdrant multitenancy pattern, accepted. `scroll_collection` gained a `file_path` filter (adapter-only) for selectivity at scale.
**Future chunks must know:** shared document chunks are **projection copies** keyed by `published_from` (not in-place scope flips); anything that deletes/re-ingests a document must also reconcile the shared projection (re-ingest re-projects via `reproject_shared_on_reingest`; `purge_document` deletes the shared points). Entity/graph sharing of a shared doc is a separate follow-up.

### As-built notes (verify-plan, 2026-07-06)

- **Partial success is first-class.** `_project_file_chunks` returns `(projected, failed)`; a per-chunk upsert failure increments `failed` rather than aborting, so the `share_document` job reports an honest `partial` stage (never a false `shared`). Unit + handler tests pin this.
- **Concurrency.** `services/share_lock.py` serialises a share/unshare against a concurrent re-ingest re-projection of the same `document_id` via a Postgres advisory lock; it degrades to no-lock (projection is idempotent) if a connection cannot be acquired.
- **Postgres.** Migration 046 made `file_index_user_path_uniq` partial (`WHERE published_from IS NULL`); `ingest.py`'s `ON CONFLICT (user_id, file_path)` names that predicate so a source ingest still upserts.
- **Purge.** `purge_document`'s `_run_qdrant_arm` issues an explicit scoped `published_from` delete for the shared chunks (belt-and-suspenders alongside the `{user_id,file_path}` sweep).
- **Live proof.** `orchestrator/tests/integration/test_document_share_projection_live.py` runs against a real Qdrant + Postgres and validates: (1) a second member's `visible_qdrant_filter` retrieves the shared chunks after share (and does not before); (2) unshare removes only the shared copies; (3) purging the source leaves no shared orphans.

### Review-round hardening (LUM-157 security + code review)

- **Availability.** The user-facing share path (`/api/v1/documents/{id}/publish`) always runs the projection as the `share_document` background job (202 + `job_id`). The raw generic route (`/api/v1/files/{id}/publish`) keeps its fast inline path for small documents but routes any document with more than `LUMOGIS_SHARE_INLINE_MAX_CHUNKS` (default 50) chunks to the same background job, so a large share never blocks the request thread.
- **No stale shared points.** `project_file_with_status` (and the re-ingest re-projection) **unproject-then-project** so the shared set exactly mirrors the current source chunks — re-sharing after a re-ingest with fewer chunks can never leave orphaned shared points at removed indices.
- **Unique projection ids.** Chunks whose payload lacks an integer `chunk_index` are skipped (counted as failed → honest partial) instead of collapsing onto a `"{src}:None"` point id.
- **Scope gate.** Content-chunk projection only fires for `target_scope == "shared"` (system-scoped rows are produced by system writers; personal document content is never duplicated into system scope).
- **Owner invariant.** `project_file_with_status` asserts the source row's `user_id` equals the acting `actor.user_id`, so a future caller can never project another member's content.

## Revisit conditions

- If duplicated-point storage or write-amplification becomes a measured problem at household scale → revisit a `set_payload`-based in-place scheme behind a new `VectorStore` Protocol method.
- If a future embedder/vector-format migration makes reusing stored vectors unsafe → fall back to re-embed via scroll (Option B).
- If more than the documents resource needs vector reuse → promote `scroll`/`retrieve` into the `VectorStore` Protocol rather than keeping it adapter-only.

## Status history
- 2026-07-06: Draft created by /explore (recommendation: reuse vectors via scroll_collection(with_vectors=True); High confidence).
- 2026-07-06: **PoC PASSED** against a real Qdrant engine — scroll-with-vectors reuse, member retrieval, unshared-doc isolation, and delete_where(published_from) cleanup all validated. Confidence raised to High; mechanism ready to implement.
- 2026-07-06: **Finalised by /verify-plan** — implementation confirmed the decision; live two-user retrieval, scoped-delete, and no-orphan-on-purge integration tests pass against real Qdrant + Postgres.
