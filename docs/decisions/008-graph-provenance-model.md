# ADR-008: Graph provenance model — direct edges with inline properties

## Context

Every fact in the Lumogis knowledge graph must be traceable to its source: "Why does the
graph say Ada is connected to Project X?" should always be answerable. Two structural
approaches exist for encoding provenance in a property graph:

1. **Direct edges with provenance properties** — the relationship itself carries `evidence_id`,
   `evidence_type`, `timestamp`, and `user_id` as edge properties.

2. **Reified assertion nodes** — a separate intermediate node represents the claim
   (Entity → Assertion → Evidence), decoupling the relationship from its provenance and
   enabling multi-source conflict modelling.

## Decision

Phase 3 uses **direct edges with inline provenance properties only**. No reified
assertion nodes.

Every edge in FalkorDB carries these properties:

| Property        | Type       | Description                                                          |
| --------------- | ---------- | -------------------------------------------------------------------- |
| `evidence_id`   | `str`      | ID of the source object (file_path, session UUID, note UUID, audio UUID) |
| `evidence_type` | `str`      | One of `DOCUMENT`, `SESSION`, `NOTE`, `AUDIO`                       |
| `timestamp`     | `str`      | ISO 8601 datetime when the relationship was extracted                |
| `user_id`       | `str`      | Owner scope — always set, never taken from client input              |

The `user_id` is a SET property on edges (not a MERGE match key). Source and target nodes
are already tenant-scoped in their own MERGE keys (`lumogis_id + user_id`), so edges
inherit tenant isolation from their endpoints.

### Why not reified assertion nodes

1. **Phase 3 queries don't need it.** Ego-network, shortest-path, and entity-dossier
   queries all work with direct edges. Reified nodes would triple the hop count for every
   traversal without enabling any query that direct edges cannot answer.

2. **Edge count stays manageable.** Reification would add one Assertion node and two
   edges for every existing edge — roughly 3× the graph size with no user-visible benefit.

3. **Provenance is already inline.** Every edge carries `evidence_id + evidence_type`,
   which is sufficient to answer "why does this edge exist?" by following the ID to the
   source document or session in Postgres/Qdrant.

4. **Migration path is clear if needed.** If Phase 5's position tracking requires
   reified assertions for multi-source conflict modelling, the migration is: create
   Assertion nodes, copy edge properties into them, replace direct edges with two-hop
   paths. This is a data migration, not an architecture change, and it does not require
   changing the query interface.

### Source confidence policy

Phase 3 does not score source quality numerically. `evidence_type` is preserved on every
edge so query-time source-quality weighting can be layered in Phase 4+ without schema
changes. For example, audio transcripts (noisy) could be weighted lower than documents
(clean) by filtering on `evidence_type` in Cypher WHERE clauses. No schema change needed.

### Co-occurrence edges (RELATES_TO)

Entity-to-entity co-occurrence is stored as a single directed RELATES_TO edge using
**canonical direction: lower `lumogis_id` → higher `lumogis_id`** (lexicographic
comparison, enforced in `schema.py`). This prevents duplicate edges from processing-order
variance.

RELATES_TO edges carry:
- `co_occurrence_count` (int) — incremented on each co-occurrence via Cypher `coalesce()`
- `last_seen_at` (ISO 8601) — updated on each occurrence
- `user_id` — owner scope (SET property, not MERGE key)

RELATES_TO edges are **latent** until `co_occurrence_count >= GRAPH_COOCCURRENCE_THRESHOLD`
(default 3). Sub-threshold edges are stored but hidden from queries and visualization —
internal bookkeeping, not first-class relationships. This prevents low-signal co-mentions
from cluttering the graph.

## Consequences

- **Every graph fact is source-traceable** via `evidence_id` + `evidence_type` on the edge.
- **Traversal is efficient.** Direct edges mean 1-hop queries instead of 3-hop queries
  for provenance lookups.
- **Phase 4+ can add source weighting** by filtering on `evidence_type` — no schema change.
- **Phase 5 can migrate to reified assertions** if multi-source conflict modelling is
  needed — the migration path is clear and non-breaking for query consumers.
- **Trade-off:** If two sources produce the same entity relationship, they are represented
  as two separate MENTIONS edges (same source/target, different `evidence_id`). This is
  correct for Phase 3's provenance model. Entity-to-entity co-occurrence is aggregated
  into a single RELATES_TO edge (count incremented), not stored as separate edges per
  source.
