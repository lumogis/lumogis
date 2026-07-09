# ADR-150: Temporal KG core — build clean-room, do not wrap Graphiti (LUM-558)

**Status:** Accepted (awaiting human checkpoint — exec:guided, risk:public-private-boundary)
**Created:** 2026-07-02
**Last updated:** 2026-07-02
**Decided by:** /explore LUM-558 (Fable 5); evidence pinned to getzep/graphiti main `f4330dc0bb39a3dd7356cdb67d0cc8ae16e5de53` (2026-07-02)

## Context

LUM-104 (bi-temporal edges, contradiction detection, provenance on the FalkorDB KG) is blocked on a go/no-go: wrap **Graphiti** (getzep/graphiti, Apache-2.0, v0.29.2, first-party FalkorDB driver) as the temporal-KG core, or build proprietary on the shipped single-axis `valid_until` leg (ADR-133, TEMPR recall fusion). Constraints shaping the decision: ADR-015 (one household graph with scope-column union, because FalkorDB cannot cross-graph union — the load-bearing constraint), ADR-138 (coarse per-bank FalkorDB graphs, `group_id=bank` precedent from LUM-293), ADR-042 (KG export boundary; single strip-list source; `GRAPH_MODE` default `disabled`), ADR-066 (external-memory-service foreclosure), and the local-model + latency posture (Ollama/LiteLLM). Full evidence: `.cursor/explorations/LUM-558-graphiti-build-vs-wrap.md`.

## Decision

**Build clean-room.** Do not wrap Graphiti and do not fork it. LUM-104 implements the four-timestamp edge schema (`created_at` / `valid_at` / `invalid_at` / `expired_at`), a contradiction-resolution procedure, and a `SUPERSEDES` edge directly on the existing premium KG layer (`services/lumogis-graph/`), keeping ADR-015's scope union and MERGE keys and ADR-138's per-bank graphs exactly as shipped, and keeping ADR-133's Postgres `valid_until` recall contract authoritative (graph-edge `invalid_at` must sync to `entity_edges.valid_until`). Graphiti is a **reference implementation only**: borrow its timestamp semantics, its `resolve_edge_contradictions` interval logic (with the `ensure_utc()` discipline), its date-extraction prompt rules, its refusal to skip contradiction detection on ongoing ingestion, and a concurrency cap for local-model calls.

## Per-section findings

1. **ADR-066 reconciliation — outside the foreclosure; ADR-066 untouched.** The foreclosure as written rejects external memory *services* ("extra services, Neo4j, cloud APIs"; "doubles the persistence layer"; "requires new Docker services"). Graphiti-as-library on the existing FalkorDB is none of those. However, ADR-066's redundancy leg — "no capability that cannot be reproduced with … derived properties on a FalkorDB node" — applies directly and supports build. Because the verdict is build, no ADR-066 revisit is needed or recorded.
2. **ADR-015 conformance gate — FAIL (decisive).** On Graphiti's FalkorDB driver, `group_id` is a **separate physical graph** (`driver.clone(database=group_id)`), and multi-group reads are merged **application-side** (`handle_multiple_group_ids`) — the pattern ADR-015 explicitly ruled out. Within a group, Graphiti has no seam for ADR-015's visibility predicate: `SearchFilters.property_filters` is declared but consumed nowhere in `graphiti_core` at the pinned commit, and node resolution/dedup has no scope-conditional MERGE key, so personal rows and shared projections of the same entity would be merged, breaking the publish/unpublish projection model. Wrap-with-fork would mean forking the search constructors and write/dedup pipeline — the subsystems upstream is actively rearchitecting (unmerged PR #1209 would invert the FalkorDB isolation model; open races #1325/#1331).
3. **Strip-list / export boundary — no changes needed for build.** All build work lands in already-stripped paths (`services/lumogis-graph/`, `orchestrator/adapters/falkordb_store.py`, `orchestrator/plugins/graph/`). `GRAPH_MODE=disabled` default confirmed intact (`orchestrator/config.py`). Recorded gap: `scripts/check-public-export.sh` is deny-list-based and does **not** fail closed on an *unlisted* new source tree (e.g. a hypothetical `orchestrator/vendor/graphiti/` would export silently); only `apps/` has a subtree guard. This gap needs its own Linear outcome (P3 hardening) regardless of this verdict.
4. **ADR-133 reconciliation — not reopened.** Its revisit condition ("if FalkorDB becomes the default backend") is not met; `GRAPH_MODE` default remains `disabled`. Two-axis `valid_at`/`invalid_at` on premium graph edges is additive at a different layer and does not supersede the Postgres single-axis `valid_until` contract, which stays mandatory on every read path.
5. **Adoption readiness (for the record):** #1001 (`add_triplet` UUID-null on FalkorDB) fixed/closed (PR #1013; current main copies the full edge map via `SET r = edge`). #893 (naive/aware datetime in `resolve_edge_contradictions`) still open but Kuzu-scoped; the FalkorDB-relevant symptom is defensively fixed on main via `ensure_utc()` wrapping. Newer open FalkorDB defects (#1325 routing skip, #1331 shared-driver contamination race) are more material and unresolved.

## Alternatives Considered

- **Wrap Graphiti (library on existing FalkorDB)** — rejected: fails the ADR-015 conformance gate (no intra-group scope-predicate seam on reads; no scope-aware MERGE on writes; app-side merge for cross-group union); schema mismatch makes "wrapping" a de-facto store re-projection; open FalkorDB driver races.
- **Wrap-with-fork** — rejected: permanent fork of Graphiti's two most actively-churning subsystems to gain a capability reducible to ~3 edge properties plus one resolution procedure; couples the paid KG SKU (LUM-260 household-graph depth) to upstream driver churn.
- **Graphiti MCP server as a sidecar service** — not evaluated in depth: a new Docker service, squarely inside ADR-066's foreclosure.

Full comparison: `.cursor/explorations/LUM-558-graphiti-build-vs-wrap.md`.

## Consequences

**Easier:** ADR-015 scope union, ADR-138 bank graphs, and the ADR-042 export boundary stay untouched; the premium SKU remains self-contained (no third-party runtime in the paid differentiator); LUM-104 has a concrete borrowed spec (schema, contradiction semantics, prompt discipline) instead of an open design space; no new pip dependency, no bundled-lock (LUM-470) regeneration.

**Harder / owned:** Lumogis owns contradiction-detection quality on local models — the hardest thing Graphiti would have provided. LUM-104's hand-graded 50-episode-pair eval before auto-apply is the gate for that risk (wrap would not have removed it: Graphiti documents the same small-model structured-output failure mode). The Postgres↔graph temporal sync (`invalid_at` ↔ `valid_until`) becomes a Lumogis-owned invariant and must be an explicit LUM-104 acceptance criterion.

**Future chunks must know:** LUM-528 (graph projector) must stamp the temporal edge properties chosen here; LUM-369 (edge confidence) consumes contradiction counts/fields shaped here; LUM-105/LUM-106 are unaffected by this verdict and remain out of scope per LUM-558.

## Revisit conditions

- Upstream Graphiti merges the single-graph FalkorDB architecture (PR #1209 lineage) **and** wires `property_filters` (or an equivalent predicate seam) into the search query constructors — the two structural blockers found here.
- The LUM-104 hand-graded eval shows local models cannot reach acceptable contradiction-detection precision with Lumogis-owned prompts — then re-evaluate whether Graphiti's prompt/pipeline maturity (under a cloud or larger local model) changes the trade.
- ADR-015 itself is revisited (e.g. FalkorDB gains cross-graph union), which would dissolve the conformance failure.

## Status history

- 2026-07-02: Draft created by /explore LUM-558 (build verdict). Awaiting human checkpoint; LUM-104 remains blocked until Thomas accepts.
