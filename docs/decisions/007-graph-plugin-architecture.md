# ADR-007: Graph plugin architecture — bundled plugin, eventual consistency, tri-store ownership

## Context

Phase 3 turns the existing `GraphStore` protocol and FalkorDB overlay (ADR-002) into a
production graph layer. Three architectural questions needed answers before writing code:

1. **Where does graph-writing logic live?** The graph plugin could be core code (always
   present), a bundled plugin (present in the repo, loadable at runtime), or an external
   plugin (third-party, mounted at deploy time per ADR-005).

2. **How strongly consistent must graph writes be?** Graph writes involve a second
   database (FalkorDB). Requiring strong consistency with Postgres/Qdrant would block
   ingest and chat paths whenever FalkorDB has any downtime.

3. **Who owns which data?** Three stores now serve the same domain model. Without clear
   ownership, writes scatter and queries become ambiguous about which store is authoritative.

## Decision

### 1. Bundled plugin

The graph plugin lives under `plugins/graph/` and is loaded by the existing plugin loader
at startup (ADR-005). It is **bundled** — shipped in the core repository — but it is still
a plugin: core never imports it, all integration goes through `hooks.py`, and the system
works correctly without FalkorDB configured.

Rationale: an external plugin (separate repo) would force users to clone two repositories.
Core code (always on) would couple graph infrastructure to every installation. A bundled
plugin gives first-class graph support to users who opt in, while keeping the default
installation graph-free.

The plugin disables itself gracefully:
- `FALKORDB_URL` not set → `INFO` log, no router returned, no hooks subscribed.
- `FALKORDB_URL` set but FalkorDB unreachable → `WARNING` log with the URL, no router
  returned. The reconciliation job retries once FalkorDB becomes reachable.

### 2. Eventual consistency via `fire_background()` and `graph_projected_at`

All graph-triggering hooks (`DOCUMENT_INGESTED`, `ENTITY_CREATED`, `SESSION_ENDED`,
`NOTE_CAPTURED`, `AUDIO_TRANSCRIBED`) are dispatched with `hooks.fire_background()`.
Graph writes run in a `ThreadPoolExecutor` worker — they never block ingest or chat.

Durability is provided by a `graph_projected_at TIMESTAMPTZ` column on every source table
(`entities`, `file_index`, `sessions`, `notes`, `audio_memos`). The graph writer stamps
this column after a successful full projection unit. The reconciliation job (scheduled
daily at 03:00) queries `WHERE graph_projected_at IS NULL OR updated_at > graph_projected_at`
and replays any missing projections. **Stamping order:** FalkorDB write first, then Postgres
stamp. If FalkorDB write fails, no stamp — reconciliation retries. If Postgres stamp fails
after a successful FalkorDB write, the record is re-projected on the next cycle (harmless:
all writes use Cypher `MERGE`, so re-projection is idempotent).

This means the graph is **eventually consistent** with Postgres, not strongly consistent.
Core pipeline (Postgres + Qdrant) always completes regardless of FalkorDB state.

### 3. Tri-store ownership model

| Store          | Owns                                                                              | Source of truth for          |
| -------------- | --------------------------------------------------------------------------------- | ---------------------------- |
| **Postgres**   | Entity master records, provenance log, file index, sessions, notes, audio metadata | Canonical identity and state |
| **Qdrant**     | Dense+sparse embeddings for documents, sessions, entities, signals                | Semantic retrieval           |
| **FalkorDB**   | Entity-to-entity topology, entity-to-information-object provenance edges          | Graph traversal              |
| **Filesystem** | Raw files                                                                         | Content                      |

FalkorDB is a **traversal projection** of Postgres. Every FalkorDB node carries a
`lumogis_id` that points to the canonical Postgres record. The graph can be fully rebuilt
from Postgres via the backfill endpoint.

### Projection units

Each hook event maps to a projection unit — the complete set of graph operations that
must succeed before `graph_projected_at` is stamped:

| Event               | Projection unit                                                                      |
| ------------------- | ------------------------------------------------------------------------------------ |
| `ENTITY_CREATED`    | Entity node MERGE + MENTIONS edge + RELATES_TO edge updates for co-occurring entities |
| `DOCUMENT_INGESTED` | Document node MERGE only                                                             |
| `SESSION_ENDED`     | Session node MERGE + DISCUSSED_IN edges to resolved entity nodes                    |
| `NOTE_CAPTURED`     | Note node MERGE only                                                                 |
| `AUDIO_TRANSCRIBED` | AudioMemo node MERGE only                                                            |

Entity/MENTIONS edges for notes and audio arrive via separate `ENTITY_CREATED` events
fired by `services/notes.py` and `services/audio.py` respectively.

## Consequences

- **Core is unaffected** when the graph plugin is absent or FalkorDB is down.
- **Graph writes are non-blocking.** Ingest and chat latency are not affected by FalkorDB
  state.
- **Drift is self-healing.** The reconciliation job closes gaps from transient FalkorDB
  outages or missed events without manual intervention.
- **FalkorDB is rebuildable.** If the graph store is corrupted or migrated, `POST /graph/backfill`
  replays all Postgres records into a fresh graph.
- **Trade-off:** The graph may lag Postgres by up to one reconciliation cycle (default: ~24h)
  after a FalkorDB outage. Chat responses and `query_graph` tool results reflect the last
  successfully projected state. This is acceptable for Phase 3's single-user, personal
  knowledge graph use case.
- **Hook stability:** All hook events are internal-only — in-process listeners only, no
  external webhook contract. Payloads may change between versions without deprecation.
