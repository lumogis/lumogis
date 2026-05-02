# ADR: Knowledge graph visualization (M1–M3)
**Status:** Finalised  
**Created:** 2026-04-12  
**Last updated:** 2026-04-15  
**Decided by:** /explore (agent)

## Context

Phases M1–M3 add durable graph storage (optional FalkorDB), write hooks, reconciliation, and assistant-facing reads (`query_graph`, context injection). Users and operators still need a **human-visible** view of entities and relationships. The stack is **local-first**, the dashboard is **vanilla HTML/JS**, and the graph plugin is the natural owner of graph-specific HTTP surfaces. Visualization must be **bounded**, **user-scoped**, and must not require a new cloud service.

## Decision

Provide **first-party, read-only graph exploration** using **Cytoscape.js**, driven by a **bounded JSON subgraph API** implemented in the graph plugin (not raw browser Cypher). The primary operator UI is the **Knowledge Graph Management Page** served at **`GET /graph/mgm`** (`orchestrator/static/graph_mgm.html`), which combines graph visualization with health metrics, review queue, and settings management in a single tabbed interface using **Alpine.js** for reactivity. The standalone `GET /graph/viz` page remains functional but is no longer the primary linked surface. The core decision stands: **extend Cytoscape** (layouts via extensions/Web Workers, export, filters, LOD) rather than adopting a new framework or WebGL stack unless measured scale requires it. Document **FalkorDB Browser** as an optional **operator-sidecar** for power users who run the full FalkorDB image/UI locally, without treating it as the primary end-user experience.

## Alternatives Considered

- **vis-network** — faster MVP with physics defaults; not chosen as default due to weaker graph-theory tooling vs Cytoscape for path-centric UX (see exploration).
- **Sigma.js + Graphology** — better for very large graphs; deferred until scale is proven.
- **Apache ECharts** — attractive if standardising all dashboard charts; otherwise heavier than a dedicated graph library.
- **cosmos.gl** — high performance but incubating; spike only.
- **D3 custom** — maximum flexibility, unjustified maintenance for this feature.
- **React Flow** — strong for editable diagrams; conflicts with current non-React dashboard unless toolchain changes.
- **Export-only (Gephi/Graphviz)** — useful complement, not sufficient as primary UI.
- **AntV G6** — strong productised graph framework; heavier toolchain/bundle than extending Cytoscape for the current single-page app.
- **Neo4j NVL** — embeddable WebGL stack; licence and npm-bundler friction vs MIT Cytoscape for AGPL core unless explicitly vetted.
- **react-force-graph / React island** — viable if the project adopts a bundler for this surface; unnecessary while static Cytoscape meets requirements.

Full evaluation: *(maintainer-local only; not part of the tracked repository)* (initial), *(maintainer-local only; not part of the tracked repository)* (full-featured page, 2026-04-15).

## Consequences

- **Easier:** Operators and users can **see** provenance-linked structure (sessions, documents, entities) without learning Cypher for basic exploration.
- **Easier:** Stays aligned with **optional graph** — UI degrades gracefully when `GRAPH_BACKEND=none`.
- **Harder:** Requires explicit **auth policy** for any graph read route (especially when `AUTH_ENABLED=false`).
- **Harder:** Large graphs demand **limits**, **progressive disclosure**, and possibly a future migration to WebGL (Sigma) if ego networks routinely exceed comfortable canvas limits.

## Revisit conditions

- Ego-network responses routinely exceed **~3k graph elements** in production profiles and UX degrades — revisit **Sigma.js** or **server-side layout + preset coordinates**.
- Dashboard adopts **React** and **editable** graph workflows — revisit **React Flow** or hybrid embedding.
- **cosmos.gl** (or similar) graduates with stable APIs and clear browser support — revisit for massive-graph mode.
- Product mandates **one charting stack** for all analytics — revisit **Apache ECharts** as the single dependency.

## Status history

- 2026-04-12: Draft created by /explore
- 2026-04-15: Revised during /review-plan --arbitrate R1 — primary UI changed from `GET /graph/viz` to `GET /graph/mgm` (unified management page with Alpine.js + Cytoscape.js). Core Cytoscape decision unchanged; the viz page remains but is no longer the primary linked surface.
- 2026-04-15: Revisited by /explore — implementation now ships `/graph/viz` + Cytoscape; decision reaffirmed for "full-featured page" via **extension of Cytoscape** and bounded API (see v2 exploration).
- 2026-04-15: Finalised by /verify-plan — implementation confirmed decision. All five passes delivered: `/graph/mgm` serving `graph_mgm.html` with Overview, Graph, Review Queue, and Settings tabs; Alpine.js + Cytoscape.js from CDN; hot-reload settings via `kg_settings` table; atomic stop-entity file writes; 23/23 tests passing. Finalised copy at `docs/decisions/009-knowledge_graph_visualization.md`.
