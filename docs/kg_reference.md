# Lumogis Knowledge Graph — Technical Reference

> Last reviewed: 2026-05-02 (spot-check against `GRAPH_MODE` / service wiring; large doc — see codebase if anything disagrees)  
> Verified against commit: 98f02b1

> Authoritative reference for the knowledge graph subsystem built in Phase 3 (M1–M4, M9), the KG Quality Pipeline (Pass 1–4b), the lumogis-graph service extraction, and the **family-LAN multi-user + personal/shared/system scopes** work (ADRs 012, 013, 015, 023).
> Generated from the codebase as of **2026-04-24** (prior deep refresh). Where the codebase differs from any plan or earlier description, the codebase is authoritative.

> **Deployment modes** — The KG subsystem runs in `GRAPH_MODE=inprocess` (default) or `GRAPH_MODE=service`. See §1.6, §5.4, §6.4, and §8.1. **HTTP routes** for `/graph/ego`, `/graph/viz`, etc. are registered on **Core** only when `inprocess`; in `service` they are served from **`lumogis-graph`** (same path relative to `KG_SERVICE_URL`).

> **Auth** — `AUTH_ENABLED=true` enforces JWT bearer auth on most Core routes, with admin-only and user-scoped review semantics (§6.0). The **`/graph/health` metrics endpoint** is affected by the global auth middleware: unauthenticated when auth is off, **401** when auth is on (it does *not* bypass auth).

---

## Table of Contents

1. [Architecture Overview](#1-architecture-overview) — incl. §1.6 Deployment modes
2. [Graph Schema](#2-graph-schema)
3. [File Reference](#3-file-reference)
4. [Database Tables](#4-database-tables)
5. [Environment Variables](#5-environment-variables) — incl. §5.4 Service-mode variables
6. [API Endpoints](#6-api-endpoints) — incl. §6.0 auth matrix, §6.4 lumogis-graph service endpoints
7. [Quality Pipeline](#7-quality-pipeline)
8. [Weekly Maintenance Job](#8-weekly-maintenance-job) — incl. §8.1 Job ownership by mode
9. [Knowledge Graph Management Page](#9-knowledge-graph-management-page)
10. [Known Limitations and Deferred Work](#10-known-limitations-and-deferred-work)
11. [Parity Verification](#11-parity-verification)

---

## 1. Architecture Overview

### 1.1 The Three-Store Model

Lumogis stores data across three complementary stores:

| Store | Technology | Owns | Purpose |
|-------|-----------|------|---------|
| **Metadata Store** | PostgreSQL | Entities, relations, sessions, notes, audio memos, file index, review queue, constraint violations, edge scores, dedup runs | Source of truth for all structured data. Every entity, provenance edge, and quality metric lives here. |
| **Vector Store** | Qdrant | Document chunks, conversation embeddings, entity embeddings, signal summaries | Semantic search via dense vector similarity. Collections: `documents`, `conversations`, `entities`, `signals`. |
| **Graph Store** | FalkorDB | Entity nodes, information-object nodes, relationship edges | Relationship traversal, co-occurrence analysis, shortest-path queries. Entirely derived from Postgres — can be rebuilt from scratch. |

FalkorDB is optional. When `GRAPH_BACKEND != "falkordb"`, all graph plugin code no-ops gracefully. Core ingest, search, and chat are fully unaffected.

### 1.2 How the Graph Plugin Fits into the Orchestrator Lifecycle

The graph plugin is loaded by `plugins/__init__.py:load_plugins()` at startup. The `__init__.py` module:

1. Registers six hook handlers (M1 write path)
2. Registers the `CONTEXT_BUILDING` handler and `query_graph` tool (M3 read path)
3. Schedules the daily reconciliation job via APScheduler (M2)
4. Exposes a combined FastAPI router merging backfill routes (M2) and viz routes (M4)

The plugin router is included into the FastAPI app via `app.include_router()` during lifespan startup.

### 1.3 Hook Event Flow

| Event | Fired By | Payload | Graph Handler | Graph Action |
|-------|----------|---------|---------------|-------------|
| `DOCUMENT_INGESTED` | `services/ingest.py` | `file_path`, `chunk_count`, `user_id` | `on_document_ingested` | Merge `Document` node |
| `ENTITY_CREATED` | `services/entities.py` | `entity_id`, `name`, `entity_type`, `evidence_id`, `evidence_type`, `user_id`, `is_staged` | `on_entity_created` | Merge entity node + `MENTIONS` edge + `RELATES_TO` co-occurrence edges. Staged entities are skipped entirely. |
| `SESSION_ENDED` | `routes/chat.py` | `session_id`, `summary`, `topics`, `entities`, `entity_ids`, `user_id` | `on_session_ended` | Merge `Session` node + `DISCUSSED_IN` edges to resolved entities |
| `NOTE_CAPTURED` | Note capture flow | `note_id`, `user_id` | `on_note_captured` | Merge `Note` node |
| `AUDIO_TRANSCRIBED` | Audio processing | `audio_id`, `file_path`, `duration_seconds`, `user_id` | `on_audio_transcribed` | Merge `AudioMemo` node |
| `ENTITY_MERGED` | `services/entity_merge.py` | `winner_id`, `loser_id`, `user_id` | `on_entity_merged` | Transfer all edges from loser to winner, delete loser node |
| `CONTEXT_BUILDING` | `routes/chat.py` | `query`, `context_fragments` | `on_context_building` | Detect entities in query, append `[Graph]` context lines |

All write handlers run in the hooks `ThreadPoolExecutor` (background, thread-safe). The `CONTEXT_BUILDING` handler runs synchronously (target < 50ms).

### 1.4 The Projection Unit Model

A **projection unit** is the atomic write operation for a single Postgres row into FalkorDB. Each projection unit:

1. Writes to FalkorDB using `MERGE` (idempotent)
2. On success, stamps `graph_projected_at = NOW()` on the source Postgres row
3. On failure, logs the error and does NOT stamp — reconciliation will retry

The five projection unit types are:

| Unit | Postgres Source | FalkorDB Output | Stamp Column |
|------|----------------|-----------------|-------------|
| `project_document` | `file_index` row | `Document` node | `file_index.graph_projected_at` |
| `project_entity` | `entities` row + `entity_relations` rows | Entity node + `MENTIONS` edges + `RELATES_TO` edges | `entities.graph_projected_at` |
| `project_session` | `sessions` row | `Session` node + `DISCUSSED_IN` edges | `sessions.graph_projected_at` |
| `project_note` | `notes` row | `Note` node | `notes.graph_projected_at` |
| `project_audio` | `audio_memos` row | `AudioMemo` node | `audio_memos.graph_projected_at` |

### 1.5 The Eventual Consistency Model

FalkorDB is a derived store. Consistency is eventual, not transactional:

- **Live path**: Hook handlers project events into FalkorDB immediately after Postgres writes. If FalkorDB is unreachable, `graph_projected_at` is not stamped.
- **Reconciliation**: A daily cron job (03:00 UTC) scans all five source tables for rows where `graph_projected_at IS NULL OR updated_at > graph_projected_at`. Stale rows are re-projected using the same `project_*` helpers as the live path.
- **Backfill**: An admin endpoint (`POST /graph/backfill`) triggers on-demand reconciliation.

This model is acceptable because:
- FalkorDB is read-only from the user's perspective (no user writes originate in FalkorDB)
- All graph writes are idempotent (`MERGE` with deterministic match keys)
- The worst case is a brief window where a newly created entity is not yet visible in graph queries

In **`GRAPH_MODE=service`** the live path becomes asynchronous over HTTP: Core's `graph_webhook_dispatcher` POSTs a `WebhookEnvelope` to the KG service (which queues it on its internal `webhook_queue` ThreadPoolExecutor) and returns immediately. The KG service then runs the same `project_*` helpers, stamps the same `graph_projected_at` columns in the shared Postgres, and the eventual-consistency guarantees above continue to apply. Reconciliation is owned by the KG service in `service` mode (see §1.6 and §8.1).

---

## 1.6 Deployment Modes (`GRAPH_MODE`)

The KG subsystem can run in three modes, selected at orchestrator startup by the `GRAPH_MODE` environment variable. The mode is read once at boot by `config.get_graph_mode()` and dispatched in `main.py:_wire_graph_mode_handlers()`.

| Mode | Where the plugin runs | Owns webhooks | Owns reconciliation | Owns weekly job | `query_graph` tool | `/context` injection |
|------|----------------------|---------------|--------------------|-----------------|--------------------|---------------------|
| `inprocess` (default) | Inside `orchestrator` process | Core (in-process hooks) | Core (APScheduler) | Core (APScheduler) | Core handler in `plugins/graph/query.py` | Core hook on `CONTEXT_BUILDING` |
| `service` | Inside `lumogis-graph` container | KG service (HTTP) | KG service (APScheduler) | KG service (APScheduler) | Core proxy ToolSpec → KG `/tools/query_graph` | Core proxy → KG `/context` (40 ms client timeout) |
| `disabled` | Not loaded anywhere | Nobody | Nobody | Nobody | Not registered | Not invoked |

### Plugin self-disable (`inprocess` vs `service`)

To prevent silent double-projection, `orchestrator/plugins/graph/__init__.py` reads `config.get_graph_mode()` at import time and self-disables when the mode is anything other than `inprocess`:

```python
_MODE = _core_config.get_graph_mode()
if _MODE != "inprocess":
    # Set router = None; do NOT register hook handlers, query handlers,
    # or the reconciliation job. The KG service owns all of these in
    # `service` mode; in `disabled` mode nobody owns them.
    router = None
else:
    _register_hook_handlers()
    _register_query_handlers()
    _register_reconciliation_job()
```

### Wiring sequence

`orchestrator/main.py` lifespan calls `_wire_graph_mode_handlers(config.get_graph_mode())` once at startup:

- **`inprocess`** — does nothing extra. The plugin's import-time self-init already registered everything.
- **`service`** — calls `services.graph_webhook_dispatcher.register_core_callbacks()` (wires hook listeners that POST `WebhookEnvelope` to the KG service) and `services.tools.register_query_graph_proxy()` (registers the `query_graph` ToolSpec whose handler proxies LLM calls to the KG service).
- **`disabled`** — does nothing.

Additionally, the weekly quality job (`run_weekly_quality_job`) is scheduled by Core only when the mode is `inprocess`; in `service` mode the KG service's APScheduler owns it (controlled by `KG_SCHEDULER_ENABLED`). See §8.1 for the full ownership matrix.

### What lives in Core vs. the KG service

| Concern | `inprocess` | `service` |
|---------|-------------|-----------|
| Hook callbacks (`on_entity_created`, `on_session_ended`, ...) | `plugins/graph/writer.py` | `services/graph_webhook_dispatcher.py` (Core POST) → `lumogis-graph` `/webhook` (handles) |
| `project_*` helpers | `plugins/graph/writer.py` (in Core process) | `services/lumogis-graph/graph/writer.py` (vendored copy in KG container) |
| Reconciliation job | `plugins/graph/reconcile.py` (Core APScheduler @ 03:00) | `services/lumogis-graph/graph/reconcile.py` (KG APScheduler @ 03:00) |
| Weekly quality job | `services/edge_quality.py:run_weekly_quality_job()` (Core) | `services/lumogis-graph/quality/edge_quality.py` (KG) |
| `query_graph` LLM tool | `plugins/graph/query.py:query_graph_tool` | Core proxy in `services/tools.py:_query_graph_proxy_handler` → KG `/tools/query_graph` |
| `/context` injection during chat | Core hook on `CONTEXT_BUILDING` | Core HTTP call from `routes/chat.py` → KG `/context` (hard 40 ms timeout) |
| `/graph/mgm` operator UI | Core (`routes/admin.py:graph_mgm`) | KG service `/mgm` (hidden from host by default; see §6.4) |
| `query_graph` MCP surface | Core `/mcp` | KG service `/mcp` (FastMCP at `kg_mcp/`) |

---

## 2. Graph Schema

### 2.1 Node Types

#### Entity Nodes

| Label | Postgres `entity_type` | Properties |
|-------|----------------------|------------|
| `Person` | `PERSON` | `lumogis_id` (UUID, required), `name` (text, required), `entity_type` (text, required), `user_id` (text, required) |
| `Organisation` | `ORG` | Same as Person |
| `Project` | `PROJECT` | Same as Person |
| `Concept` | `CONCEPT` (default fallback) | Same as Person |

The mapping is defined in `NodeLabel.ENTITY_TYPE_MAP`:

```python
ENTITY_TYPE_MAP = {
    "PERSON": "Person",
    "ORG": "Organisation",
    "PROJECT": "Project",
    "CONCEPT": "Concept",
}
```

Unknown entity types fall back to `Concept`.

#### Information-Object Nodes

| Label | Properties |
|-------|------------|
| `Document` | `lumogis_id` (file_path, required), `file_path` (text, required), `file_type` (text), `user_id` (text, required), `ingested_at` (ISO 8601) |
| `Session` | `lumogis_id` (session UUID, required), `user_id` (text, required), `summary` (text, truncated to 500 chars), `topics` (list), `created_at` (ISO 8601) |
| `Note` | `lumogis_id` (note UUID, required), `user_id` (text, required), `source` (text, always `"quick_capture"`), `created_at` (ISO 8601) |
| `AudioMemo` | `lumogis_id` (audio UUID, required), `file_path` (text), `user_id` (text, required), `duration_seconds` (float), `created_at` (ISO 8601) |

### 2.2 Edge Types

| Edge Type | Direction | Properties | Created By |
|-----------|-----------|------------|-----------|
| `MENTIONS` | Information-object → Entity | `evidence_id` (text, required), `evidence_type` (text, required), `timestamp` (ISO 8601), `user_id` (text) | `project_entity` |
| `RELATES_TO` | Entity → Entity (canonical: lower `lumogis_id` → higher) | `co_occurrence_count` (int, incremented), `last_seen_at` (ISO 8601), `user_id` (text), `ppmi_score` (float, set by Pass 3), `edge_quality` (float, set by Pass 3), `decay_factor` (float, set by Pass 3), `last_evidence_at` (ISO 8601, set by Pass 3) | `_update_cooccurrence_edges` |
| `DISCUSSED_IN` | Entity → Session | `timestamp` (ISO 8601), `user_id` (text) | `project_session` |
| `DERIVED_FROM` | AudioMemo → Document | _(reserved, not yet implemented)_ | _(future: audio transcript linking)_ |
| `LINKS_TO` | Document → Document | _(reserved, not yet implemented)_ | _(future: vault adapter internal links)_ |
| `TAGGED_WITH` | Document → Concept | _(reserved, not yet implemented)_ | _(future: vault adapter tag materialisation)_ |

### 2.3 Canonical Edge Direction Rules

- **`MENTIONS`**: Always `source → entity` (information object points to the entity it mentions)
- **`RELATES_TO`**: Always `lower_lumogis_id → higher_lumogis_id` (lexicographic ordering of UUIDs). Queries use undirected pattern `(a)-[r:RELATES_TO]-(b)` to match both directions.
- **`DISCUSSED_IN`**: Always `entity → session`

### 2.4 Node Identity

In the **Core in-process** writer, entity and information-object nodes are MERGEd in FalkorDB on **`(lumogis_id, user_id)`** (see `falkordb_store` module docstring). The FalkorDB internal `id()` is used for edge creation within a single projection call but is not the cross-store identity.

**Memory scopes (ADR-015).** The canonical Postgres `entities` table also carries `scope` and `published_from`. The **`lumogis-graph` service** applies `orchestrator/visibility.py` (Core) and `services/lumogis-graph/visibility.py` (KG mirror) for **Postgres reads** and injects a **`visible_cypher_fragment` / `scope` model** in graph queries and projections where shared/system visibility applies. Relying on `user_id` alone is not sufficient for read paths in a household deployment—use the same helpers the code uses.

**Known debt:** `GET /graph/stats` on some stacks still hard-filters Cypher to `user_id = 'default'` (see follow-up **FP-042** / backlog BL-042); do not treat global stats as household-complete until that is fixed.

### 2.5 Text Property Limit

`MAX_TEXT_LENGTH = 500` — all text properties (e.g. session `summary`) are truncated to this length before writing to FalkorDB. FalkorDB is not a content store.

---

## 3. File Reference

### 3.1 `orchestrator/plugins/graph/__init__.py`

Module-level init that runs on plugin load. Registers all hook handlers, the query tool, and the reconciliation job.

| Function | Signature | Purpose |
|----------|-----------|---------|
| `_register_hook_handlers()` | `() -> None` | Registers six event handlers from `writer.py` |
| `_register_query_handlers()` | `() -> None` | Registers `CONTEXT_BUILDING` handler and `query_graph` ToolSpec |
| `_register_reconciliation_job()` | `() -> None` | Schedules daily reconciliation at 03:00 via APScheduler |

Exposes `router` — a combined `APIRouter` merging `routes.router` (backfill) and `viz_routes.router` (viz API).

### 3.2 `orchestrator/plugins/graph/writer.py`

Hook callbacks that project Lumogis events into FalkorDB. All public `on_*` functions are registered in `__init__.py`. All `project_*` helpers are also called by `reconcile.py`.

| Function | Signature | Purpose | Returns | Raises |
|----------|-----------|---------|---------|--------|
| `project_document` | `(gs, *, file_path: str, file_type: str, user_id: str, ms=None) -> None` | Merge Document node, stamp `graph_projected_at` | None | On FalkorDB/Postgres failure |
| `project_entity` | `(gs, *, entity_id: str, entity_type: str, name: str, evidence_id: str, evidence_type: str, user_id: str, ms=None, is_staged: bool = False) -> None` | Merge entity node, MENTIONS edge, RELATES_TO co-occurrence edges. Skips staged entities entirely. | None | On failure |
| `project_session` | `(gs, *, session_id: str, summary: str, topics: list, entities: list, entity_ids: list \| None = None, user_id: str, ms=None) -> None` | Merge Session node, create DISCUSSED_IN edges to resolved entities | None | On failure |
| `project_note` | `(gs, *, note_id: str, user_id: str, ms=None) -> None` | Merge Note node | None | On failure |
| `project_audio` | `(gs, *, audio_id: str, file_path: str, duration_seconds: float = 0.0, user_id: str, ms=None) -> None` | Merge AudioMemo node | None | On failure |
| `on_document_ingested` | `(*, file_path: str, chunk_count: int, user_id: str, **_kw) -> None` | Hook handler for `DOCUMENT_INGESTED` | None | Never (catches all) |
| `on_entity_created` | `(*, entity_id: str, name: str, entity_type: str, evidence_id: str, evidence_type: str, user_id: str, is_staged: bool = False, **_kw) -> None` | Hook handler for `ENTITY_CREATED` | None | Never |
| `on_session_ended` | `(*, session_id: str, summary: str, topics: list, entities: list, entity_ids: list \| None = None, user_id: str = "default", **_kw) -> None` | Hook handler for `SESSION_ENDED` | None | Never |
| `on_note_captured` | `(*, note_id: str, user_id: str, **_kw) -> None` | Hook handler for `NOTE_CAPTURED` | None | Never |
| `on_audio_transcribed` | `(*, audio_id: str, file_path: str, duration_seconds: float = 0.0, user_id: str, **_kw) -> None` | Hook handler for `AUDIO_TRANSCRIBED` | None | Never |
| `on_entity_merged` | `(*, winner_id: str, loser_id: str, user_id: str, **_kw) -> None` | Transfer edges from loser to winner, delete loser node | None | Never |

Internal helpers: `_ensure_source_node`, `_update_cooccurrence_edges` (limited to `MAX_COOCCURRENCE_PAIRS`), `_resolve_entity_names`, `_transfer_outgoing_edges`, `_transfer_incoming_edges`, `_stamp_graph_projected_at` (allowlisted tables: `entities`, `file_index`, `sessions`, `notes`, `audio_memos`).

### 3.3 `orchestrator/plugins/graph/reconcile.py`

Daily reconciliation: replays stale Postgres rows into FalkorDB.

| Function | Signature | Purpose | Returns |
|----------|-----------|---------|---------|
| `reconcile_documents` | `(limit: int \| None = None) -> dict` | Reconcile stale `file_index` rows | Counter dict |
| `reconcile_entities` | `(limit: int \| None = None) -> dict` | Reconcile stale `entities` rows (replays all `entity_relations` per entity). Skips `is_staged=TRUE`. | Counter dict |
| `reconcile_sessions` | `(limit: int \| None = None) -> dict` | Reconcile stale `sessions` rows | Counter dict |
| `reconcile_notes` | `(limit: int \| None = None) -> dict` | Reconcile stale `notes` rows | Counter dict |
| `reconcile_audio` | `(limit: int \| None = None) -> dict` | Reconcile stale `audio_memos` rows | Counter dict |
| `run_reconciliation` | `(limit_per_type: int \| None = None) -> dict` | Run all five passes, return combined summary | `{documents, entities, sessions, notes, audio, totals}` |

Stale condition: `graph_projected_at IS NULL OR updated_at > graph_projected_at`.

### 3.4 `orchestrator/plugins/graph/query.py`

Read-only graph query helpers for M3.

| Function | Signature | Purpose | Returns |
|----------|-----------|---------|---------|
| `resolve_entity_by_name` | `(name: str, user_id: str) -> dict \| None` | Postgres entity lookup by name or alias (case-insensitive). Excludes staged entities. Returns highest `mention_count` match. | Entity row dict or None |
| `ego_network` | `(gs, entity_id: str, user_id: str, depth: int = 1, limit: int = 10) -> dict` | Direct `RELATES_TO` neighbors above co-occurrence threshold and edge quality threshold. Depth capped at 1. | `{entity_id, edges, depth, duration_ms}` |
| `shortest_path` | `(gs, from_entity_id: str, to_entity_id: str, user_id: str, max_depth: int = 4) -> dict` | Shortest path between two entity nodes (any edge type, max depth 4) | `{found, path_length, node_ids, node_names, ...}` |
| `mention_sources` | `(gs, entity_id: str, user_id: str, limit: int = 10) -> dict` | Information objects with `MENTIONS` edges to entity, ordered by timestamp DESC | `{entity_id, sources, duration_ms}` |
| `query_graph_tool` | `(input_: dict) -> str` | ToolSpec handler for `query_graph` tool. Modes: `ego`, `path`, `mentions`. Returns JSON string. | JSON string |
| `on_context_building` | `(*, query: str, context_fragments: list, **_kw) -> None` | Detect entities in query (max 3, word-boundary regex), fetch ego networks (max 5 edges), append `[Graph]` context lines. **Still hard-codes `user_id = "default"`** in the function body: the `CONTEXT_BUILDING` hook has no `user_id` in its kwargs. (The KG `/context` route receives a `user_id` in the JSON body but, as of 2026-04-24, does not pass it into `on_context_building`.) | None |

Edge quality filtering in `ego_network`: edges with `edge_quality IS NULL` use the co-occurrence gate only. Edges with a non-NULL `edge_quality` must also be `>= GRAPH_EDGE_QUALITY_THRESHOLD` (default 0.3).

### 3.5 `orchestrator/plugins/graph/viz_routes.py`

M4 visualization API endpoints.

| Endpoint | Handler | Purpose |
|----------|---------|---------|
| `GET /graph/ego` | `get_ego` | Ego network for a named entity (viz format with nodes/edges arrays) |
| `GET /graph/path` | `get_path` | Shortest path between two named entities |
| `GET /graph/search` | `search_entities` | Entity name autocomplete (min 2 chars, max 20 results) |
| `GET /graph/stats` | `get_stats` | Node count, edge count, top 5 entities by mention count |
| `GET /graph/viz` | `graph_viz` | Serve the Cytoscape.js HTML visualization page |

Hard caps: `GRAPH_VIZ_MAX_NODES` (default 150), `GRAPH_VIZ_MAX_EDGES` (default 300). All endpoints return structured JSON when FalkorDB is unavailable (no 5xx).

### 3.6 `orchestrator/plugins/graph/routes.py`

M2 backfill endpoint.

| Endpoint | Handler | Purpose |
|----------|---------|---------|
| `POST /graph/backfill` | `trigger_backfill` | Admin-only one-time reconciliation. Returns 202 immediately. 409 if already running. Auth via `GRAPH_ADMIN_TOKEN` header. |

### 3.7 `orchestrator/plugins/graph/schema.py`

Single source of truth for graph schema constants.

| Constant | Value | Source |
|----------|-------|--------|
| `NodeLabel.PERSON` | `"Person"` | Hardcoded |
| `NodeLabel.ORGANISATION` | `"Organisation"` | Hardcoded |
| `NodeLabel.PROJECT` | `"Project"` | Hardcoded |
| `NodeLabel.CONCEPT` | `"Concept"` | Hardcoded |
| `NodeLabel.DOCUMENT` | `"Document"` | Hardcoded |
| `NodeLabel.SESSION` | `"Session"` | Hardcoded |
| `NodeLabel.NOTE` | `"Note"` | Hardcoded |
| `NodeLabel.AUDIO_MEMO` | `"AudioMemo"` | Hardcoded |
| `EdgeType.MENTIONS` | `"MENTIONS"` | Hardcoded |
| `EdgeType.RELATES_TO` | `"RELATES_TO"` | Hardcoded |
| `EdgeType.DISCUSSED_IN` | `"DISCUSSED_IN"` | Hardcoded |
| `EdgeType.DERIVED_FROM` | `"DERIVED_FROM"` | Hardcoded (reserved) |
| `EdgeType.LINKS_TO` | `"LINKS_TO"` | Hardcoded (reserved) |
| `EdgeType.TAGGED_WITH` | `"TAGGED_WITH"` | Hardcoded (reserved) |
| `MIN_MENTION_COUNT` | `2` | `GRAPH_MIN_MENTION_COUNT` env var |
| `COOCCURRENCE_THRESHOLD` | `3` | `GRAPH_COOCCURRENCE_THRESHOLD` env var |
| `MAX_COOCCURRENCE_PAIRS` | `100` | `GRAPH_MAX_COOCCURRENCE_PAIRS` env var |
| `MAX_TEXT_LENGTH` | `500` | Hardcoded |

### 3.8 `orchestrator/adapters/falkordb_store.py`

`GraphStore` protocol implementation using the `falkordb` pip package.

| Method | Signature | Purpose |
|--------|-----------|---------|
| `__init__` | `(self, url: str, graph_name: str = "lumogis") -> None` | Parse Redis URL, store connection params |
| `ping` | `(self) -> bool` | `RETURN 1` read-only query; returns False on any error |
| `create_node` | `(self, labels: list[str], properties: dict) -> str` | `MERGE` on `(lumogis_id, user_id)`, `SET` remaining properties, return internal `id(n)` |
| `create_edge` | `(self, from_id: str, to_id: str, rel_type: str, properties: dict) -> None` | `MERGE` on `(evidence_id)` between nodes matched by internal `id()` |
| `query` | `(self, cypher: str, params: dict \| None = None) -> list[dict]` | Execute Cypher, return rows as list of dicts |

Thread safety: per-call `FalkorDB(host, port).select_graph(name)` handle. No shared connection. Connection creation < 1ms on localhost.

Constructor: `FalkorDB(host=host, port=port)` — `from_url()` does not exist in falkordb v1.6.x.

### 3.9 `orchestrator/ports/graph_store.py`

Protocol definition for graph store adapters.

```python
class GraphStore(Protocol):
    def ping(self) -> bool: ...
    def create_node(self, labels: list[str], properties: dict) -> str: ...
    def create_edge(self, from_id: str, to_id: str, rel_type: str, properties: dict) -> None: ...
    def query(self, cypher: str, params: dict | None = None) -> list[dict]: ...
```

### 3.10 `orchestrator/services/entity_quality.py`

Pass 1 of the KG Quality Pipeline — heuristic entity scoring.

| Function | Signature | Purpose | Returns |
|----------|-----------|---------|---------|
| `score_and_filter_entities` | `(entities: list[ExtractedEntity], user_id: str) -> tuple[list[ExtractedEntity], int]` | Score each entity, route to discard/staged/normal | `(kept_entities, discarded_count)` |

See [§7.1 Heuristic Scoring](#71-pass-1-heuristic-entity-scoring) for formula details.

### 3.11 `orchestrator/services/entity_constraints.py`

Pass 2 of the KG Quality Pipeline — constraint validation.

| Function | Signature | Purpose | Returns |
|----------|-----------|---------|---------|
| `run_batch_constraints` | `(entity_ids: list[str], user_id: str) -> int` | Run all per-ingest rules for given entities | Count of new violations |
| `check_orphan_entities` | `(user_id: str) -> int` | Corpus-level: entities with zero edges, > 7 days old | Count of new violations |
| `check_alias_uniqueness` | `(user_id: str) -> int` | Corpus-level: distinct entities sharing an alias | Count of new violations |

Never raises. See [§7.3 Constraint Rules](#73-pass-2-constraint-validation) for rule details.

### 3.12 `orchestrator/services/edge_quality.py`

Pass 3 of the KG Quality Pipeline — PPMI + temporal decay + composite scoring.

| Function | Signature | Purpose | Returns |
|----------|-----------|---------|---------|
| `compute_ppmi` | `(pair_count: int, count_a: int, count_b: int, total_evidence: int) -> float` | PPMI from raw co-occurrence counts | `max(0, log2(P(a,b) / (P(a) * P(b))))` |
| `compute_decay_factor` | `(last_evidence_at: datetime \| None, half_life_days: float) -> float` | `0.5^(elapsed_days / half_life_days)` | `[0.0, 1.0]` |
| `run_edge_quality_job` | `(user_id: str = "default") -> dict` | Compute and upsert all edge scores | `{pairs_computed, pairs_upserted, falkordb_updated, duration_ms}` |
| `run_weekly_quality_job` | `() -> dict` | Full weekly maintenance: edge scores + corpus constraints + dedup | Combined summary dict |

Never raises. See [§7.4 Edge Quality](#74-pass-3-edge-quality-scoring) for formula details.

### 3.13 `orchestrator/services/entity_merge.py`

Pass 4a of the KG Quality Pipeline — two-phase entity merging.

| Function | Signature | Purpose | Returns | Raises |
|----------|-----------|---------|---------|--------|
| `merge_entities` | `(winner_id: str, loser_id: str, user_id: str) -> MergeResult` | Two-phase merge: Postgres transaction + Qdrant cleanup | `MergeResult` | `ValueError` (same ID, not found), `RuntimeError` (SQL error) |

See [§7.6 Two-Phase Merge](#76-pass-4a-two-phase-entity-merge) for step details.

### 3.14 `orchestrator/services/deduplication.py`

Pass 4b of the KG Quality Pipeline — Splink probabilistic deduplication.

| Function | Signature | Purpose | Returns |
|----------|-----------|---------|---------|
| `run_deduplication_job` | `(user_id: str = "default") -> dict` | Full dedup pipeline: blocking → scoring → routing | `{run_id, candidate_count, auto_merged, queued_for_review, duration_ms}` |

Never raises. See [§7.7 Splink Deduplication](#77-pass-4b-splink-probabilistic-deduplication) for details.

### 3.15 `orchestrator/services/entities.py`

Entity extraction, resolution, and storage. Modified by the quality pipeline:

- **Added**: Import and call to `entity_quality.score_and_filter_entities()` at the start of `store_entities()`
- **Added**: Import and call to `entity_constraints.run_batch_constraints()` at the end of `store_entities()`
- **Added**: `is_staged` parameter plumbing through `_insert_new_entity()` and `_upsert_entity()`
- **Added**: `extraction_quality` column write in `_insert_new_entity()`
- **Added**: Staged entity promotion logic in merge path of `_upsert_entity()` (triggered when quality exceeds `ENTITY_QUALITY_UPPER` or `mention_count >= ENTITY_PROMOTE_ON_MENTION_COUNT`)
- **Added**: `is_staged` field in `hooks.fire_background(Event.ENTITY_CREATED, ...)` call

### 3.16 `orchestrator/config.py`

Wiring layer. Added for Phase 3 / Quality Pipeline and KG Management Page:

| Function | Signature | Purpose |
|----------|-----------|---------|
| `get_graph_store` | `() -> GraphStore \| None` | Return FalkorDB singleton or `None` if `GRAPH_BACKEND != "falkordb"` |
| `get_stop_entity_set` | `() -> set[str]` | Cached set of lowercased stop phrases from `stop_entities.txt`. Mtime-based invalidation. |
| `get_stop_entities_path` | `() -> str` | Return resolved filesystem path to `stop_entities.txt`. Respects `STOP_ENTITIES_PATH` env var; falls back to `_resolve_config_file("stop_entities.txt")`. Does not check file existence. |
| `get_edge_quality_threshold` | `() -> float` | Alias for `get_graph_edge_quality_threshold()` — preserved for backward compatibility |
| `get_graph_edge_quality_threshold` | `() -> float` | Return min edge quality threshold (default 0.3). DB-first with env/hardcoded fallback. |
| `get_cooccurrence_threshold` | `() -> int` | Return min co-occurrence count (default 3). DB-first. |
| `get_graph_min_mention_count` | `() -> int` | Return min mention count for graph queries (default 2). DB-first. |
| `get_graph_max_cooccurrence_pairs` | `() -> int` | Return max RELATES_TO edge writes per ingestion event (default 100). DB-first. |
| `get_graph_viz_max_nodes` | `() -> int` | Return hard node cap for viz API (default 150). DB-first. |
| `get_graph_viz_max_edges` | `() -> int` | Return hard edge cap for viz API (default 300). DB-first. |
| `get_entity_quality_lower` | `() -> float` | Return discard threshold (default 0.35). DB-first. |
| `get_entity_quality_upper` | `() -> float` | Return staged-vs-normal threshold (default 0.60). DB-first. |
| `get_entity_promote_on_mention_count` | `() -> int` | Return mention count that auto-promotes staged entities (default 3). DB-first. |
| `get_decay_half_life_relates_to` | `() -> int` | Return RELATES_TO half-life in days (default 365). DB-first. |
| `get_decay_half_life_mentions` | `() -> int` | Return MENTIONS half-life in days (default 180). DB-first. |
| `get_decay_half_life_discussed_in` | `() -> int` | Return DISCUSSED_IN half-life in days (default 30). DB-first. |
| `get_dedup_cron_hour_utc` | `() -> int` | Return UTC hour for weekly dedup job (default 2). DB-first. |
| `get_scheduler` | `() -> BackgroundScheduler` | APScheduler singleton (created here, started in `main.py`) |
| `invalidate_settings_cache` | `() -> None` | Force the next `_get_setting` call to re-fetch from Postgres. Called by `POST /kg/settings` and `POST /kg/stop-entities` after successful writes. |

All `get_*` KG parameter functions use the fallback hierarchy: `kg_settings` table → environment variable → hardcoded default. See [§5.3 KG Settings (hot-reload)](#53-kg-settings-hot-reload) for details.

### 3.17 `orchestrator/main.py`

Added for Phase 3 / Quality Pipeline:

- Plugin router loading via `load_plugins()` at startup
- Weekly quality maintenance job registration via APScheduler (Sunday at `DEDUP_CRON_HOUR_UTC`, default 02:00 UTC)
- Scheduler startup and shutdown lifecycle

### 3.18 `orchestrator/routes/admin.py`

Added for the quality pipeline and KG Management Page (see **§6.0** for `AUTH_ENABLED` gating: most routes are **`require_admin`**, `POST /review-queue/decide` is **`require_user`**, `GET /graph/health` is global-auth middleware only):

| Endpoint | Purpose |
|----------|---------|
| `GET /graph/health` | Six KG quality metrics from Postgres (currently scoped to `user_id='default'` in SQL) |
| `GET /graph/mgm` | Serve the KG Management Page SPA (`orchestrator/static/graph_mgm.html`) |
| `GET /review-queue` | Legacy merge candidates (admin cross-user) |
| `GET /review-queue?source=all` | Unified prioritised queue across all four item types (admin) |
| `POST /review-queue/decide` | Process operator decisions (authenticated user; admin on-behalf allowed) |
| `POST /entities/merge` | Manual entity merge endpoint (admin) |
| `POST /entities/deduplicate` | Launch ad-hoc deduplication job (admin; returns 202) |
| `GET /kg/settings` | Return all 13 hot-reload KG settings with current value, type, default, and source |
| `POST /kg/settings` | Upsert one or more KG settings; invalidates TTL cache immediately |
| `DELETE /kg/settings/{key}` | Remove a setting from DB, reverting it to env var / hardcoded default |
| `GET /kg/job-status` | Return last-run timestamps and status for the three KG background jobs |
| `POST /kg/trigger-weekly` | Trigger the weekly KG quality maintenance job on demand (202); 409 if dedup already running |
| `GET /kg/stop-entities` | Return current stop entity phrases, count, and source path |
| `POST /kg/stop-entities` | Add or remove a stop entity phrase; atomic file write via `tempfile.mkstemp` + `os.replace` |

Added to `_BACKUP_TABLES`: `known_distinct_entity_pairs`, `review_decisions`, `deduplication_runs`, `dedup_candidates`, `kg_settings`. Excluded from backup: `edge_scores` (recomputable), `constraint_violations` (excluded).

### 3.19 `orchestrator/events.py`

Added events: `NOTE_CAPTURED`, `AUDIO_TRANSCRIBED`, `ENTITY_MERGED`.

### 3.20 `orchestrator/models/entities.py`

Added fields to `ExtractedEntity`:

```python
extraction_quality: float | None = None
is_staged: bool | None = None
```

Added `MergeResult` model:

```python
class MergeResult(BaseModel):
    winner_id: str
    loser_id: str
    aliases_merged: int
    relations_moved: int
    sessions_updated: int
    qdrant_cleaned: bool
```

### 3.21 `orchestrator/config/stop_entities.txt`

160 stop phrases across categories: meeting references, project references, people references, time/scheduling references, document references, generic phrases. Case-insensitive matching. Lines starting with `#` are comments.

---

## 4. Database Tables

### 4.1 Tables Created or Modified by the Graph/Quality Work

#### `entities` (modified)

| Column | Type | Default | Constraints | Purpose |
|--------|------|---------|-------------|---------|
| `entity_id` | `UUID` | `gen_random_uuid()` | `PRIMARY KEY` | Unique entity identifier |
| `name` | `TEXT` | — | `NOT NULL` | Entity name (original language) |
| `entity_type` | `TEXT` | — | `NOT NULL` | `PERSON \| ORG \| PROJECT \| CONCEPT` |
| `aliases` | `TEXT[]` | `'{}'` | `NOT NULL` | Alternative names |
| `context_tags` | `TEXT[]` | `'{}'` | `NOT NULL` | Domain/topic tags for resolution |
| `mention_count` | `INTEGER` | `1` | `NOT NULL` | Running count of mentions |
| `user_id` | `TEXT` | `'default'` | `NOT NULL` | User scope |
| `scope` | `TEXT` | `'personal'` | `NOT NULL, CHECK` | `personal` \| `shared` \| `system` — **ADR-015** (migration 013) |
| `published_from` | `UUID` | `NULL` | `FK → entities` | Non-null = published clone of a personal source row. **(migration 013)** |
| `created_at` | `TIMESTAMPTZ` | `NOW()` | `NOT NULL` | Row creation time |
| `updated_at` | `TIMESTAMPTZ` | `NOW()` | `NOT NULL` | Last modification time |
| `extraction_quality` | `DOUBLE PRECISION` | `NULL` | — | Heuristic quality score [0,1]. NULL = pre-Pass-1 row. **(Added by migration 004)** |
| `is_staged` | `BOOLEAN` | `FALSE` | `NOT NULL` | Staged = quarantined from graph/queries. **(Added by migration 004)** |
| `graph_projected_at` | `TIMESTAMPTZ` | `NULL` | — | Last successful FalkorDB projection. **(Added by migration 003)** |

**Indexes (selection):** also `entities_user_scope_idx` `(user_id, scope)`; partial unique `(published_from, scope)` where `published_from` is not null; see migration 013.

**Indexes (legacy in doc):**
- `idx_entities_staged` — `(user_id, is_staged) WHERE is_staged = TRUE` — fast lookup of staged entities
- `idx_entities_name_lower` — `LOWER(name)` — case-insensitive name lookup for CONTEXT_BUILDING
- `idx_entities_aliases_gin` — `GIN (aliases)` — alias search

#### `entity_relations` (modified)

| Column | Type | Default | Constraints | Purpose |
|--------|------|---------|-------------|---------|
| `id` | `SERIAL` | — | `PRIMARY KEY` | Row ID |
| `source_id` | `UUID` | — | `NOT NULL, FK → entities ON DELETE CASCADE` | Entity UUID |
| `relation_type` | `TEXT` | — | `NOT NULL` | Edge type in Postgres |
| `evidence_type` | `TEXT` | — | `NOT NULL` | `SESSION \| DOCUMENT` |
| `evidence_id` | `TEXT` | — | `NOT NULL` | Session UUID or file path |
| `user_id` | `TEXT` | `'default'` | `NOT NULL` | User scope |
| `created_at` | `TIMESTAMPTZ` | `NOW()` | `NOT NULL` | Row creation time |
| `evidence_granularity` | `TEXT` | `'document'` | `NOT NULL` | `sentence \| paragraph \| document`. **(Added by migration 004)** |

#### `sessions` (created by migration 003)

| Column | Type | Default | Constraints | Purpose |
|--------|------|---------|-------------|---------|
| `session_id` | `UUID` | — | `PRIMARY KEY` | Session identifier |
| `summary` | `TEXT` | `''` | `NOT NULL` | Session summary text |
| `topics` | `TEXT[]` | `'{}'` | `NOT NULL` | Extracted topics |
| `entities` | `TEXT[]` | `'{}'` | `NOT NULL` | Entity name strings (legacy) |
| `entity_ids` | `TEXT[]` | `'{}'` | `NOT NULL` | Resolved entity UUIDs (preferred) |
| `user_id` | `TEXT` | `'default'` | `NOT NULL` | User scope |
| `created_at` | `TIMESTAMPTZ` | `NOW()` | `NOT NULL` | Session start |
| `updated_at` | `TIMESTAMPTZ` | `NOW()` | `NOT NULL` | Last update (trigger-maintained) |
| `graph_projected_at` | `TIMESTAMPTZ` | `NULL` | — | Last FalkorDB projection |

#### `notes` (created by migration 003)

| Column | Type | Default | Constraints | Purpose |
|--------|------|---------|-------------|---------|
| `note_id` | `UUID` | `gen_random_uuid()` | `PRIMARY KEY` | Note identifier |
| `text` | `TEXT` | — | `NOT NULL` | Note content |
| `user_id` | `TEXT` | `'default'` | `NOT NULL` | User scope |
| `source` | `TEXT` | `'quick_capture'` | `NOT NULL` | Capture source |
| `created_at` | `TIMESTAMPTZ` | `NOW()` | `NOT NULL` | Creation time |
| `updated_at` | `TIMESTAMPTZ` | `NOW()` | `NOT NULL` | Last update (trigger-maintained) |
| `graph_projected_at` | `TIMESTAMPTZ` | `NULL` | — | Last FalkorDB projection |

#### `audio_memos` (created by migration 003)

| Column | Type | Default | Constraints | Purpose |
|--------|------|---------|-------------|---------|
| `audio_id` | `UUID` | `gen_random_uuid()` | `PRIMARY KEY` | Audio memo identifier |
| `file_path` | `TEXT` | — | `NOT NULL` | Path to audio file |
| `transcript` | `TEXT` | `NULL` | — | Transcription text |
| `duration_seconds` | `FLOAT` | `NULL` | — | Audio duration |
| `whisper_model` | `TEXT` | `NULL` | — | Whisper model used |
| `user_id` | `TEXT` | `'default'` | `NOT NULL` | User scope |
| `created_at` | `TIMESTAMPTZ` | `NOW()` | `NOT NULL` | Creation time |
| `updated_at` | `TIMESTAMPTZ` | `NOW()` | `NOT NULL` | Last update (trigger-maintained) |
| `transcribed_at` | `TIMESTAMPTZ` | `NULL` | — | When transcription completed |
| `graph_projected_at` | `TIMESTAMPTZ` | `NULL` | — | Last FalkorDB projection |

#### `file_index` (modified)

Added column: `graph_projected_at TIMESTAMPTZ` (migration 003).

#### `constraint_violations` (created by migration 005)

| Column | Type | Default | Constraints | Purpose |
|--------|------|---------|-------------|---------|
| `violation_id` | `UUID` | `gen_random_uuid()` | `PRIMARY KEY` | Violation identifier |
| `user_id` | `TEXT` | `'default'` | `NOT NULL` | User scope |
| `entity_id` | `UUID` | — | `FK → entities ON DELETE CASCADE` | Related entity |
| `rule_name` | `TEXT` | — | `NOT NULL` | Rule identifier |
| `severity` | `TEXT` | — | `NOT NULL, CHECK IN ('CRITICAL', 'WARNING', 'INFO')` | Severity level |
| `detail` | `TEXT` | `NULL` | — | Human-readable description |
| `detected_at` | `TIMESTAMPTZ` | `NOW()` | `NOT NULL` | Detection time |
| `resolved_at` | `TIMESTAMPTZ` | `NULL` | — | Resolution time (NULL = open) |

**Indexes:**
- `idx_constraint_violations_open` — `(user_id, severity, detected_at DESC) WHERE resolved_at IS NULL` — open violation queries
- `idx_constraint_violations_entity` — `(entity_id) WHERE resolved_at IS NULL` — per-entity violation lookup

#### `edge_scores` (created by migration 006)

| Column | Type | Default | Constraints | Purpose |
|--------|------|---------|-------------|---------|
| `id` | `BIGSERIAL` | — | `PRIMARY KEY` | Row ID |
| `user_id` | `TEXT` | `'default'` | `NOT NULL` | User scope |
| `entity_id_a` | `UUID` | — | `NOT NULL, FK → entities ON DELETE CASCADE` | First entity (canonical: a < b) |
| `entity_id_b` | `UUID` | — | `NOT NULL, FK → entities ON DELETE CASCADE` | Second entity |
| `ppmi_score` | `DOUBLE PRECISION` | `NULL` | — | PPMI score |
| `edge_quality` | `DOUBLE PRECISION` | `NULL` | — | Composite quality score [0,1] |
| `decay_factor` | `DOUBLE PRECISION` | `NULL` | — | Temporal decay factor |
| `last_evidence_at` | `TIMESTAMPTZ` | `NULL` | — | Most recent co-occurrence evidence |
| `computed_at` | `TIMESTAMPTZ` | `NOW()` | `NOT NULL` | When score was computed |

**Constraints:** `UNIQUE (user_id, entity_id_a, entity_id_b)`, `CHECK (entity_id_a < entity_id_b)`.  
**Indexes:** `idx_edge_scores_user` — `(user_id)`.  
**Not in `_BACKUP_TABLES`** — recomputable from `entity_relations` by the weekly quality job.

#### `known_distinct_entity_pairs` (created by migration 007)

| Column | Type | Default | Constraints | Purpose |
|--------|------|---------|-------------|---------|
| `user_id` | `TEXT` | `'default'` | `NOT NULL` | User scope |
| `entity_id_a` | `UUID` | — | `NOT NULL, FK → entities ON DELETE CASCADE` | First entity |
| `entity_id_b` | `UUID` | — | `NOT NULL, FK → entities ON DELETE CASCADE` | Second entity |
| `created_at` | `TIMESTAMPTZ` | `NOW()` | `NOT NULL` | When marked as distinct |

**Constraints:** `PRIMARY KEY (user_id, entity_id_a, entity_id_b)`, `CHECK (entity_id_a < entity_id_b)`.

#### `review_decisions` (created by migration 007)

| Column | Type | Default | Constraints | Purpose |
|--------|------|---------|-------------|---------|
| `decision_id` | `UUID` | `gen_random_uuid()` | `PRIMARY KEY` | Decision identifier |
| `user_id` | `TEXT` | `'default'` | `NOT NULL` | User scope |
| `item_type` | `TEXT` | — | `NOT NULL` | Queue item type |
| `item_id` | `TEXT` | — | `NOT NULL` | Queue item identifier |
| `action` | `TEXT` | — | `NOT NULL` | Action taken |
| `payload` | `JSONB` | `NULL` | — | Action details |
| `created_at` | `TIMESTAMPTZ` | `NOW()` | `NOT NULL` | When decision was made |

**Indexes:** `idx_review_decisions_user_time` — `(user_id, created_at DESC)`.

#### `deduplication_runs` (created by migration 008)

| Column | Type | Default | Constraints | Purpose |
|--------|------|---------|-------------|---------|
| `run_id` | `UUID` | `gen_random_uuid()` | `PRIMARY KEY` | Run identifier |
| `user_id` | `TEXT` | `'default'` | `NOT NULL` | User scope |
| `started_at` | `TIMESTAMPTZ` | `NOW()` | `NOT NULL` | Job start time |
| `finished_at` | `TIMESTAMPTZ` | `NULL` | — | Job completion time (NULL = in progress) |
| `candidate_count` | `INT` | `NULL` | — | Pairs evaluated |
| `auto_merged` | `INT` | `NULL` | — | Pairs auto-merged |
| `queued_for_review` | `INT` | `NULL` | — | Pairs sent to review queue |
| `known_distinct` | `INT` | `NULL` | — | Known distinct pairs at run time |
| `error_message` | `TEXT` | `NULL` | — | Error details if failed |

#### `kg_settings` (created by migration 009)

| Column | Type | Default | Constraints | Purpose |
|--------|------|---------|-------------|---------|
| `key` | `TEXT` | — | `PRIMARY KEY` | Setting key (e.g. `entity_quality_lower`) |
| `value` | `TEXT` | — | `NOT NULL` | Setting value, always stored as a string |
| `updated_at` | `TIMESTAMPTZ` | `NOW()` | `NOT NULL` | Last modification time (trigger-maintained) |

**Purpose**: Stores hot-reload KG quality and graph parameters that take effect without a container restart. Config getters read this table first and fall back to env vars. The table is writeable via `POST /kg/settings` and readable via `GET /kg/settings`.

**Reserved key prefix**: Keys beginning with `_job_` are internal job timestamp markers (`_job_last_reconciliation`, `_job_last_weekly`). They are written by background jobs and read by `GET /kg/job-status`; they are not exposed via `GET /kg/settings`.

**In `_BACKUP_TABLES`** — operator-customised settings are preserved across restores.

#### `dedup_candidates` (created by migration 008)

| Column | Type | Default | Constraints | Purpose |
|--------|------|---------|-------------|---------|
| `id` | `BIGSERIAL` | — | `PRIMARY KEY` | Row ID |
| `run_id` | `UUID` | — | `NOT NULL, FK → deduplication_runs ON DELETE CASCADE` | Parent run |
| `user_id` | `TEXT` | `'default'` | `NOT NULL` | User scope |
| `entity_id_a` | `UUID` | — | `NOT NULL, FK → entities ON DELETE CASCADE` | First entity |
| `entity_id_b` | `UUID` | — | `NOT NULL, FK → entities ON DELETE CASCADE` | Second entity |
| `match_probability` | `DOUBLE PRECISION` | — | `NOT NULL` | Splink match probability |
| `features` | `JSONB` | `NULL` | — | Feature vector used for scoring |
| `created_at` | `TIMESTAMPTZ` | `NOW()` | `NOT NULL` | Row creation time |

**Constraints:** `CHECK (entity_id_a < entity_id_b)`.  
**Indexes:**
- `idx_dedup_candidates_run` — `(run_id, match_probability DESC)`
- `idx_dedup_candidates_user` — `(user_id, match_probability DESC) WHERE match_probability >= 0.5`

### 4.2 Backup Table List

Tables in `_BACKUP_TABLES` (restored in this order):

```python
_BACKUP_TABLES = [
    "file_index",
    "entities",
    "entity_relations",
    "sessions",
    "review_queue",
    "known_distinct_entity_pairs",
    "review_decisions",
    "connector_permissions",
    "routine_do_tracking",
    "action_log",
    "deduplication_runs",
    "dedup_candidates",
    "kg_settings",
]
```

**Excluded from backup:**
- `edge_scores` — recomputable from `entity_relations` by the weekly quality job
- `constraint_violations` — recomputable by running constraint checks; transient quality data
- `notes`, `audio_memos` — not yet in the backup list (these tables were added in migration 003; adding them is a future improvement)

---

## 5. Environment Variables

### 5.1 Graph Plugin Variables

| Variable | Default | Type | Purpose | Read By |
|----------|---------|------|---------|---------|
| `GRAPH_BACKEND` | `"none"` | string | `"falkordb"` to enable graph; anything else disables | `config.get_graph_store()` |
| `FALKORDB_URL` | `"redis://falkordb:6379"` | string | Redis URL for FalkorDB | `config.get_graph_store()`, `falkordb_store.py` |
| `FALKORDB_GRAPH_NAME` | `"lumogis"` | string | Graph name inside FalkorDB | `config.get_graph_store()`, `falkordb_store.py` |
| `GRAPH_COOCCURRENCE_THRESHOLD` | `3` | int | Min co-occurrence count for RELATES_TO edges to appear in queries | `schema.py` |
| `GRAPH_MIN_MENTION_COUNT` | `2` | int | Min mention count for entities in CONTEXT_BUILDING | `schema.py` |
| `GRAPH_MAX_COOCCURRENCE_PAIRS` | `100` | int | Max RELATES_TO edges written per entity creation event | `schema.py` |
| `GRAPH_VIZ_MAX_NODES` | `150` | int | Hard node cap per viz API response | `viz_routes.py` |
| `GRAPH_VIZ_MAX_EDGES` | `300` | int | Hard edge cap per viz API response | `viz_routes.py` |
| `GRAPH_ADMIN_TOKEN` | `""` | string | Admin auth token for backfill endpoint | `routes.py` |
| `GRAPH_EDGE_QUALITY_THRESHOLD` | `0.3` | float | Min edge_quality for RELATES_TO edges in ego_network queries | `config.get_edge_quality_threshold()` |

### 5.2 Quality Pipeline Variables

| Variable | Default | Type | Purpose | Read By |
|----------|---------|------|---------|---------|
| `ENTITY_QUALITY_LOWER` | `0.35` | float | Below this: discard entity | `entity_quality.py` |
| `ENTITY_QUALITY_UPPER` | `0.60` | float | Below this (but >= lower): stage entity | `entity_quality.py`, `entities.py` |
| `ENTITY_PROMOTE_ON_MENTION_COUNT` | `3` | int | Staged entities promoted when mention_count reaches this | `entities.py` |
| `ENTITY_QUALITY_FAIL_OPEN` | `true` | bool | If true, scorer exceptions return original list unchanged | `entity_quality.py` |
| `STOP_ENTITIES_PATH` | _(auto-resolved)_ | string | Override path to stop entity phrase list | `config.get_stop_entity_set()` |
| `DECAY_HALF_LIFE_RELATES_TO` | `365` | float | Half-life in days for RELATES_TO temporal decay | `edge_quality.py` |
| `DECAY_HALF_LIFE_MENTIONS` | `180` | float | Reserved for future use | — |
| `DECAY_HALF_LIFE_DISCUSSED_IN` | `30` | float | Reserved for future use | — |
| `DEDUP_CRON_HOUR_UTC` | `2` | int | UTC hour for weekly quality maintenance job (Sunday) | `main.py` |
| `SPLINK_MODEL_PATH` | `"/workspace/splink_model.json"` | string | Trained Splink model persistence path | `deduplication.py` |

### 5.3 KG Settings (hot-reload)

All 13 quality and graph parameters above can be overridden at runtime via the `kg_settings` Postgres table without a container restart. The same parameters are exposed as environment variables (§5.1, §5.2) for initial configuration; the database value takes precedence when present.

#### The `kg_settings` table

Created by migration 009. Schema: `key TEXT PRIMARY KEY`, `value TEXT NOT NULL`, `updated_at TIMESTAMPTZ` (trigger-maintained). Values are always stored as strings regardless of the parameter's underlying type; type coercion happens in the getter.

Write via `POST /kg/settings`. Read via `GET /kg/settings`. Delete (revert to default) via `DELETE /kg/settings/{key}`. Backed up in `_BACKUP_TABLES`.

#### Fallback hierarchy

For every hot-reload parameter, the resolution order is:

1. **`kg_settings` table** — if a row exists for the key, its string value is used (cast to the declared type)
2. **Environment variable** — if no DB row, the corresponding env var is read (see §5.1 and §5.2)
3. **Hardcoded default** — if neither DB nor env var is set, the default in `_SETTING_META` is used

#### In-process TTL cache

All DB reads are routed through `config._get_setting()`, which maintains a process-wide cache with a 30-second TTL. Cache internals:

- `_settings_cache: dict[str, str]` — last full fetch of all `kg_settings` rows
- `_settings_cache_loaded_at: float` — monotonic timestamp of last fetch
- `_settings_cache_lock: threading.Lock` — guards cache writes
- `_SETTINGS_TTL = 30.0` seconds

On cache expiry, a full `SELECT key, value FROM kg_settings` is re-issued. A double-fetch on simultaneous expiry is accepted as preferable to blocking. `config.invalidate_settings_cache()` resets `_settings_cache_loaded_at` to `0.0`, forcing an immediate re-fetch on the next call. It is called by `POST /kg/settings` and `POST /kg/stop-entities`.

#### All 13 configurable keys

| Key | Type | Default | Env Var Fallback | Getter | Read By |
|-----|------|---------|-----------------|--------|---------|
| `entity_quality_lower` | float | `0.35` | `ENTITY_QUALITY_LOWER` | `get_entity_quality_lower()` | `entity_quality.py` |
| `entity_quality_upper` | float | `0.60` | `ENTITY_QUALITY_UPPER` | `get_entity_quality_upper()` | `entity_quality.py`, `entities.py` |
| `entity_promote_on_mention_count` | int | `3` | `ENTITY_PROMOTE_ON_MENTION_COUNT` | `get_entity_promote_on_mention_count()` | `entities.py` |
| `graph_edge_quality_threshold` | float | `0.3` | `GRAPH_EDGE_QUALITY_THRESHOLD` | `get_graph_edge_quality_threshold()` | `query.py` |
| `graph_cooccurrence_threshold` | int | `3` | `GRAPH_COOCCURRENCE_THRESHOLD` | `get_cooccurrence_threshold()` | `query.py`, `schema.py`, `viz_routes.py` |
| `graph_min_mention_count` | int | `2` | `GRAPH_MIN_MENTION_COUNT` | `get_graph_min_mention_count()` | `query.py`, `schema.py` |
| `graph_max_cooccurrence_pairs` | int | `100` | `GRAPH_MAX_COOCCURRENCE_PAIRS` | `get_graph_max_cooccurrence_pairs()` | `writer.py`, `schema.py` |
| `graph_viz_max_nodes` | int | `150` | `GRAPH_VIZ_MAX_NODES` | `get_graph_viz_max_nodes()` | `viz_routes.py` |
| `graph_viz_max_edges` | int | `300` | `GRAPH_VIZ_MAX_EDGES` | `get_graph_viz_max_edges()` | `viz_routes.py` |
| `decay_half_life_relates_to` | int | `365` | `DECAY_HALF_LIFE_RELATES_TO` | `get_decay_half_life_relates_to()` | `edge_quality.py` |
| `decay_half_life_mentions` | int | `180` | `DECAY_HALF_LIFE_MENTIONS` | `get_decay_half_life_mentions()` | reserved |
| `decay_half_life_discussed_in` | int | `30` | `DECAY_HALF_LIFE_DISCUSSED_IN` | `get_decay_half_life_discussed_in()` | reserved |
| `dedup_cron_hour_utc` | int | `2` | `DEDUP_CRON_HOUR_UTC` | `get_dedup_cron_hour_utc()` | `main.py` |

**Range validation** (enforced by `POST /kg/settings`):

| Key | Min | Max |
|-----|-----|-----|
| `entity_quality_lower` | 0.0 | 1.0 |
| `entity_quality_upper` | 0.0 | 1.0 |
| `graph_edge_quality_threshold` | 0.0 | 1.0 |
| `entity_promote_on_mention_count` | 1 | — |
| `graph_cooccurrence_threshold` | 1 | — |
| `graph_min_mention_count` | 1 | — |
| `graph_max_cooccurrence_pairs` | 1 | — |
| `graph_viz_max_nodes` | 1 | — |
| `graph_viz_max_edges` | 1 | — |
| `decay_half_life_relates_to` | 1 | — |
| `decay_half_life_mentions` | 1 | — |
| `decay_half_life_discussed_in` | 1 | — |
| `dedup_cron_hour_utc` | 0 | 23 |

#### Reserved `_job_` keys

Keys with the `_job_` prefix are written by background jobs and are not exposed via the `/kg/settings` API:

| Key | Written By | Read By |
|-----|-----------|---------|
| `_job_last_reconciliation` | `reconcile.py:run_reconciliation()` at job completion | `GET /kg/job-status` |
| `_job_last_weekly` | `edge_quality.py:run_weekly_quality_job()` at job completion | `GET /kg/job-status` |

Both values are ISO 8601 UTC timestamps. Writes are wrapped in `try/except` — failures are logged as WARNING and never raise.

#### `config.get_stop_entities_path()`

Returns the resolved filesystem path to `stop_entities.txt`. Used by `GET /kg/stop-entities` and `POST /kg/stop-entities` to locate the file without hardcoding the path. Respects `STOP_ENTITIES_PATH` env var; falls back to `_resolve_config_file("stop_entities.txt")` which checks `/app/config/` then `/opt/lumogis/config/`. Does not check whether the file exists — callers handle `FileNotFoundError`.

### 5.4 Service-Mode Variables

These variables are read by Core (orchestrator) and/or by the standalone `lumogis-graph` service. They have no effect when `GRAPH_MODE=inprocess`.

| Variable | Default | Type | Read By | Purpose |
|----------|---------|------|---------|---------|
| `GRAPH_MODE` | `"inprocess"` | enum | `config.get_graph_mode()`, `plugins/graph/__init__.py`, `main.py:_wire_graph_mode_handlers`, `routes/chat.py:_inject_context`, `services/tools.py:register_query_graph_proxy` | `inprocess` \| `service` \| `disabled`. Selected once at orchestrator startup; see §1.6. |
| `KG_SERVICE_URL` | `"http://lumogis-graph:8001"` | string | `services/graph_webhook_dispatcher.py`, `services/tools.py` | Base URL for Core → KG service HTTP calls (webhooks, `/context`, `/tools/query_graph`). |
| `GRAPH_WEBHOOK_SECRET` | `""` | string | Core dispatcher (`Authorization: Bearer ...`); KG `routes/webhook.py:check_webhook_auth` (`hmac.compare_digest`); same gate is reused on `/context`. | Shared secret authenticating Core → KG calls on `/webhook` and `/context`. When unset, see `KG_ALLOW_INSECURE_WEBHOOKS`. |
| `KG_ALLOW_INSECURE_WEBHOOKS` | `false` (process default); `true` in `docker-compose.premium.yml` for local dev | bool | KG `config.kg_allow_insecure_webhooks()` | When `GRAPH_WEBHOOK_SECRET` is unset, controls whether unauthenticated callers are accepted (`true` → 202) or rejected (`false` → 503). **Never set to `true` in production**: combined with an empty secret it disables auth on `/webhook` and `/context`. Has no effect once a secret is set. |
| `GRAPH_ADMIN_TOKEN` | `""` | string | KG service admin write routes (e.g. `POST /webhook` admin variants, future write endpoints) | When set, KG service requires `X-Graph-Admin-Token` on admin write paths. Independent of webhook auth. |
| `KG_SERVICE_PORT` | `8001` | int | KG service uvicorn binding | Port the KG service listens on inside its container. |
| `KG_SCHEDULER_ENABLED` | `true` | bool | KG service `main.py` lifespan | When `false` AND `GRAPH_MODE=service`, the KG service does NOT register reconciliation or weekly jobs (useful in tests, parity runs, and when running multiple KG replicas where only one should schedule). |
| `KG_MANAGEMENT_URL` | `"http://lumogis-graph:8001/mgm"` | string | KG service `CapabilityManifest` builder | Absolute URL advertised by `GET /capabilities` for the operator-facing UI. In production this should point to the reverse-proxy URL of the KG service. |
| `KG_MEM_LIMIT` | `4g` | size | `docker-compose.premium.yml` | Memory cap for the KG container. Override to `1g` on small dev machines. |
| `KG_MEM_RESERVATION` | `512m` | size | `docker-compose.premium.yml` | Memory reservation for the KG container. |
| `LUMOGIS_CORE_BASE_URL` | `"http://orchestrator:8000"` | string | KG service (when it needs to call back into Core, e.g. for shared utilities) | Base URL the KG service uses to reach Core. |
| `CAPABILITY_SERVICE_URLS` | `""` | comma-list | Core capability discovery | Comma-separated list of capability service base URLs Core should poll for `GET /capabilities`. Set to `http://lumogis-graph:8001` to enable discovery in `service` mode. |

**Auth matrix for KG `/webhook` and `/context`** (implemented in `routes/webhook.py:check_webhook_auth`, reused by `routes/context.py`):

| `GRAPH_WEBHOOK_SECRET` set | `KG_ALLOW_INSECURE_WEBHOOKS` | Result |
|---------------------------|-----------------------------|--------|
| Yes | (any) | Bearer token required: 202 on match, 401 on mismatch / missing |
| No | Yes | Accepted without auth → 202 (dev only) |
| No | No | Rejected → 503 with `detail="webhook auth not configured"` (KG still starts; every call returns 503) |

---

## 6. API Endpoints

### 6.0 Core authentication matrix (`AUTH_ENABLED`)

| `AUTH_ENABLED` | Effect |
|----------------|--------|
| `false` (default in many dev `.env` files) | `require_user` / `require_admin` are **no-ops**. `get_user` returns a **synthetic** `user_id="default"`, `role=admin` context so legacy `curl` without headers still works. |
| `true` (family-LAN) | Unauthenticated API calls to protected routes return **401**. Admin routes require a JWT whose role is **admin**. Some routes (notably `POST /review-queue/decide`) use **`require_user`**: a normal user may act on their own queue items; an admin may act for others. |

**Graph read endpoints** (`/graph/ego`, `/graph/path`, `/graph/search`, `/graph/stats`, `/graph/viz`) resolve **`user_id` from the JWT** when `AUTH_ENABLED=true` (see `plugins/graph/viz_routes._require_auth`). `user_id` is **never** read from query parameters.

**Admin / operator JSON routes on Core** (all require **`require_admin`** when `AUTH_ENABLED=true`): `GET /graph/mgm`, `GET/POST/DELETE /kg/settings`, `GET /kg/job-status`, `POST /kg/trigger-weekly`, `GET/POST /kg/stop-entities`, `GET /review-queue` (incl. `?source=all`), `POST /entities/merge`, `POST /entities/deduplicate`. **`POST /review-queue/decide`** uses **`require_user`** (not `require_admin`).

**`GET /graph/health`** is **not** a public bypass: with `AUTH_ENABLED=true` the **auth middleware** requires a **Bearer** token (`test_graph_health.py`).

**`POST /graph/backfill`**: uses `plugins/graph/routes._check_admin` — **not** `require_admin` (any authenticated user when `AUTH_ENABLED=true`, plus `X-Graph-Admin-Token` when `GRAPH_ADMIN_TOKEN` is set).

**Operator HTML** is served from `GET /graph/mgm` (Core, admin-gated when auth is on) and the KG service’s `GET /mgm`.

### 6.1 Graph Plugin Endpoints

#### `POST /graph/backfill`

Trigger a one-time graph reconciliation (privilege model in `plugins/graph/routes._check_admin`, **not** `require_admin`).

- **Auth**: If `AUTH_ENABLED=true`, any **authenticated** user may call the route (401 if unauthenticated). If `GRAPH_ADMIN_TOKEN` is set in the environment, **`X-Graph-Admin-Token`** must match it (403 otherwise). When `AUTH_ENABLED=false` and `GRAPH_ADMIN_TOKEN` is empty, the route is open (dev default).
- **Parameters**: `limit_per_type` (query, optional int) — cap per table
- **Response 202**: `{"status": "backfill_started", "limit_per_type": ..., "message": ...}`
- **Response 401**: Not authenticated
- **Response 403**: Admin token mismatch
- **Response 409**: Backfill already running
- **Response 503**: Graph store not configured

#### `GET /graph/ego`

Ego network for a named entity.

- **Auth**: Standard auth when `AUTH_ENABLED=true`; user_id from JWT
- **Parameters**: `entity` (required), `depth` (default 1, capped at 1), `limit` (default 50, max `GRAPH_VIZ_MAX_NODES`), `min_strength` (default 0, floored to `COOCCURRENCE_THRESHOLD`)
- **Response**: `{available, found, entity_id, entity_name, entity_type, nodes: [{id, label, type, mention_count, center}], edges: [{source, target, type, strength}], truncated, node_count, edge_count}`
- **Graph unavailable**: `{available: false, message: ...}`

#### `GET /graph/path`

Shortest path between two named entities.

- **Auth**: Standard
- **Parameters**: `from_entity` (required), `to_entity` (required), `max_depth` (default 4, max 4)
- **Response**: `{available, found, path_found, path_length, from_entity, to_entity, nodes, edges, truncated, node_count, edge_count}`

#### `GET /graph/search`

Entity name autocomplete.

- **Auth**: Standard
- **Parameters**: `q` (required, min 2 chars), `limit` (default 10, max 20)
- **Response**: `{results: [{entity_id, name, type, mention_count}]}`

#### `GET /graph/stats`

Summary statistics.

- **Auth**: Standard
- **Response**: `{available, node_count, edge_count, top_entities: [{name, type, mention_count}], cooccurrence_threshold}` — `cooccurrence_threshold` reflects the live `kg_settings` value via `config.get_cooccurrence_threshold()`

#### `GET /graph/viz`

Serve the Cytoscape.js visualization HTML page.

- **Auth**: Standard
- **Response**: HTML file (`orchestrator/static/graph_viz.html`)
- **Response 404**: File not found

### 6.2 Quality Pipeline Endpoints

#### `GET /graph/health`

Six KG quality metrics from Postgres (never queries FalkorDB). All SQL in the handler currently scopes aggregations to **`user_id = 'default'`** (admin dashboard / legacy bucket).

- **Auth**: Unauthenticated when `AUTH_ENABLED=false`. When `AUTH_ENABLED=true`, **requires Bearer JWT** (same as other non-exempt API routes) — 401 if missing/invalid.
- **Response 200**:

```json
{
  "duplicate_candidate_count": 0,
  "orphan_entity_pct": 0.0,
  "mean_entity_completeness": 0.0,
  "constraint_violation_counts": {"CRITICAL": 0, "WARNING": 0, "INFO": 0},
  "ingestion_quality_trend_7d": null,
  "temporal_freshness": {"last_7d": 0, "8_30d": 0, "31_90d": 0, "90d_plus": 0}
}
```

- **Response 503**: Postgres unreachable

#### `GET /review-queue`

Legacy review queue (backward compatible).

- **Auth**: **`require_admin`** when `AUTH_ENABLED=true` (cross-user god-mode; items include `user_id` / `scope` for badges).
- **Response**: Array of `{id, reason, created_at, candidate_a: {name, type}, candidate_b: {name, type}}`

#### `GET /review-queue?source=all`

Unified prioritised review queue.

- **Auth**: **`require_admin`**
- **Response**: `{items: [...], next_cursor: null}`
- **Item types and priorities**:
  - `ambiguous_entity` (priority 1.0) — merge candidates with `{candidate_a, candidate_b, reason}`
  - `constraint_violation` (priority 0.9) — CRITICAL violations with `{violation: {rule_name, severity, detail, entity_id}}`
  - `staged_entity` (priority 0.7) — entities awaiting promotion with `{entity: {entity_id, name, entity_type, extraction_quality, mention_count}}`
  - `orphan_entity` (priority 0.5) — entities with no edges with `{entity: {entity_id, name, entity_type, mention_count, created_at}}`

#### `POST /review-queue/decide`

Process an operator decision.

- **Auth**: **`require_user`** — non-admins may only act on their own `user_id`; admins can act for any item (see ADR-023 and `orchestrator/routes/admin.py`).
- **Request body**: `{item_type, item_id, action, user_id?}`
- **Valid actions per item type**:
  - `ambiguous_entity`: `merge` (merges entities), `distinct` (adds to known_distinct_entity_pairs, deletes queue row)
  - `staged_entity`: `promote` (sets is_staged=FALSE, graph_projected_at=NULL), `discard` (deletes entity)
  - `constraint_violation`: `suppress` (sets resolved_at=NOW())
  - `orphan_entity`: `dismiss` (sets resolved_at=NOW())
- **Response**: `{status: "ok", action: ..., result: ...}`

#### `POST /entities/merge`

Manual entity merge.

- **Auth**: **`require_admin`**
- **Request body**: `{winner_id: UUID, loser_id: UUID, user_id: str}` (owner of the data; default `"default"` in the Pydantic model for back-compat)
- **Response 200**: `{status: "ok", winner_id, loser_id, aliases_merged, relations_moved, sessions_updated, qdrant_cleaned}`
- **Response 400**: Same winner/loser, invalid UUID
- **Response 404**: Entity not found
- **Response 500**: SQL error

#### `POST /entities/deduplicate`

Launch ad-hoc deduplication job.

- **Auth**: **`require_admin`**
- **Response 202**: `{status: "started", run_id: UUID}`
- **Response 409**: Deduplication already running

### 6.3 KG Settings and Management Endpoints

#### `GET /kg/settings`

Return all 13 hot-reload KG settings.

- **Auth**: **`require_admin`**
- **Response 200**:

```json
{
  "settings": [
    {
      "key": "entity_quality_lower",
      "value": 0.35,
      "type": "float",
      "default": 0.35,
      "source": "default",
      "description": "..."
    }
  ]
}
```

- `source` is `"database"` when the value comes from `kg_settings` table; `"default"` when using env var or hardcoded default.
- Values are returned as native Python types (float, int) for the response.

#### `POST /kg/settings`

Upsert one or more KG settings.

- **Auth**: **`require_admin`**
- **Request body**: `{settings: [{key: string, value: string}]}` — **value must be a string** even for numeric types; Pydantic will reject JSON numbers.
- **Response 200**: `{status: "ok", updated: [key, ...]}`
- **Response 400**: Unknown key, type mismatch, or out-of-range value
- **Response 500**: DB write failed
- Calls `config.invalidate_settings_cache()` on success — new value visible within the current request.

#### `DELETE /kg/settings/{key}`

Remove a setting from the DB, reverting it to its env var / hardcoded default.

- **Auth**: **`require_admin`**
- **Response 200**: `{status: "ok", key: string, reverted_to: <default_value>}`
- **Response 400**: Unknown key
- **Response 500**: DB delete failed

#### `GET /graph/mgm`

Serve the Knowledge Graph Management Page single-page application.

- **Auth**: **`require_admin`**
- **Response 200**: `text/html` — `orchestrator/static/graph_mgm.html`
- **Response 404**: HTML file not found on disk

#### `GET /kg/job-status`

Return last-run timestamps and status for the three KG background jobs. Reads `_job_last_reconciliation` and `_job_last_weekly` directly from `kg_settings` (bypassing the 30s TTL cache). Derives deduplication status from `deduplication_runs`. Returns graceful nulls on any field failure.

- **Auth**: **`require_admin`**
- **Response 200**:

```json
{
  "reconciliation": {
    "last_run": "2026-04-15T03:00:00+00:00"
  },
  "weekly_quality": {
    "last_run": "2026-04-13T02:00:00+00:00"
  },
  "deduplication": {
    "last_run": "2026-04-13T02:05:00+00:00",
    "running": false,
    "last_auto_merged": 3,
    "last_queued_for_review": 7,
    "last_candidate_count": 42
  }
}
```

All fields are nullable. `running` is always a boolean (never null).

#### `POST /kg/trigger-weekly`

Trigger the weekly KG quality maintenance job on demand as a FastAPI `BackgroundTask`.

- **Auth**: **`require_admin`**
- **Response 202**: `{status: "started", message: "Weekly KG quality job started in background."}`
- **Response 409**: A deduplication job is already running (the weekly job includes deduplication and cannot run concurrently). Detail message explains the conflict.
- **Response 500**: DB error when checking for in-progress runs

#### `GET /kg/stop-entities`

Return the current stop entity list.

- **Auth**: **`require_admin`**
- **Response 200**: `{phrases: [string, ...], count: int, source_path: string}` — phrases are sorted alphabetically (case-insensitive). Returns empty list with count 0 when the file does not exist.
- **Response 500**: File exists but cannot be read (permissions error, etc.)

#### `POST /kg/stop-entities`

Add or remove a phrase from the stop entity list. Writes atomically via `tempfile.mkstemp` + `os.replace()` — never leaves a partially-written file.

- **Auth**: **`require_admin`**
- **Request body**: `{action: "add" | "remove", phrase: string}`
- **Validation**: action must be `"add"` or `"remove"`; phrase after strip must be non-empty, ≤ 200 characters, no newlines or control characters (ASCII < 32).
- **Response 200**: `{status: "ok", count: <new_count>}`
- **Response 400**: Invalid action; empty phrase; phrase too long; phrase contains newlines/control chars; `"add"` with a phrase already in list (case-insensitive); `"remove"` with a phrase not in list
- **Response 500**: File read or write error
- Calls `config.invalidate_settings_cache()` on success.

### 6.4 lumogis-graph Service Endpoints

These endpoints live on the `lumogis-graph` container, NOT in Core. They exist only when `GRAPH_MODE=service`. The service listens on `KG_SERVICE_PORT` (default 8001) and is reached at `KG_SERVICE_URL` (default `http://lumogis-graph:8001`). All routes share the same Postgres + Qdrant + FalkorDB as Core.

**Auth is not identical to Core** (§6.0). The KG process uses `services/lumogis-graph/auth.py:auth_middleware` plus per-route gates:

| Layer | Behaviour |
|-------|-----------|
| **Always open (no JWT)** | `GET /health`, `POST /webhook`, `POST /context`, `GET /capabilities` — middleware bypasses JWT; service-specific auth is **Bearer `GRAPH_WEBHOOK_SECRET`** on `/webhook` and `/context` via `check_webhook_auth` (§5.4 matrix). |
| **`AUTH_ENABLED=false` (KG default)** | Middleware injects a synthetic `UserContext(user_id="default", role="admin")` for every other path — **LAN-trusted** posture. |
| **`AUTH_ENABLED=true` on the KG container** | Middleware requires a **valid Lumogis JWT** (same `AUTH_SECRET` as Core) on **all routes except** the open list above. **`GET /mgm` requires `role=admin`** in the JWT (`auth._requires_admin`). The middleware docstring still mentions `/api/graph` / `/api/viz` prefixes; **actual routes use `/graph/...`** — graph JSON and HTML are **any authenticated user**, not admin-only, matching `graph/viz_routes._require_auth`. |
| **`X-Graph-Admin-Token`** | `routes/graph_admin_routes._require_admin` enforces this header **when `GRAPH_ADMIN_TOKEN` is set** on **mutating** admin routes (`POST`/`DELETE` on `/kg/*`, `GET /graph/health` on the KG copy, `POST /kg/stop-entities`, `POST /kg/trigger-weekly`). When `GRAPH_ADMIN_TOKEN` is **empty**, that check is a no-op (dev default). |

**`graph_admin_routes` read vs write (token header):**

- **Unauthenticated reads (no `_require_admin` in handler):** `GET /kg/settings`, `GET /kg/job-status`, `GET /kg/stop-entities`.
- **Reads gated by `_require_admin` (X-Graph-Admin-Token when `GRAPH_ADMIN_TOKEN` set):** `GET /graph/health` **only on the KG service** — Core’s `GET /graph/health` uses **Core** global auth instead (§6.2).
- **Writes all call `_require_admin`:** `POST`/`DELETE` on `/kg/settings`, `POST /kg/trigger-weekly`, `POST /kg/stop-entities`.

**Core vs KG — same path, different envelope:** On **Core**, `GET /kg/*` and `GET /graph/mgm` use **`require_admin`** (JWT role). On **KG**, with **`AUTH_ENABLED=true`**, `GET /kg/settings` only needs **any** valid JWT (middleware does not require admin for that path). Prefer **calling operator JSON APIs on Core** for a single auth story; use **KG direct** for `/webhook`, `/context`, service health, and Docker-internal checks.

#### Service-internal contract (called by Core)

| Endpoint | Auth | Caller | Purpose |
|----------|------|--------|---------|
| `POST /webhook` | Bearer (`GRAPH_WEBHOOK_SECRET`); see §5.4 auth matrix | `services/graph_webhook_dispatcher.py` (Core) | Receive `WebhookEnvelope` (`event`, `schema_version`, `payload`), validate, enqueue projection on `webhook_queue.submit()`. Returns 202 immediately; projection runs on the KG service's background ThreadPoolExecutor. |
| `POST /context` | Same Bearer gate | `routes/chat.py:_inject_context` (Core, on every chat request when `GRAPH_MODE=service`) | Run `graph.query.on_context_building` for `query`. Hard 35 ms in-route budget; Core wraps the call in a 40 ms client timeout. Returns `{fragments: list[str]}`. |

The `WebhookEnvelope` model is defined in `services/lumogis-graph/models/webhook.py` (and mirrored in `orchestrator/services/graph_webhook_dispatcher.py`):

```python
class WebhookEnvelope(BaseModel):
    schema_version: int            # SUPPORTED_SCHEMA_VERSIONS = [1]
    event: WebhookEvent            # enum value == graph.writer.on_<value> handler name
    payload: dict                  # validated against _PAYLOAD_BY_EVENT[event]
```

`WebhookEvent` enum values: `on_document_ingested`, `on_entity_created`, `on_session_ended`, `on_note_captured`, `on_audio_transcribed`, `on_entity_merged`. The enum value equals the handler function name in `graph.writer` by design.

#### Public service endpoints (operator, debugging, and browser UIs)

| Endpoint | Auth on KG | Purpose |
|----------|------------|---------|
| `GET /health` | **Open** (middleware bypass) | Liveness + readiness. Docker healthcheck, capability poller. |
| `GET /capabilities` | **Open** (middleware bypass) | `CapabilityManifest`; `management_url` from `KG_MANAGEMENT_URL`. |
| `GET /mgm` | `AUTH_ENABLED=false`: open. `AUTH_ENABLED=true`: **Bearer JWT with `role=admin`** (middleware) | Serves `static/graph_mgm.html` with `LUMOGIS_CORE_BASE_URL` injected. Host port often not published; use Core’s `/graph/mgm` or exec/curl. |
| `POST /tools/query_graph` | **`GRAPH_WEBHOOK_SECRET`** Bearer (`check_webhook_auth`, same as `/webhook`) | Core’s `query_graph` proxy. Not “open LAN”. |
| `POST /graph/backfill` | Same **`_check_admin`** as Core’s `graph/routes.py` (JWT if `AUTH_ENABLED` on **KG** + `X-Graph-Admin-Token` when `GRAPH_ADMIN_TOKEN` set) | Stale-row reconciliation. |
| `GET /graph/ego`, `GET /graph/path`, `GET /graph/search`, `GET /graph/stats`, `GET /graph/viz` | `AUTH_ENABLED=false`: open. `AUTH_ENABLED=true`: **Bearer JWT** (any role); `viz_routes` scopes `user_id` from `get_user` | Same handlers as Core’s plugin; on KG, middleware enforces JWT when auth is on. |
| `GET /graph/health` | **`_require_admin` in `graph_admin_routes`** → `X-Graph-Admin-Token` when `GRAPH_ADMIN_TOKEN` set. If `AUTH_ENABLED=true`, **Bearer JWT** is also required (path is not in middleware open list). | Same six metrics as §6.2; **not** the same auth story as **Core** `/graph/health` (see §6.2). |
| `GET /kg/settings` | `AUTH_ENABLED=true`: **Bearer JWT** (any role, unlike Core’s admin-only). No `X-Graph-Admin-Token` on GET. | Read knobs from shared `kg_settings`. |
| `POST /kg/settings`, `DELETE /kg/settings/{key}` | JWT if `AUTH_ENABLED` + **`_require_admin`** → `X-Graph-Admin-Token` when `GRAPH_ADMIN_TOKEN` set | Write knobs. |
| `GET /kg/job-status` | JWT if `AUTH_ENABLED` | Job timestamps. |
| `GET /kg/stop-entities` | JWT if `AUTH_ENABLED` | Read stop list. |
| `POST /kg/trigger-weekly` | JWT if `AUTH_ENABLED` + **`_require_admin`** (admin token header when set) | Triggers `run_weekly_quality_job` in-process on KG. |
| `POST /kg/stop-entities` | JWT if `AUTH_ENABLED` + **`_require_admin`** (admin token header when set) | Mutate stop file on KG’s filesystem. |
| `/mcp/*` | **`MCP_AUTH_TOKEN`** (separate mount in `main.py`) | FastMCP tools. |

#### Core → KG client contracts

| Core module | Calls | Timeout / failure mode |
|-------------|-------|------------------------|
| `services/graph_webhook_dispatcher.py` | `POST /webhook` per fired event (`ENTITY_CREATED`, `SESSION_ENDED`, `NOTE_CAPTURED`, `AUDIO_TRANSCRIBED`, `ENTITY_MERGED`, `DOCUMENT_INGESTED`) | Fire-and-forget on a Core ThreadPoolExecutor. Network errors are logged at WARNING and dropped — reconciliation in the KG service will replay missed projections within 24 h. |
| `routes/chat.py:_inject_context` | `POST /context` once per chat turn | 40 ms client timeout. On timeout / non-200 / connection error the chat path proceeds with an empty `[Graph]` context — the only observable effect is the chat response missing graph-derived hints for that turn. |
| `services/tools.py:_query_graph_proxy_handler` | `POST /tools/query_graph` per LLM tool call | Configurable per-call; failures surface to the LLM as a tool error. |
| Capability discovery (`routes/capabilities.py`) | `GET /capabilities` on each entry in `CAPABILITY_SERVICE_URLS` | Polled once at startup and on demand. Failures are logged; the manifest is simply not registered. |

---

## 7. Quality Pipeline

### 7.1 Pass 1 — Heuristic Entity Scoring

**File**: `orchestrator/services/entity_quality.py`

Scores each extracted entity on five signals:

| Signal | Weight | Function | Score Range |
|--------|--------|----------|-------------|
| **Stop list absence** | 0.35 | `_score_stop_absence` | 1.0 if not in stop set; 0.0 if in stop set (hard-clamps total to 0.0) |
| **Capitalisation** | 0.25 | `_score_capitalisation` | 1.0 if any non-first token is title/allcaps; 0.5 if only first token; 0.2 if all lower |
| **Determiner absence** | 0.15 | `_score_determiner_absence` | 1.0 if no leading determiner ("the", "a", etc.); 0.35 if starts with one |
| **Length sanity** | 0.15 | `_score_length_sanity` | 1.0 for 2–120 chars (non-digit); 0.0 for <2 or pure digits; linear decay 120–240 |
| **Multi-token** | 0.10 | `_score_multi_token` | 1.0 if >= 2 tokens; 0.6 if single token |

**Composite formula**: `extraction_quality = Σ(weight × signal)`, clamped to [0, 1].  
Stop list membership hard-clamps to 0.0 regardless of other signals.

**Routing thresholds**:

| Tier | Condition | Effect |
|------|-----------|--------|
| **Discard** | `quality < ENTITY_QUALITY_LOWER` (default 0.35) | Entity is dropped before Postgres write |
| **Staged** | `0.35 <= quality < ENTITY_QUALITY_UPPER` (default 0.60) | `is_staged=TRUE`; excluded from graph projection and queries |
| **Normal** | `quality >= 0.60` | `is_staged=FALSE`; fully visible in graph |

**Staged entity promotion**: A staged entity is promoted to normal when:
- A new mention merges into it AND quality exceeds `ENTITY_QUALITY_UPPER`, OR
- `mention_count >= ENTITY_PROMOTE_ON_MENTION_COUNT` (default 3)

On promotion, `is_staged` is set to `FALSE` and `graph_projected_at` is set to `NULL` (triggers reconciliation re-projection).

### 7.2 Stop Entity List

**File**: `orchestrator/config/stop_entities.txt`

160 phrases in categories: meeting references ("the meeting", "this call"), project references ("the project"), people references ("the client", "the team"), time references ("this week", "next steps"), document references ("the document", "the report"), generic phrases ("the issue", "the solution").

Loaded by `config.get_stop_entity_set()` with mtime-based cache invalidation. Adding a new phrase takes effect on the next `store_entities()` call without restart.

### 7.3 Pass 2 — Constraint Validation

**File**: `orchestrator/services/entity_constraints.py`

#### Per-Ingest Rules (called on every `store_entities()`)

| Rule | Severity | Condition | Auto-resolves |
|------|----------|-----------|---------------|
| `person_name_required` | `CRITICAL` | Person entity with empty/null name | Yes, when name is populated |
| `organisation_name_required` | `CRITICAL` | Org entity with empty/null name | Yes |
| `no_self_loop` | `CRITICAL` | `entity_relations` row where `source_id::text == evidence_id` | Yes, when self-referencing row removed |
| `valid_edge_type` | `CRITICAL` | Relation type not in allowed set | Yes |
| `person_completeness` | `INFO` | Person entity with zero `MENTIONS` relation rows | Yes, when a MENTIONS row is added |

Allowed edge types: `MENTIONED_IN_SESSION`, `MENTIONED_IN_DOCUMENT`, `RELATED_TO`, `MENTIONS`, `RELATES_TO`, `DISCUSSED_IN`, `DERIVED_FROM`, `WORKED_ON`.

#### Corpus-Level Rules (called by weekly job)

| Rule | Severity | Condition | Auto-resolves |
|------|----------|-----------|---------------|
| `orphan_entity` | `WARNING` | Non-staged entity with zero edges, created > 7 days ago | Yes, when entity gains edges |
| `alias_uniqueness` | `WARNING` | Two distinct entities sharing the same alias value | Yes, when alias conflict resolved |

### 7.4 Pass 3 — Edge Quality Scoring

**File**: `orchestrator/services/edge_quality.py`

#### PPMI Formula

```
P(x)    = count_distinct_evidence(x) / total_distinct_evidence
P(x,y)  = count_distinct_evidence(x ∩ y) / total_distinct_evidence
PPMI(x,y) = max(0, log₂(P(x,y) / (P(x) × P(y))))
```

Co-occurrence is evidence-based: two entities co-occur when they both appear in the same `evidence_id`.

#### Evidence Granularity Weights

| Granularity | Weight |
|-------------|--------|
| `sentence` | 1.0 |
| `paragraph` | 0.7 |
| `document` | 0.4 (default for all pre-Pass-3 rows) |

The effective granularity weight for a pair is the average of both sides' weights.

#### Temporal Decay Formula

```
decay_factor(t) = 0.5 ^ (days_since_last_evidence / half_life_days)
```

| Edge Type | Half-Life | Env Var |
|-----------|-----------|---------|
| `RELATES_TO` | 365 days | `DECAY_HALF_LIFE_RELATES_TO` |
| `MENTIONS` | 180 days | `DECAY_HALF_LIFE_MENTIONS` (reserved) |
| `DISCUSSED_IN` | 30 days | `DECAY_HALF_LIFE_DISCUSSED_IN` (reserved) |

Returns 1.0 if `last_evidence_at` is None. Clamped to [0.0, 1.0].

#### Composite Edge Quality Formula

```
edge_quality = 0.25 × normalised_frequency
             + 0.35 × ppmi_score_normalised
             + 0.20 × window_weight
             + 0.20 × decay_factor
```

Weights sum to 1.0. Normalisation uses max value across all pairs for the same user. Result clamped to [0.0, 1.0].

- `normalised_frequency` = `raw_count / max_count`
- `ppmi_score_normalised` = `ppmi_score / max_ppmi`
- `window_weight` = weighted average granularity across all evidence for the pair

### 7.5 Edge Quality Filtering in Queries

In `ego_network` queries (both M3 query path and M4 viz path):

- Edges with `edge_quality IS NULL` (before the first weekly job run): only `co_occurrence_count >= COOCCURRENCE_THRESHOLD` is required
- Edges with a non-NULL `edge_quality`: must pass both `co_occurrence_count >= COOCCURRENCE_THRESHOLD` AND `edge_quality >= GRAPH_EDGE_QUALITY_THRESHOLD` (default 0.3)

This is the transition predicate that handles the period before the weekly scoring job has ever run.

### 7.6 Pass 4a — Two-Phase Entity Merge

**File**: `orchestrator/services/entity_merge.py`

#### Phase A — Single Postgres Transaction

1. **Verify**: Both entities exist and belong to `user_id`. Raises `ValueError` if not.
2. **Redirect relations**: `UPDATE entity_relations SET source_id = winner WHERE source_id = loser`
3. **Update sessions**: `UPDATE sessions SET entity_ids = array_replace(entity_ids, loser, winner)`
4. **Merge metadata**: Union aliases, union context_tags, sum mention_counts onto winner
5. **Null projection stamp**: `SET graph_projected_at = NULL` on winner (triggers re-projection)
6. **Delete loser**: `DELETE FROM entities WHERE entity_id = loser` (review_queue rows CASCADE)
7. **Commit**

#### Phase B — Best-Effort Qdrant Cleanup

- Delete Qdrant points matching loser `entity_id`
- Re-upsert winner point with merged aliases
- On failure: logs ERROR, sets `qdrant_cleaned=False` — Postgres commit stands

#### Failure Contracts

- Phase A failure → transaction rolled back, no hook fired, no Phase B
- Phase B failure → Postgres commit stands, `qdrant_cleaned=False`, graph state may be temporarily inconsistent
- `ENTITY_MERGED` hook fires after Phase A commit, outside the transaction

### 7.7 Pass 4b — Splink Probabilistic Deduplication

**File**: `orchestrator/services/deduplication.py`

#### Blocking Strategy

A candidate pair must pass at least one blocker:

1. **Type-based**: Only compare entities of the same `entity_type`
2. **Qdrant ANN**: Top-10 nearest neighbours per entity (user_id filtered, cosine similarity)
3. **Attribute**: Entities sharing the first 2 characters of normalised (lowercased, stripped) name

Union of all three blockers, minus `known_distinct_entity_pairs`.

#### Scoring Features

| Feature | Computation |
|---------|------------|
| `jaro_winkler_name` | Jaro-Winkler similarity on normalised full names |
| `entity_type_match` | 1.0 if same type, 0.0 otherwise |
| `embedding_cosine` | Cosine similarity from Qdrant ANN results |
| `alias_match` | 1.0 if either entity has an alias matching the other's name |

#### Splink Model

- Comparisons: Jaro-Winkler at thresholds on name, exact match on entity_type, exact match on alias_match
- Link type: `dedupe_only`
- Training: EM with blocking rule `(l.entity_type = r.entity_type)`, u-estimation via random sampling (max 100k pairs)
- Persistence: JSON at `SPLINK_MODEL_PATH` (default `/workspace/splink_model.json`). Load if exists, train fresh if missing/corrupt, save after training.

#### Decision Thresholds

| Match Probability | Action |
|-------------------|--------|
| `>= 0.85` | Auto-merge if BOTH entities have `mention_count >= 2`; otherwise queue for review |
| `0.50 – 0.85` | Insert into `dedup_candidates` + `review_queue` for human decision |
| `< 0.50` | Ignored |

#### Auto-Merge Safety Guard

Auto-merge requires BOTH entities to have `mention_count >= 2`. Single-mention entities are too uncertain and are routed to the review queue instead.

#### Winner Selection

Higher `mention_count` wins. Tie-break: lower UUID string (lexicographic).

#### Fallback Scoring

If Splink `predict()` fails, a simple weighted feature scorer is used:

```python
prob = 0.45 * jaro_winkler_name
     + 0.25 * entity_type_match
     + 0.20 * embedding_cosine
     + 0.10 * alias_match
```

---

## 8. Weekly Maintenance Job

**Registered in**: `orchestrator/main.py`  
**Entry point**: `services/edge_quality.py:run_weekly_quality_job()`  
**Schedule**: APScheduler cron, Sunday at `DEDUP_CRON_HOUR_UTC` (default 02:00 UTC)  
**APScheduler config**: `misfire_grace_time=60`, `coalesce=True`, `max_instances=1`

### Steps in Order

| Step | Function | Produces | Can Fail Independently |
|------|----------|----------|----------------------|
| 1. Edge quality scoring | `run_edge_quality_job("default")` | Upserts `edge_scores` rows, updates `RELATES_TO` edge properties in FalkorDB | Yes |
| 2a. Orphan entity check | `check_orphan_entities("default")` | Inserts/resolves `constraint_violations` rows | Yes |
| 2b. Alias uniqueness check | `check_alias_uniqueness("default")` | Inserts/resolves `constraint_violations` rows | Yes |
| 3. Probabilistic deduplication | `run_deduplication_job("default")` | Auto-merges, inserts `dedup_candidates` + `review_queue` rows, updates `deduplication_runs` | Yes |

**Per-household note:** the weekly job still passes **`user_id="default"`** to these steps. Multi-user households that never remapped the legacy id may see correct work; for purely non-`default` data, a **per-user (or all-users) job** is not yet the shipped behaviour—run targeted maintenance per user in future work.

### Failure Isolation

Each step is wrapped in its own try/except. A failure in step 1 does not prevent steps 2 or 3 from running. A failure in step 3 does not affect the results of steps 1 and 2. The overall job never raises — it returns a combined summary dict and logs a structured INFO message.

### Separate Reconciliation Job

The daily reconciliation job (`plugins/graph/reconcile.py:run_reconciliation`) runs independently at 03:00 daily (not part of the weekly quality job). It is registered by `plugins/graph/__init__.py`.

### 8.1 Job Ownership by Mode

The same APScheduler entries exist in both the Core process and the KG service container; only one of the two should ever schedule them in a given deployment. The selection is made by `GRAPH_MODE` (Core side) and `KG_SCHEDULER_ENABLED` (KG side).

| Job | `GRAPH_MODE=inprocess` | `GRAPH_MODE=service` (default `KG_SCHEDULER_ENABLED=true`) | `GRAPH_MODE=service`, `KG_SCHEDULER_ENABLED=false` |
|-----|------------------------|---------------------------------------------------------|--------------------------------------------------|
| Reconciliation (daily 03:00 UTC) | Core | KG service | Nobody — operator must trigger via `POST /graph/backfill` |
| Weekly quality maintenance (Sun `DEDUP_CRON_HOUR_UTC`) | Core (`main.py`) | KG service | Nobody — operator must trigger via `POST /kg/trigger-weekly` |
| Live webhook projection | Core in-process hooks | KG `webhook_queue` ThreadPoolExecutor | Same as middle column (the queue does not depend on the scheduler) |

The "nobody" column is the supported configuration for multi-replica KG deployments where exactly one replica should schedule cron work; all replicas still accept and process webhook traffic.

---

## 9. Knowledge Graph Management Page

**URL**: `GET /graph/mgm`  
**File**: `orchestrator/static/graph_mgm.html`  
**Served by**: `routes/admin.py:graph_mgm()` via `FileResponse`

A single self-contained HTML file that serves as the operator console for the knowledge graph subsystem. No build pipeline, no npm, no bundler — Alpine.js and Cytoscape.js are loaded from CDN with `onerror` fallbacks to `/static/` for offline deployments.

### Tabs

| Tab | Hash | Content |
|-----|------|---------|
| **Overview** | `#overview` | Six health metric cards (`GET /graph/health`), job status panel (`GET /kg/job-status`), and three quick-action buttons (Trigger Backfill, Trigger Deduplication, Trigger Weekly Job). Auto-refreshes health metrics every 60 seconds. |
| **Graph** | `#graph` | Full Cytoscape.js graph visualization ported from `graph_viz.html`: ego network, shortest path, and mentions mode; entity autocomplete; node type filter checkboxes; edge strength slider; node info panel; theme-aware styling. |
| **Review Queue** | `#queue` | Unified review queue (`GET /review-queue?source=all`) grouped into four accordion sections (Ambiguous Entities, Critical Violations, Staged Entities, Orphan Entities) with per-item action buttons. Tab label shows unreviewed item count. |
| **Settings** | `#settings` | All 13 hot-reload KG settings organized into five accordion groups with per-group save (`POST /kg/settings`), per-key reset (`DELETE /kg/settings/{key}`), source badges, and info tooltips. Stop entity list management (`GET /kg/stop-entities`, `POST /kg/stop-entities`) below the groups. |

### Technology choices

- **Alpine.js 3.14.8** — declarative reactivity without a build step. All API-returned text rendered via `x-text` (never `x-html`).
- **Cytoscape.js 3.28.1** — graph visualization. Instance created when the Graph tab activates and destroyed when leaving, preventing memory leaks on tab switches.
- **CDN with local fallback** — both libraries attempt CDN load; `onerror` falls back to `/static/cytoscape.min.js` and `/static/alpine.min.js` for air-gapped deployments.

### Shared state with other pages

- **`lm-theme` localStorage key** — dark/light theme preference shared across the management page, `graph_viz.html`, and `dashboard/index.html`.
- **`/graph/viz`** — the standalone Cytoscape visualization page remains accessible at its original URL and is suitable as a lightweight alternative. It is no longer linked from the LibreChat footer (now points to `/graph/mgm`).

### LibreChat footer

The `Knowledge Graph` footer link in LibreChat points to `/graph/mgm`:
- **`orchestrator/librechat_config.py`**: `_graph_url = f"{_ext_url}/graph/mgm"`
- **`config/librechat.coldstart.yaml`**: `customFooter` markdown link updated to `/graph/mgm`

---

## 10. Known Limitations and Deferred Work

### 10.1 Explicitly Not Built

| Item | Why | Prerequisite |
|------|-----|--------------|
| **Multi-hop ego network** (depth > 1) | Produces too many results without additional ranking heuristics. Depth parameter accepted for API forward-compatibility but capped at 1. | Ranking heuristic design |
| **Edge types `DERIVED_FROM`, `LINKS_TO`, `TAGGED_WITH`** | Reserved in schema but not implemented. `DERIVED_FROM` requires audio→transcript linking; `LINKS_TO` and `TAGGED_WITH` require the vault adapter (M6). | M6 vault adapter, M7 audio pipeline |
| **Sentence/paragraph granularity evidence** | `evidence_granularity` column exists and weights are defined, but all current extraction produces `'document'` granularity only. | Sentence-level NER or chunked extraction |
| **Per-edge-type decay** | Half-lives defined for `MENTIONS` and `DISCUSSED_IN` but only `RELATES_TO` decay is computed. | Separate decay computation per edge type in the weekly job |
| **Drift detection** | Not implemented. Would monitor graph metrics over time and alert on degradation. | Baseline metric collection, alerting infrastructure |
| **Chat `[Graph]` user scope** | `on_context_building` **hard-codes `user_id="default"`** in both Core and `lumogis-graph` (the hook has no `user_id`; the KG `/context` handler does not yet thread `ContextRequest.user_id` into the function). | Pass `user_id` into the handler, or add `user_id` to the `CONTEXT_BUILDING` hook and fire it from `routes/chat` after scoping. |
| **Viz + graph JSON APIs** | **Solved (JWT scoping).** `viz_routes` uses `_require_auth` → `user_id` from JWT when `AUTH_ENABLED=true`. | — |
| **GET /graph/health** | **Still aggregates only `user_id="default"`** in SQL. | Per-user or all-users admin metrics. |
| **GET /graph/stats global counts** | **May still use `user_id="default"` in Cypher** (debt item FP-042 / BL-042). | Wire stats to the same scoping as `/graph/ego`. |
| **Qdrant-based semantic entity resolution** | Deferred from M3. Entity resolution in queries uses deterministic Postgres name/alias lookup (plus `visible_filter` for multi-scope). Fuzzy/semantic fallback remains in `query_entity` (tools.py) only. | Evaluation of resolution quality vs. latency |
| **FalkorDB `from_url()` constructor** | Documented in the plan but does not exist in falkordb v1.6.x. Per-call `FalkorDB(host, port).select_graph(name)` is used instead. | falkordb package update |
| **`constraint_violations` in backup** | Excluded because violations are transient and recomputable. If persistent violation history is needed, add to `_BACKUP_TABLES`. | Decision on retention requirements |
| **`sessions`, `notes`, `audio_memos` in backup** | These tables were added in migration 003 after the backup/restore system was built. They are not yet in `_BACKUP_TABLES`. | Add to backup table list |

### 10.2 Discrepancies Between Plan and Implementation

- **ADR-007 and ADR-008**: The finalised ADRs exist at `docs/decisions/007-graph-plugin-architecture.md` and `docs/decisions/008-graph-provenance-model.md` (note: filenames omit the `ADR-` prefix).
- **ADR-011**: `docs/decisions/011-lumogis-graph-service-extraction.md` records the lumogis-graph extraction.
- **FalkorDB constructor**: Plan specified `FalkorDB.from_url(url)`. Implementation uses `FalkorDB(host=host, port=port)` because `from_url` does not exist in falkordb v1.6.x.
- **`CONTEXT_BUILDING` user_id**: The hook is still **kwargs without `user_id`**. The implementation hard-codes `"default"`. In **`GRAPH_MODE=service`**, Core’s chat path passes the real `user_id` in the **HTTP body to KG `/context`**, but the KG `post_context` handler does not yet pass that `user_id` into `on_context_building` (same hard-coded `"default"` in `services/lumogis-graph/graph/query.py` until a small wiring PR lands).

---

## 11. Parity Verification

`tests/integration/test_graph_parity.py` verifies that `GRAPH_MODE=inprocess` and `GRAPH_MODE=service` produce byte-identical FalkorDB graph state for the same fixture set. It is the canonical regression test for the lumogis-graph extraction.

### What it does

1. Brings up the live stack twice with the same Postgres + Qdrant + FalkorDB volumes, swapping `GRAPH_MODE` between phases.
2. Ingests the fixture set (`tests/fixtures/`) via Core's `/ingest` endpoint mounted at `/fixtures` inside the orchestrator container (mount defined in `docker-compose.parity.yml`).
3. Waits for the projection queue to drain (`tests/integration/wait_for_idle.py`).
4. Snapshots FalkorDB and diffs the two snapshots (`tests/integration/diff_snapshots.py`). Differences fail the test.

### How to run

```
make test-graph-parity
```

The Make target wires the right compose overlays and runs `pytest -m integration tests/integration/test_graph_parity.py`. It is destructive: the named Docker volumes for Postgres, FalkorDB, and Qdrant are wiped between phases. The dev stack should be `docker compose down -v` first.

### Compose overlay layout

| Overlay | When loaded | Purpose |
|---------|-------------|---------|
| `docker-compose.yml` | Both phases | Core stack |
| `docker-compose.falkordb.yml` | Both phases | Pinned to `falkordb/falkordb:v4.18.1` (the `v4.4.4` tag was retired from Docker Hub) |
| `docker-compose.parity.yml` | Both phases | Mounts `tests/fixtures` at `/fixtures` (NOT `/data/fixtures`, because `/data` is bind-mounted read-only by the base file) |
| `docker-compose.premium.yml` | `service` phase only | Brings up the `lumogis-graph` container |
| `docker-compose.parity-premium.yml` | `service` phase only | Exposes `lumogis-graph:8001` to the host so the test can poll `/health` directly |

The test deliberately starts only a subset of services (`orchestrator`, `falkordb`, `postgres`, `qdrant`, `mongodb`, `ollama`, `stack-control`, plus `lumogis-graph` in the second phase) — `librechat` is excluded because its healthcheck blocks `docker compose up --wait` indefinitely on a cold cache.
