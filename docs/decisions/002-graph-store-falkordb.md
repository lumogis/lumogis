# ADR-002: FalkorDB as graph store

## Context

The graph plugin (proprietary, lumogis-app) needs a property graph for entities and relationships. Neo4j, Memgraph, and FalkorDB were considered.

## Decision

Use **FalkorDB** as the graph backend, Redis-protocol compatible, MIT-licensed.

## Consequences

- **Licence:** MIT avoids AGPL/commercial friction for optional graph features in downstream products.
- **Redis protocol:** Fits existing Redis operational patterns; single container in compose.
- **Lightweight:** Lower resource footprint than a full Neo4j deployment for self-hosters.
- **Embeddable:** Suitable for edge and small deployments.
- **Cypher:** Team and contributors familiar with Cypher can query the graph; schema documented in `docs/graph-schema.md`.
