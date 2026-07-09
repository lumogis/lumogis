# ADR-134: Code-structure ingest for the coding KG — native tree-sitter, not CodeGraph-as-is

**Status:** Finalised
**Created:** 2026-06-25
**Last updated:** 2026-06-25
**Decided by:** /explore (LUM-301); finalised by /verify-plan
**Linear:** LUM-301
**Scope:** APP / proprietary — ships in the strip-listed `services/lumogis-graph/` (paid KG add-in, `CapabilityLicenseMode.COMMERCIAL`, off by default). Per ADR-101 / ADR-042, only the `GraphStore` Protocol/port stays AGPL-public.

## Context

The Lumogis coding bank has a decision layer (LUM-291/294: `CodingDecision`, `Failure`, `Session`). LUM-301 asked whether to add a **structure layer** — `Component`/`Library` entities and `calls`/`depends_on`/`implements` edges from a tree-sitter parse — and whether **CodeGraph** (github.com/colbymchenry/codegraph, MIT) should be the source. The constraint that shaped the option space: CodeGraph is **TypeScript/Node + SQLite**, while Lumogis is a local-first **Python** appliance with an established graph **port → projector → event** seam (`ports/graph_store.py`, `services/lumogis-graph/graph/writer.py`); tree-sitter alone is heuristic at cross-file call resolution.

## Decision

Build a **native Python tree-sitter ingest adapter** (Lumogis-owned) that parses configured repo roots and writes `Component`/`Library` nodes and `CALLS`/`DEPENDS_ON`/`IMPLEMENTS` edges into the coding bank's FalkorDB graph through the existing `GraphStore` port and a dedicated projector. **Adapt** CodeGraph's MIT taxonomy/patterns as reference; do **not** adopt its Node runtime, run it as a sidecar, or stand up a second graph store.

## Alternatives Considered

- **CodeGraph as-is (Node sidecar)** — rejected: adds a Node runtime + second graph store to a Python local-first deployment; no control over taxonomy mapping.
- **Bridge CodeGraph CLI (subprocess → FalkorDB)** — rejected: still needs Node; brittle coupling to an external SQLite schema for parsing we can do in-process.
- **Adopt a Python engine (CodeGraphContext MIT / Blarify)** — not as primary: right language, but its own graph schema re-introduces an impedance layer; kept as reference + optional **SCIP** accuracy booster.

See `.cursor/explorations/LUM-301-codegraph-code-structure-ingest.md` for full detail.

## Implementation notes (deviations recorded at finalisation)

The implementation matched the core decision (native tree-sitter, no CodeGraph runtime). Three sub-decisions were refined during implementation, all preserving the ADR's local-first/offline intent:

1. **Per-language grammar wheels** (`tree-sitter-python`, `tree-sitter-typescript`) instead of `tree-sitter-language-pack`. The language-pack (kreuzberg-dev v1.x) **downloads grammars from GitHub at runtime**, which fails offline and violates local-first. The per-language wheels bundle the compiled grammar — no network.
2. **Direct AST traversal** instead of `.scm` query files — the tree-sitter query capture API is version-unstable; traversal over `node.type`/`child_by_field_name` is robust and fully unit-testable. (`queries/*.scm` were not created.)
3. **Dedicated `project_code_structure` projector** (entity↔entity edges, MERGE by `lumogis_id`) rather than routing through `_ensure_source_node` (the document→MENTIONS→entity provenance pattern, which does not fit Component→CALLS→Component). Node id keyed on `sha1(bank|qualname|kind)` (qualname already encodes the file path), enabling cross-file `DEPENDS_ON` endpoint matching on MERGE.

## Consequences

**Easier:** single graph store (FalkorDB); full control of symbol→entity-type mapping and bank placement; in-process (no new Docker/Node); reuses LUM-294 types, the projector, and bank isolation; cross-domain decision↔structure queries via the existing `graph.*` MCP tools.

**Harder / foreclosed:** we own per-language tree-sitter extraction logic; rules out the standalone Node sidecar and a second SQLite graph. tree-sitter call accuracy is heuristic across files — v1 ships intra-file calls + import edges; precise cross-file resolution (SCIP/LSP) is a deferred additive phase.

**Downstream must know:** adds a `CALLS` token to `RELATION_TYPES` (AGPL core, additive — also auto-expands `add_memory`'s relation-extraction prompt); needs a code-structure `evidence_type="CODE_STRUCTURE"` provenance tag (graph-internal); the graph `ENTITY_TYPE_MAP` sync for `Component`/`Library` (**LUM-533**) keeps projection fidelity; the projector edge path coordinates with **LUM-528**.

## Revisit conditions

- If CodeGraph/CodeGraphContext ships a **stable, offline** Python library that writes into an external graph store with a mappable schema, revisit "build vs adopt."
- If intra-file + import-edge precision proves insufficient, escalate to SCIP/LSP augmentation (Blarify / scip-python).
- If `Component`-node volume materially impacts FalkorDB or bank isolation (LUM-293), revisit retention policy for structure nodes.

## Status history
- 2026-06-25: Draft created by /explore (LUM-301).
- 2026-06-25: Finalised by /verify-plan — implementation confirmed the decision (native tree-sitter, no CodeGraph runtime); three sub-decisions recorded above (offline grammar wheels, AST traversal, dedicated entity↔entity projector).
