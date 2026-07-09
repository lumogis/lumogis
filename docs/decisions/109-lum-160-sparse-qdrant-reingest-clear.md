# ADR 109: LUM-160 — sparse Qdrant chunk clear on document re-ingest (as-shipped)

**Status:** Finalised
**Created:** 2026-06-21
**Last updated:** 2026-06-21
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-06-21 (Composer)
**Plan:** none — shipped via Cursor agent branch before formal plan / verify for this slice
**Exploration:** `.cursor/explorations/ingest_sparse_qdrant_reingest_retro.md`
**Draft mirror:** `.cursor/adrs/ingest_sparse_qdrant_reingest.md`
**Linear:** [LUM-160](https://linear.app/lumogis/issue/LUM-160/document-library-ui-list-status-delete-and-re-ingest-ingested)

## Context

**LUM-160** (**ADR 101**) and follow-on purge work (**ADR 103**, **ADR 104**) established document library delete/re-ingest and Qdrant cleanup via deterministic chunk point IDs. Prior orphan-chunk fixes on **`dev`** (**`a4361bf4d`**, **`cursor/critical-bug-investigation-4aec`**, **`cursor/critical-bug-investigation-665b`**) cleared stale vectors when re-ingest **shrunk** contiguous indices `0..chunk_count-1`.

With **`INJECTION_ACTION=block_ingest`** (**ADR 039**), **`_ingest_chunked_text`** can skip early chunks while writing later ones — **`file_index.chunk_count`** reflects **written** chunks, not the highest Qdrant index. Force re-ingest that only deleted `document_chunk_point_id(user, path, j)` for `j in range(old_chunk_count)` left **sparse** vectors behind; stale text could surface in auto-RAG (**ADR 059** / **ADR 106**) and document chat (**ADR 101** / **LUM-175**).

Fix landed on **`dev`** via **`cursor/critical-bug-investigation-3f11`** (commit **`6819063a0`**, merge **`52105b303`**, 2026-06-20) without a Product OS plan/verify loop.

## Decision

1. **`orchestrator/services/ingest.py::_delete_document_vectors_for_path`** — before re-ingest when an existing index row is present, call **`VectorStore.delete_where`** on collection **`documents`** with payload filter **`user_id` + `file_path`** (personal **`ingest_file`** and external **`ingest_external_document`** paths).
2. **Legacy fallback** — after **`delete_where`**, still attempt deterministic **`vs.delete(id=point_id_for_chunk(i))`** for `i in range(chunk_count_fallback)` so pre-payload-filter points are cleared.
3. **Call sites** — invoke the helper at the start of **`ingest_file`** and **`ingest_external_document`** re-ingest (including when updated content extracts zero chunks).
4. **Regression tests** — **`orchestrator/tests/test_ingest_orphan_chunks.py`**: existing force-reingest tests assert **`delete_where`**; new **`test_ingest_file_reingest_uses_delete_where_for_sparse_indices`** pins sparse-index behaviour.

## Alternatives considered

- **Sequential ID loop only** (pre-fix) — rejected; misses sparse indices when **`block_ingest`** skipped early chunks.
- **`delete_where` only, no ID fallback** — rejected; legacy points may predate payload-indexed deletes.
- **Defer to purge sweeper only** (**ADR 104**) — rejected; re-ingest must not leave stale vectors reachable before the next sweep.

## Consequences

**Positive:** Force re-ingest and external-document refresh clear all document vectors for a path regardless of sparse chunk indices; aligns with **`document_purge`** payload-filter deletes (**ADR 101** amendment / **LUM-501**).

**Limits:** Unit mocks only; no compose round-trip against live Qdrant with **`block_ingest`** enabled.

## Revisit conditions

- **`ingest_file`** / **`ingest_external_document`** refactor — preserve **`delete_where` + legacy ID fallback** before writing new chunks.
- **`VectorStore.delete_where`** semantics or payload schema change — re-run **`test_ingest_orphan_chunks.py`**.
- Reports of ghost citations after re-ingest — add integration test with real Qdrant + **`INJECTION_ACTION=block_ingest`**.

## Linear linkage (Product OS)

- **LUM-160:** parent document-library / ingest programme — post-ship correctness (comment via **`/linear-update`**, not a new issue required).
- **New issue needed:** no

## Testing retrospective

**`/tmp/lumogis-pytest-venv/bin/python -m pytest orchestrator/tests/test_ingest_orphan_chunks.py -v`** (temp venv, **`PYTHONPATH=orchestrator`**, **`AUTH_ENABLED=false`**) — **3 passed** on merge **`52105b303`**. Full **`make test`** not re-run for this retro slice.

## Status history

- **2026-06-21:** Finalised by **`/record-retro`** — merge **`52105b303`** on **`dev`**.
