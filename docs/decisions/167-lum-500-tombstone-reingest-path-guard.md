# ADR 167: LUM-500 — tombstone purge retry must not wipe re-ingested path vectors

**Status:** Finalised  
**Created:** 2026-07-14  
**Last updated:** 2026-07-14  
**Decided by:** as-shipped implementation (retrospective)  
**Finalised by:** /record-retro 2026-07-14 (Composer)  
**Plan:** none — shipped via Cursor branch `critical-bug-investigation-e4c1` before formal plan / verify for this slice  
**Exploration:** `.cursor/explorations/purge_tombstone_reingest_path_guard_retro.md`  
**Draft mirror:** `.cursor/adrs/purge_tombstone_reingest_path_guard.md`  
**Parent ADRs:** `docs/decisions/103-lum-160-document-purge-followups.md` (LUM-500 tombstone), `docs/decisions/109-lum-160-sparse-qdrant-reingest-clear.md` (re-ingest path clears)

## Context

**LUM-500** (**ADR 103**) introduced `purged_documents` tombstones and retry of failed Qdrant/graph arms when `DELETE /api/v1/documents/{id}` is called again after a partial purge. Personal document chunk point IDs are keyed by **`(user_id, file_path)`** (**LUM-505**). When Postgres delete succeeds but Qdrant fails, the `file_index` row is gone and a partial tombstone remains.

If the user **re-ingests the same on-disk path** before the tombstone retry completes, a **new** `file_index` row (new `document_id`) reclaims the path. A tombstone retry that runs the normal path-wide Qdrant `delete_where` sweep would delete the **live** document's vectors.

Fix landed on **`dev`** via **`cursor/critical-bug-investigation-e4c1`** (commit **`82b8279f6`**, merge **`0f04adb7f`**, 2026-07-14) without a Product OS plan/verify loop.

## Decision

1. **`_path_reclaimed_by_other_document`** — before retrying store arms for a tombstone, check whether another live personal `file_index` row owns the same `(user_id, file_path)` with a different `document_id`.
2. **`skip_path_scoped`** — when the path is reclaimed, **`_run_qdrant_arm`** and **`_run_graph_arm`** skip path-wide deletes (payload filter + legacy deterministic chunk IDs; graph path delete). **`published_from`** projection deletes and orphan-entity Qdrant cleanup for the tombstoned document still run.
3. **`share_document_lock(document_id)`** on tombstone retry entry in **`_handle_missing_row`** — serialise against concurrent share/re-ingest on the same document id.
4. **Regression test** — **`test_purge_tombstone_retry_skips_path_when_reingested`** in **`orchestrator/tests/test_document_purge.py`**.

## Alternatives considered

- **Always run path-wide delete on tombstone retry** (pre-fix) — rejected; destroys re-ingested vectors at the same path.
- **Key tombstones by `file_path` instead of `document_id`** — rejected; breaks LUM-500 owner-keyed tombstone contract and UI retry semantics.
- **Defer to background sweeper only** — rejected; user-visible data loss on manual retry before any sweeper exists for documents (**ADR 103** deferred document sweeper).

## Consequences

**Positive:** Partial purge + re-ingest + tombstone retry no longer wipes live indexed content at the reclaimed path.

**Limits:** Unit/mock tests only; document background reconciliation sweeper (if added) must apply the same path-reclaimed guard.

## Revisit conditions

- **`document_purge.py` refactor** — preserve `skip_path_scoped` semantics.
- **Document purge sweeper** (ADR 103 revisit) — reuse `_path_reclaimed_by_other_document` before path-scoped deletes.
- Operator reports of missing vectors after purge retry — add live Qdrant integration test.

## Linear linkage (Product OS)

- **LUM-500:** tombstone programme — post-ship correctness (comment via **`/linear-update comment LUM-500`**, not a new issue required).
- **LUM-160:** parent document-library programme.
- **New issue needed:** no

## Testing retrospective

`AUTH_ENABLED=false ../.venv/bin/python -m pytest tests/test_document_purge.py -q` — **22 passed** on `dev` @ `9405368c2`. No integration test for this race yet.

## Status history

- **2026-07-14:** Finalised by **`/record-retro`** — merge **`0f04adb7f`** on **`dev`**.
