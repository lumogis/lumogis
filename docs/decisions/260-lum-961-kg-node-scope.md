# ADR-260: Every Knowledge Graph node carries `scope`, stamped by the writer (LUM-961)

**Status:** Finalised
**Created:** 2026-11-11
**Last updated:** 2026-11-12
**Decided by:** /explore (claude-opus-5-5, headless run-workflow); scope narrowed by Thomas in the LUM-961 issue (2026-11-11); refined in /review-plan arbitration R1
**Finalised by:** /verify-plan 2026-11-12 (claude-opus-5-5)
**Plan:** `.cursor/plans/LUM-961-kg-node-scope.plan.md`
**Exploration:** `.cursor/explorations/LUM-961-kg_node_scope.md`
**Draft mirror:** `.cursor/adrs/LUM-961-kg_node_scope.md`
**Relates:** [ADR 015](015-personal-shared-system-memory-scopes.md) (scope model and fail-closed reads; this ADR makes the graph writers meet it), ADR 158 (LUM-586 shared-edge sweep)

## Context

ADR 015 made `scope` a property on every FalkorDB node, and made every graph read fail closed on it through `visible_cypher_fragment`. That fragment admits `scope = 'personal' AND user_id = $me`, or `scope IN ['shared','system']`.

The personal graph writer (`services/lumogis-graph/graph/writer.py`) never wrote `scope`. `GRAPH_MODE=inprocess` uses the same writer through Core's re-export shims. So every personal Document, entity, evidence, Session, Note, AudioMemo, code-structure and temporal node was hidden from ego, path, mentions, `/graph/stats` and the temporal pipeline's personal tier. Mock-store unit tests could not see this. The LUM-929 live lane did: `/graph/stats` returned `node_count` 0.

Three constraints shaped the fix:

- The LUM-586 shared-edge sweep (`projection.py:_sweep_incident_edges`) found personal nodes by `scope IS NULL`, so it depended on the bug.
- The shared `file_index` projection row has the same `(file_path, user_id)` as the personal row, so both MERGE onto the same Document node. That node's `scope` therefore cannot come from the row.
- Replaying all projections would inflate `RELATES_TO.co_occurrence_count` (LUM-953).

On 2026-11-11 Thomas ruled out any repair of existing graphs in the issue body: no backfill, migration or reconcile pass, and no read-side fallback for nodes without `scope`. There is no production graph yet, and dev and test graphs are rebuilt.

## Decision

**Writers stamp `scope` on every node. The store adapter never defaults it.**

| Node | `scope` | `published_from` | Writer |
|---|---|---|---|
| `:Document` | `'personal'` always | absent | `project_document` (it has no `scope` parameter), `_ensure_source_node`, `temporal._merge_evidence_node` |
| type-labelled entity (`:Person` …) | `'personal'` always | absent | `project_entity` |
| `:Entity` projection | `'shared'` / `'system'` | personal entity id | `projection.project_entity_into_graph` (unchanged) |
| `:Session`, `:Note`, `:AudioMemo` | `'personal'` for the owner's row; the row's `'shared'` / `'system'` for a projection | set exactly when the node is a projection | `project_session`, `project_note`, `project_audio` |
| code-structure nodes | `'personal'`, set last so parser props cannot override it | absent | `project_code_structure` |
| temporal evidence stubs | `'personal'` | absent | `_merge_evidence_node`, `stamp_temporal_checked` |

- **Invariant:** a node has `scope = 'personal'` exactly when it has no `published_from`. `graph/schema.py:node_scope(scope, published_from)` enforces it for Session, Note and AudioMemo before any graph write, and raises `ValueError` for an unknown scope or a mismatch. Reconcile counts such a row as `projected_failed` and leaves it unstamped. The other writers hardcode one side of the invariant. `PERSONAL_SCOPE` and `node_scope` live in `graph/schema.py` because `writer.py` already imports `temporal.py`.
- **`reconcile_documents` selects personal `file_index` rows only** (`AND (scope = 'personal')`). A shared `file_index` row can never reach the Document node. Shared documents reach the graph through the LUM-586 entity cascade.
- **The sessions, notes and audio reconcile arms pass the row's `scope` and `published_from`.** `published_from` is passed as a string.
- **The LUM-586 sweep is strict:** `src.scope = 'personal'` and `other.scope = 'personal'`. It also returns `count(r2)`, and `project_entity_into_graph` logs it as `swept_edges=N`.
- **Unchanged:** `visible_cypher_fragment` (both copies), the `create_node` adapters, the user import (`user_export.py`), and the MERGE key `(lumogis_id, user_id)`.
- **No repair of existing graphs** (Thomas, 2026-11-11). A node written before this change stays unscoped until something writes it again.
- **Guards:** an AST ratchet in `services/lumogis-graph/tests/test_writer_node_scope_lum961.py` counts every `.create_node(` call site in the KG service and the orchestrator, and also scans for raw labelled-node `MERGE`/`CREATE` Cypher. A new writer fails it until it is stamped and listed. A parity test keeps the two `visible_cypher_fragment` copies identical.

## Alternatives Considered

- **Backfill `scope='personal'` onto unscoped nodes** (the draft's `backfill_node_scope()`, run each reconcile cycle) **plus a tolerant sweep** (`coalesce(scope,'personal')`). Rejected by Thomas (2026-11-11). No production graph exists, dev and test graphs are rebuilt, and a fallback for unscoped nodes is the guessing the decision rules out.
- **NULL-tolerant read fragment.** Rejected: it makes "no scope" legitimate in a fail-closed privacy predicate, which contradicts ADR 015.
- **Backfill by Postgres replay** (clear `graph_projected_at`). Rejected: entity re-projection inflates co-occurrence counts (LUM-953) and misses orphan nodes.
- **Scope in the MERGE key** (ADR 015's conditional MERGE key). Rejected: it is a node-identity migration this bug does not need, because shared projections already have distinct uuid5 ids.
- **A `scope` parameter on `project_document`.** Rejected: the Document node is shared by the personal and shared `file_index` rows, so a shared row could flip the personal node to `shared`.

## Consequences

- Personal graph reads (ego, path, mentions, `/graph/stats`, viz, the temporal personal tier) return the caller's own nodes in both graph modes. Other members' personal nodes stay hidden. Members with `allows_shared` also see shared Session, Note and AudioMemo projection nodes. Members who opted out see only `system` ones.
- **"Absent scope means personal" is no longer a graph convention.** The GC (LUM-952), the future Memory node (LUM-528) and any new writer must stamp and match `scope = 'personal'`. A strict key never collects nodes written before this change.
- **A graph built before this change is a partly visible mix.** Any re-touch stamps one node (a re-mention, a re-ingest, or a reconcile of a changed row). The sweep ignores unscoped nodes and logs `swept_edges=0`. The supported remedy is a fresh stack (private ops guide). Clearing `graph_projected_at` is not a remedy (LUM-953).
- **A published item is two nodes,** the personal one and its projection, and the owner's `/graph/stats` counts both. Ego follows `RELATES_TO` and `mention_sources` follows `MENTIONS`, so neither returns the projection twice.
- **Unpublish leaves the projection node.** `_unproject_uuid_pk` removes the Postgres row and the Qdrant point only. The leftover node now adds 1 to household `/graph/stats` `node_count` and exposes no content. GC is folded into LUM-952's Later section.
- **Edges are outside this contract.** Personal edges carry no `scope`, and readers filter nodes only.
- **The evidence-stub precondition is documented, not checked at runtime.** `_ensure_source_node` and `_merge_evidence_node` must receive personal row ids. That is true today: publish fires no extraction hook.
- **A shared `file_index` row keeps `graph_projected_at` NULL** until the personal Document is next projected. Nothing reads that column for `file_index`.
- **ADR 015's conditional MERGE-key sentence is superseded** by "MERGE on `(lumogis_id, user_id)`, with distinct uuid5 projection ids".
- A live FalkorDB round trip (writer → real `get_stats` handler and `mention_sources`; owner, other member, opted-out member) runs in CI `lint-and-test`.

## Revisit conditions

- Shared Documents become first-class graph nodes: the Document MERGE key must first separate personal from shared (exploration Finding 6).
- A production graph exists that was built before this change: the no-repair decision needs revisiting, because a fresh stack is no longer an acceptable remedy.
- FalkorDB gains enforceable node-property existence constraints: make `scope` mandatory at the store.

## Status history

- 2026-11-11: Draft created by /explore (backfill + tolerant sweep).
- 2026-11-11: Scope narrowed by Thomas in the issue: no backfill and no read-side fallback.
- 2026-11-12: Revised and finalised by /verify-plan. Implementation revealed: no backfill, a strict sweep, `published_from` on Note/AudioMemo projections, `node_scope` in `graph/schema.py`, and `swept_edges` logging.
