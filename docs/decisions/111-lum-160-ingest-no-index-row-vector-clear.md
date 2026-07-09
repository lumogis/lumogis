# ADR 111: Amendment to ADR 109 — ingest vector clear without file_index row (LUM-160)

**Status:** Finalised
**Created:** 2026-06-22
**Last updated:** 2026-06-22
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-06-22 (Composer)
**Plan:** none — shipped via Cursor agent branch before formal plan / verify for this slice
**Exploration:** `.cursor/explorations/ingest_no_index_row_vector_clear_retro.md`
**Draft mirror:** `.cursor/adrs/ingest_no_index_row_vector_clear.md`
**Amends:** `docs/decisions/109-lum-160-sparse-qdrant-reingest-clear.md` (call-site gate: cleanup without pre-existing index row)

**Linear:** [LUM-160](https://linear.app/lumogis/issue/LUM-160/document-library-ui-list-status-delete-and-re-ingest-ingested)

## Context

**ADR 109** (merge **`52105b303`**, retro **2026-06-21**) introduced **`_delete_document_vectors_for_path`** with **`VectorStore.delete_where`** on **`user_id` + `file_path`** before re-ingest when sparse chunk indices could leave stale vectors under **`INJECTION_ACTION=block_ingest`** (**ADR 039**). The as-shipped call sites still gated cleanup on **`if existing:`** — an existing **`file_index`** or **`external_documents`** row.

A follow-on gap remained: a **partial first ingest** can write Qdrant chunk vectors then abort **before** creating a **`file_index`** row (e.g. **`block_ingest`** wrote some chunks then returned). A later ingest at the same path saw **`existing is None`**, skipped **`_delete_document_vectors_for_path`**, and left orphan vectors reachable in auto-RAG (**ADR 059** / **ADR 106**) and document chat (**ADR 101** / **LUM-175**).

Fix landed on **`dev`** via **`cursor/critical-bug-investigation-5feb`** (commit **`f5e8be0b3`**, cherry-picked 2026-06-22) without a Product OS plan/verify loop.

## Decision

1. **`ingest_file`** — after the unchanged-hash short-circuit, **always** call **`_delete_document_vectors_for_path`** before writing new chunks (remove **`if existing:`** gate).
2. **`ingest_external_document`** — same unconditional pre-write cleanup after unchanged-hash skip logic.
3. **Regression test** — **`orchestrator/tests/test_ingest_orphan_chunks.py::test_ingest_file_clears_vectors_on_first_write_without_index_row`**: mocks no **`file_index`** row and asserts **`delete_where`** on **`user_id` + `file_path`**.

ADR **109** remains the canonical record for sparse re-ingest **`delete_where`** semantics; this ADR **narrows** the call-site contract so cleanup is not conditional on a pre-existing index row.

## Alternatives considered

- **Keep `if existing:` gate** (ADR 109 as-shipped) — rejected; partial first ingest leaves searchable orphans on retry.
- **Defer to purge sweeper only** (**ADR 104**) — rejected; same rationale as ADR 109.
- **New ADR without amending 109** — rejected; explicit **Amends ADR 109** link preferred over silent drift (see **ADR 085** pattern).

## Consequences

**Positive:** Ingest retries after partial **`block_ingest`** first writes cannot skip vector cleanup; aligns **`capabilities.md`** re-ingest wording with runtime behaviour.

**Limits:** Unit mocks only; no compose round-trip against live Qdrant with **`block_ingest`** enabled (inherits ADR 109).

## Revisit conditions

- **`ingest_file`** / **`ingest_external_document`** refactor — preserve unconditional pre-write **`_delete_document_vectors_for_path`** after unchanged-hash short-circuit.
- Reports of ghost citations after ingest retry — add integration test with real Qdrant + **`INJECTION_ACTION=block_ingest`**.

## Linear linkage (Product OS)

- **LUM-160:** parent document-library / ingest programme — post-ship correctness (comment via **`/linear-update`**, not a new issue required).
- **New issue needed:** no

## Testing retrospective

| Layer | Command / artefact | Result |
|-------|-------------------|--------|
| Unit | `PYTHONPATH=orchestrator AUTH_ENABLED=false .venv/bin/python -m pytest orchestrator/tests/test_ingest_orphan_chunks.py -v` | **4 passed** on **`f5e8be0b3`** |

Full **`make test`** not re-run for this retro slice.

## Status history

- **2026-06-22:** Finalised by **`/record-retro`** — cherry-pick **`f5e8be0b3`** on **`dev`**.
