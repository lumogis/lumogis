# ADR-101: Document library UI — list, status, delete, and re-ingest (LUM-160)

> Status: Needs update  
> Last reviewed: 2026-07-07  
> Verified against commit: 6c80e10  
> Notes: **LUM-157** household document sharing shipped (**ADR 155**); the Decision section still says share toggle was deferred — see amendment below.

**Status:** Finalised
**Created:** 2026-06-15
**Last updated:** 2026-06-18
**Decided by:** /explore --headless LUM-160; finalised by /verify-plan 2026-06-15

## Context

LUM-160 (LUM-44 Lumogis Web programme, `milestone:v1.1`) requires a user-facing document library: list ingested documents, show per-document processing status, hard-delete a document across Postgres + Qdrant + optional FalkorDB, and re-ingest failed or stale files. The repo persisted ingest results (`file_index`, Qdrant `documents` chunks, optional graph `Document` nodes) but had no `/api/v1/documents` resource, no multi-store document purge, and no `/documents` SPA route. LUM-162 (ADR-074) established the conversation-history blueprint (API module + `memory_purge` + SSE invalidation) directly applicable here.

## Decision

Ship the document library by reusing the LUM-162 blueprint in one chunk:

- **`/api/v1/documents`** — `GET` list/detail, `DELETE` hard-delete, `POST /{id}/reingest`; `document_id` === `file_index` INTEGER PK; reads via `visible_filter`; delete/re-ingest restricted to personal rows owned by the caller.
- **`services/document_purge.purge_document`** — transactional Postgres (cascade `published_from` projections, delete `entity_relations` by `file_path`, delete personal row), bounded-retry Qdrant chunk deletes via `document_chunk_point_id`, optional `graph.writer.delete_document` (`DETACH DELETE` `Document` node only); honest `partial=true` via `PurgeResult`.
- **Derived status** (no schema migration): batch jobs → `indexing`; `chunk_count > 0` → `indexed`; dead jobs / zero chunks → `failed`; in-flight-only rows use `document_id=null` + `in_flight_job_id`.
- **Re-ingest** enqueues `ingest_upload` or `ingest_watch_file` batch work with optional `force` bypassing unchanged-hash skip; **409** when source file missing on disk.
- **SSE** — separate `_document_debounce_timers` map; sibling hook on `Event.DOCUMENT_INGESTED` pushes coalesced **`document_status_changed`** with `{}` payload (LUM-488 empty-payload pattern); TanStack Query polling fallback while any row is `indexing`.
- **Lumogis Web** — `features/documents/` list + detail, Library bottom-nav entry, `/entities/:entityId` contract route via `EntityCardPanel` (LUM-161 URL contract only); read-only scope badge (LUM-157 deferred).

## Alternatives considered

See `.cursor/explorations/LUM-160-document-library-ui.md` and `.cursor/adrs/LUM-160-document-library-ui.md`.

## Consequences

- **Easier:** trust surface (true delete) + visibility ship together; LUM-175 inherits `/documents/:documentId` identity; no migration or new Docker services.
- **Harder / to watch:** purge trusts snapshot `chunk_count` (stale count may orphan Qdrant points — shared LUM-162 reconciliation follow-up); v1 leaves orphan `entities` rows when last document evidence is removed; `ingest_folder` scans do not synthesise per-file `indexing` rows; Playwright desktop smoke requires live stack + smoke creds (P1 gap).
- **Coordination:** blocks LUM-161 (entity URL contract) and LUM-157 (share toggle placement) — contract route only in this chunk.

## Status history

- 2026-06-15: Draft created by /explore --headless LUM-160
- 2026-06-15: Plan arbitration R1 — SSE debounce isolation, user-scoped entity counts, orphan-entity v1 policy
- 2026-06-15: Finalised by /verify-plan — implementation confirmed (LUM-160)
- 2026-06-18: Amended — LUM-500 partial-failure reconciliation (tombstone + retry)
- 2026-06-18: Amended — LUM-501 orphan-entity inline GC (Postgres + Qdrant entities)
- 2026-07-07: **LUM-157 shipped** — share/unpublish UI + shared Qdrant content projection; canonical record **[ADR 155](155-lum-157-document-content-projection.md)**. The Decision bullet "LUM-157 deferred" and Coordination note are historical for this chunk only.

---

## Amendment: LUM-500 — Partial-failure reconciliation (2026-06-18)

**Decision:** Tombstone pattern (`purged_documents` table, migration 032) mirroring
`purged_conversations` (029 / LUM-162), extended with retry-context columns
(`file_path`, `chunk_count`, `qdrant_deleted`, `graph_deleted`, `errors`, `resolved_at`).

On Postgres arm success a tombstone row is inserted before the store arms run. A subsequent
`DELETE /api/v1/documents/{id}` that finds no `file_index` row checks the tombstone:
partial tombstone → retries only the failed arms and updates it; resolved tombstone →
idempotent HTTP 200 with `partial=false`; no tombstone → HTTP 404 (unchanged).

The `DocumentDetailView` UI fix: no longer navigates away on `partial=true`; shows a
categorized per-arm error banner (Qdrant arm → "Search index copies may still exist", graph arm
→ "Knowledge graph entries may still exist") with a **Retry cleanup** button; escalation copy
("Contact your administrator. Reference: document #ID.") shown after retry also returns partial.

**Structured log event:** `document_purge_partial` with
`{event, document_id, file_path, qdrant_deleted, graph_deleted, errors, user_id}`.

**Acceptance threshold (for LUM-416 sweep):** a background reconciliation sweep is warranted
if the `document_purge_partial` event rate exceeds 1% of document deletes over a 7-day window
in production. Below that threshold the tombstone + manual retry path is sufficient.

**Operator manual recovery procedure** (persistent partial after user retries):

1. Identify the stuck tombstone:
   ```sql
   SELECT * FROM purged_documents
   WHERE user_id = '<uid>' AND document_id = <id>;
   ```
2. If `qdrant_deleted = false`: delete orphan Qdrant points via payload filter —
   ```
   POST /collections/documents/points/delete
   { "filter": { "must": [
     { "key": "file_path", "match": { "value": "<file_path>" } },
     { "key": "user_id",  "match": { "value": "<uid>"       } }
   ]}}
   ```
   Or enumerate individually: `document_chunk_point_id(user_id, file_path, i)` for
   `i` in `0..chunk_count` and delete each point.
3. If `graph_deleted = false`: run against FalkorDB —
   ```cypher
   MATCH (d:Document {file_path: "<file_path>", user_id: "<uid>"}) DETACH DELETE d
   ```
4. Mark tombstone resolved:
   ```sql
   UPDATE purged_documents SET resolved_at = NOW()
   WHERE user_id = '<uid>' AND document_id = <id>;
   ```

---

## Amendment: LUM-501 — Orphan-entity inline GC (2026-06-18)

**Decision:** Inline GC inside `purge_document` — after deleting `entity_relations` for the
deleted document, personal entities with **zero remaining relations** are deleted from Postgres
and from the Qdrant `entities` collection in the same call.

This replaces the v1 "leave orphans" policy from the original ADR-101 decision.

### Detection query

Within the Postgres transaction, before deleting `entity_relations`, fetch entities that:
1. Belong to the same user with `scope = 'personal'`
2. Are referenced in the about-to-be-deleted `entity_relations` rows (for this `file_path`)
3. Have **no other** `entity_relations` row for a different `evidence_id`

This avoids a full-table orphan scan; only entities that were just evidence-connected to the
deleted document are candidates.

### Qdrant entity arm

Deletes by `entity_id` payload filter (not by point ID — entity names are mutable post-merge).
Uses `_retry_store_arm("qdrant_entities", ...)` (3 attempts). Error prefix `"qdrant_entities:"`
distinguishable from `"qdrant:"` (document chunk arm).

The arm issues **two** user-scoped filtered deletes against the `entities` collection:
1. personal points — payload `entity_id ∈ orphan_ids`;
2. shared/system **projection** points — payload `published_from ∈ orphan_ids` (a projection's
   own `entity_id` is a distinct uuid5, see `services.projection.project_entity`).

Postgres cascades projection rows via the `published_from` FK (`ON DELETE CASCADE`); the second
filter mirrors that in Qdrant so a GC'd personal entity that the owner had published cannot
linger as a ghost in shared-scope vector search.

### Tombstone atomicity

The tombstone `INSERT` runs **inside** the same Postgres transaction as the row deletes, so the
recovery record is atomic with them — a crash can never leave entities/chunks deleted without a
retryable tombstone (closes the LUM-500 insert-after-commit window).

### Tombstone extension (migration 033)

`purged_documents` gains two columns:
- `qdrant_entities_deleted BOOLEAN NOT NULL DEFAULT FALSE`
- `orphan_entity_ids JSONB NOT NULL DEFAULT '[]'`

`PurgeResult.partial` is now true when any of `qdrant_deleted`, `graph_deleted`, or
`qdrant_entities_deleted` is false (after Postgres success). Retry via tombstone also retries
the entity arm if `qdrant_entities_deleted = false`.

### Structured log

`document_entity_gc` info event emitted when `orphan_count > 0`:
`{event, document_id, file_path, orphan_count, user_id}`.

### Out of scope

- Graph `Entity` node GC — household multi-user ownership rules not yet defined; deferred.
- GC of entities *shared by other household members* — different ownership semantics. (A user's
  own personal→shared projections of a GC'd entity **are** swept, see Qdrant entity arm above.)
- `entities` rows that become orphaned through other paths (e.g. session memory purge) — LUM-416.
