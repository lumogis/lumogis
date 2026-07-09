# ADR-158: Graph-aware entity sharing on document publish (LUM-586)

**Status:** Finalised
**Created:** 2026-07-08
**Last updated:** 2026-07-08
**Decided by:** /explore (Opus 4.8); finalised by /verify-plan (Composer)

## Context

LUM-157 shares a household document's **chunks** into `scope='shared'` but deliberately left the knowledge graph out — members could search/document-chat the text, not traverse the entities and relationships the document extracted. LUM-586 closes that gap for premium/graph-enabled deployments: when a document is shared, its extracted personal entities are projected into shared Postgres+Qdrant (reusing LUM-581's `project_entity`), then into the shared FalkorDB graph via a new `DOCUMENT_SHARED` webhook and the existing `project_entity_into_graph` primitive.

Code review during planning discovered the shared-scope graph primitives (`project_entity_into_graph`, `_sweep_incident_edges`) had **zero callers** and the sweep MATCH pattern (`:Entity {scope:'personal'}`) did not match live personal nodes (type-labelled, no `scope` property). LUM-586 therefore **owns fixing and wiring** these primitives, not merely calling them.

## Decision

Adopt **Option A** (as explored): on document share, cascade each document-extracted entity into `scope='shared'` with `share_origin='document'` provenance, dispatch `DOCUMENT_SHARED` to the KG service for near-immediate graph projection, and use a refcounted retraction helper on unshare/purge that respects `share_origin` (`document` / `user` / `multiple`) and whether another still-shared document still justifies the entity.

**v1 deployment constraint:** graph-aware entity sharing requires **`GRAPH_MODE=service`** (the shared-scope primitive and reconcile arm live in `services/lumogis-graph/`). Postgres+Qdrant shared entity rows are still created when graph mode is not disabled; only the FalkorDB projection is service-mode-only in v1.

**Retraction contract:** `retract_document_entities` / `plan_document_entity_retraction` decide per entity whether to delete the shared projection, downgrade `multiple`→`user`, or keep. Wired into `unproject_file` (owner unshare), `document_purge._postgres_arm` (enumerate before `entity_relations` delete), and indirectly into LUM-584 admin unshare via `sharing_registry` → `unproject_file`.

## Alternatives Considered

- **Option A′** — recall-only / suppress doc-derived entities from lists (deferred UX follow-up).
- **Option B** — graph-only nodes without shared entity rows (rejected; forks projection model).
- **Option C** — query-time visibility union (rejected; traversal-leak risk).

Full detail: `.cursor/explorations/LUM-586-graph-aware-entity-sharing.md`.

## Consequences

**Easier:** Reuses LUM-157 share job + LUM-581 `project_entity`; honest `partial` on per-entity failure; reconcile backstop for dropped webhooks; flat "my shared items" entities arm (LUM-583) surfaces cascaded entities automatically.

**Harder:** Three teardown call sites must stay consistent; `share_origin` state machine must be correct; sweep fix was mandatory before any cascade could work; migration `050-entities-share-origin.sql` required on existing volumes.

### As-built notes (verify-plan, 2026-07-08)

- **`_sweep_incident_edges` fix:** personal matches are label-agnostic with `scope IS NULL`; shared projections keep `:Entity` + `scope`. Proven live against FalkorDB (`test_document_shared_projects_edge_between_two_shared_entities`).
- **`entities.share_origin`:** migration `050`; `project_entity` defaults to `'user'`; cascade passes `'document'`; `ON CONFLICT` promotes to `'multiple'`.
- **`DOCUMENT_SHARED`:** new `WebhookEvent` + payload; vendored via `make sync-vendored`; dispatcher registers seventh callback.
- **Mode gate:** orchestrator cascade uses `get_graph_mode() != 'disabled'` (not `get_graph_store()`), so `GRAPH_MODE=service` still creates PG+Qdrant rows and fires the webhook even though the orchestrator has no in-process FalkorDB store.
- **Purge ordering:** `_postgres_arm` plans retraction before deleting `entity_relations`; shared-row deletes use `published_from = ANY(%s::uuid[])` (text[] params from the adapter require explicit cast).
- **Admin unshare:** no direct edit to `admin_unshare.py`; file teardown routes through `sharing_registry` → `unproject_file`, which now resolves `file_path` from `RETURNING` and calls `retract_document_entities`.
- **Re-ingest:** `reproject_shared_on_reingest` diff-retracts removed doc-origin shared entities (LUM-604) then re-runs the entity cascade for survivors; relation prune happens during ingest entity storage.
- **Large-doc batching (LUM-605):** PoC on lumogis-test stack — 200-entity cascade ~7.4 s (embed-dominated); **defer** UNWIND/batch-embed implementation; evidence in `orchestrator/tests/premium/lum605-cascade-poc-report.json`.
- **Grouped-under-doc UI:** deferred to LUM-583 amendment.

## Status history

- 2026-07-08: Draft created by /explore LUM-586.
- 2026-07-08: Revised during plan review (R1/R2) — sweep fix, reconcile scope filter, purge ordering, `share_origin` defaults, service-mode-only v1.
- 2026-07-08: Finalised by /verify-plan — implementation confirmed; live FalkorDB merge gate + live Postgres/Qdrant refcount integration green on test stack.
- 2026-07-09: **LUM-603** — formalised `GRAPH_MODE=service` as the v1 hard requirement for shared FalkorDB projection (no in-process parity); CI guards in `orchestrator/tests/premium/test_lum586_graph_mode_service_requirement.py`.
