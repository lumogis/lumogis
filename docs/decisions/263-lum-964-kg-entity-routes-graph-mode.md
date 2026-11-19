# ADR-263: KG entity routes serve Postgres in every graph mode (LUM-964)

**Status:** Finalised
**Created:** 2026-11-16
**Last updated:** 2026-11-16
**Decided by:** claude-opus-5-5 (/explore --headless LUM-964); the `es.user_id` filter decided in the LUM-964 plan self-review
**Finalised by:** /verify-plan 2026-11-16 (claude-opus-5-5)
**Plan:** `.cursor/plans/LUM-964-kg-entity-routes-every-graph-mode.plan.md`
**Exploration:** `.cursor/explorations/LUM-964-kg_entity_routes_graph_mode.md`
**Draft mirror:** `.cursor/adrs/LUM-964-kg_entity_routes_graph_mode.md`
**Related:** [ADR 035](035-kg-graph-proxy-service-bearer-fail-closed.md) (`GRAPH_MODE=service` fail-closed wiring), [ADR 229](229-lum-855-kg-agpl-relicence.md) (Knowledge Graph is AGPL and public), [ADR 155](155-lum-157-document-content-projection.md) (shared projections); LUM-938, LUM-161

## Context

`orchestrator/routes/api_v1/kg.py` has three read routes behind `_graph_mode_guard()`: entity card, related entities and entity search. The guard returned `502 kg_unavailable` whenever the effective `GRAPH_MODE` was `service`. That is the only graph mode that works on the standard image, so switching the knowledge graph on broke entity search and every entity page in Lumogis Web (LUM-964).

The guard was added in LUM-44 Phase 0 (`dabb49b0`, 2026-04-23) as a placeholder for a planned service-mode httpx proxy, "deferred until the `lumogis-graph` service exists". The graph service has shipped since then. It shares Core's Postgres and exposes no entity-card, related or search-by-name routes. All three handlers read only Postgres (`entities`, `edge_scores`). Entities are written to Postgres in every mode.

## Decision

1. Delete `_graph_mode_guard()` and its three calls. The `/api/v1/kg/*` routes run the same Postgres-backed handlers in `disabled`, `inprocess` and `service`. Nothing in the tree produces `kg_unavailable` any more.
2. `/related` returns only the caller's own `edge_scores` rows (`AND es.user_id = <caller>`, bound from `get_user(request)`), on top of the existing `visible_filter` on both ends. No shipped writer pairs entities across members today (`edge_scores` is built from a per-user `entity_relations` self-join, and entity merge repoints per user), so the filter changes no current answer. It is there because service mode makes the query reachable with real data for the first time, and it makes the privacy property hold by construction instead of by an invariant of two other modules that LUM-938 is about to converge.
3. Tests parametrized over all three modes pin both points. They fail if any route returns `kg_unavailable` again, and a live two-member Postgres test seeds a deliberate cross-member score row that goes red without the filter.

## Alternatives Considered

- **Narrowed "needs the graph" guard (503 on graph-service outage):** no handler needs the graph, so it would have no callers.
- **Service-mode proxy to `lumogis-graph`:** both services read the same Postgres. The proxy would add a hop, a failure point and a second copy of the visibility logic, which is the drift LUM-938 is removing.
- **Keep failing with a better message (503 + "turn the graph off"):** this does not fix the bug. 502 and 503 both mean upstream or temporary failure, not "unsupported mode".
- **Prove there is no cross-member row instead of filtering:** rejected; it leaves privacy resting on writer behaviour in modules outside this route.
- **`es.user_id IN (caller + owners of visible shared entities)`:** rejected; no shared projection has scores today, and it would reintroduce other members' weights.

## Consequences

- **Easier:**
  - Switching the graph on only adds things: entity pages keep working, and "related" gains co-occurrence rows.
  - LUM-161 (the "What Lumogis knows" page) and the entry-editing tickets (LUM-965–968) build on one code path with no mode special case.
- **`/related` is per caller.** A shared entity's related list is built from the caller's own scores only. If household-level related lists are ever wanted, that is a design change to this predicate, not a quiet edit.
- **Known limit, not caused by this decision:** both weekly quality jobs score only `user_id="default"`, so on an auth-on household no member has `edge_scores` rows and `/related` is empty even with the graph on. Owned by LUM-938 (it converges exactly those modules); the converged writer must keep stamping the scoring member's `user_id`.
- **Future chunks must know:**
  - A route that really reads FalkorDB or the graph service handles that dependency's outage in its own handler. It does not gate the whole router on `GRAPH_MODE`.
  - Do not reintroduce a mode guard in front of Postgres reads.
  - Tests set the graph mode with `config.set_effective_graph_mode_for_process` + `clear_graph_mode_env_cache`, not by monkeypatching `config.get_graph_mode`.

## Revisit conditions

- The entity routes start reading FalkorDB or the graph service (for example graph-ranked "related" in LUM-161 Later). That route then needs its own degradation path and a test for graph-service-down.
- `entities` or `edge_scores` stop being written to Core's Postgres in some mode (for example the graph service moving to its own database). The "same data in every mode" premise breaks.

## Implementation

- `orchestrator/routes/api_v1/kg.py` — guard and calls deleted; module docstring states the every-mode rule; `/related` gains `AND es.user_id = %s` with a `# SCOPE-EXEMPT:` marker.
- Tests: `orchestrator/tests/test_api_v1_kg.py` (`test_kg_routes_serve_in_every_graph_mode`, `test_kg_routes_404_in_every_graph_mode`, three modes each); `orchestrator/tests/integration/test_kg_routes_graph_mode_live.py` (real Postgres, two members, 10 cases); `orchestrator/tests/test_household_sharing.py` S15–S17 now run in `service` mode.

## Status history

- 2026-11-16: Draft created by /explore
- 2026-11-16: `/related` cross-member question settled in the plan self-review: add the `es.user_id` filter
- 2026-11-16: Finalised by /verify-plan — implementation confirmed decision
