# ADR-002: Graph capabilities as an optional extension (Protocol + overlay)

## Context

Graph storage — nodes for people, organisations, documents, and the relationships
between them — is a legitimate building block for many extensions. It should be
possible for contributors to build graph plugins against a stable interface
without graph infrastructure affecting the default installation.

The naive approach (add a graph database to `docker-compose.yml`) imposes an
extra service on every user regardless of whether they build graph plugins. It
also implicitly commits us to one backend, discouraging alternatives.

## Decision

Graph capability is exposed through a Protocol (`ports/graph_store.py`) that
any backend can implement. Core defines the schema contract (`docs/graph-schema.md`)
but ships no graph adapter and no graph database in the default stack.

FalkorDB is provided as the **reference backend** via an optional overlay
(`docker-compose.falkordb.yml`). Contributors who want to develop or use a
graph plugin start FalkorDB with one command and write plugins against the
`GraphStore` Protocol. Users who do not need graph functionality install and
run nothing extra.

This is the same pattern used throughout the stack: each optional capability
(graph, workflow automation, LLM proxy) has a Protocol in `ports/`, a reference
adapter in `adapters/`, and an optional Docker Compose overlay. See
`docs/extending-the-stack.md` for the full list.

### Why FalkorDB as the reference backend

| Concern | FalkorDB |
|---|---|
| Licence | MIT — permissive for downstream plugin authors |
| Query language | Cypher — widely known, schema documented in `docs/graph-schema.md` |
| Transport | Redis protocol — minimal operational overhead, familiar tooling |
| Footprint | Single container, low memory — suitable for self-hosters and edge |

## Consequences

- **Default installation is unaffected.** No graph service runs unless the
  overlay is explicitly enabled.
- **Backend is swappable.** Neo4j, Memgraph, or any Cypher-compatible store can
  implement `GraphStore` and be used in place of FalkorDB.
- **Community graph plugins are first-class.** The schema contract is public,
  the Protocol is stable, and a reference backend is always available for
  local development.
- **No Redis dependency in core.** FalkorDB and its Redis runtime live entirely
  in the overlay and are not referenced by any service in the default stack.
