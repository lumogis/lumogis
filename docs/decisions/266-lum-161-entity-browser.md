# ADR-266: "What Lumogis knows" — Postgres-only entity list and mentions, Postgres-first rename (LUM-161)
**Status:** Finalised
**Created:** 2026-11-16
**Last updated:** 2026-11-16
**Decided by:** claude-opus-5-5 (`/explore --headless LUM-161`)
**Finalised by:** /verify-plan 2026-11-16 (claude-opus-5-5)
**Plan:** `.cursor/plans/LUM-161-what-lumogis-knows-entity-browser.plan.md`
**Exploration:** `.cursor/explorations/LUM-161-entity_browser.md`
**Draft mirror:** `.cursor/adrs/LUM-161-entity_browser.md`
**Related:** [ADR 015](015-personal-shared-system-memory-scopes.md) (personal / shared / system scopes), [ADR 158](158-graph-aware-entity-sharing-lum586.md) (shared entity projections), [ADR 263](263-lum-964-kg-entity-routes-graph-mode.md) (KG entity routes serve Postgres in every graph mode), [ADR 264](264-lum-938-graph-dedup-scope-filter.md) (dedup's personal-scope ANN filter, which merge's winner point now satisfies)

## Context

LUM-161 adds a member page that lists what Lumogis has learned (entries by kind with counts), a detail page that says where each entry was mentioned, and a way to rename an entry and edit its other names. No member list route with ids exists (the dashboard's `GET /entities` returns none), `EntityCard.sources` is always empty, and nothing can rename an entry. The pages must work with the knowledge graph off (the default), inprocess and as a service (ADR 263), follow the personal/shared/system visibility model (ADR 015) and the LUM-581 shared-entry collapse, and must not expose one member's private mentions to another. A rename touches Postgres, the Qdrant `entities` collection, the shared projection (LUM-581/586) and FalkorDB.

## Decision

Serve the list (`GET /api/v1/kg/entities`, with counts by kind) and the mentions (`GET /api/v1/kg/entities/{id}/mentions`) from Postgres only, in every `GRAPH_MODE`, with the search route's visibility filter and collapse rule, and show a mention only if the viewer could open the item it points to. Rename through `PATCH /api/v1/kg/entities/{id}` (owner, personal source row, OCC `version`): write Postgres first, keep the old name as an alias, refuse a **new** name or alias that another of the caller's entries already uses (`409 name_in_use`); alias changes travel as deltas (`aliases_add` / `aliases_remove`) applied to the locked row, because extraction rewrites aliases without bumping `version`, then re-embed and replace the entry's Qdrant points and refresh the shared projection with its current `share_origin`. FalkorDB follows through the existing reconcile stale rule (the rename sets `graph_projected_at = NULL` on the personal row and its projections) with no new event. One service helper (`services/entity_edit.py`) is the write path that merge (LUM-965) and correct-kind (LUM-967) extend.

## Alternatives Considered

- Extend the legacy `GET /entities` — no ids, outside the v1 contract.
- Read the list or mentions from Qdrant or FalkorDB — several points per entry, or broken with the graph off.
- Fill `EntityCard.sources` in place — a breaking change to a `List[str]` that cannot carry links.
- Add an `ENTITY_UPDATED` event so the graph updates at once — correct, deferred until a member page reads names from FalkorDB (LUM-161 Later).
- Qdrant `set_payload` without re-embedding, or Postgres-only rename — search and the shared copy keep the old name.

Full detail: `.cursor/explorations/LUM-161-entity_browser.md`

## Consequences

- Easier: every entry page works the same in all graph modes; merge, correct-kind and split build on one write helper and one collision rule.
- A rename never forgets a name (the old name becomes an alias); forgetting belongs to LUM-966's "won't learn" list.
- The graph shows the old name until the next reconcile (daily).
- The KG v1 router is no longer read-only; its docstring and tests must say so.
- Mentions of a shared entry are limited to items the viewer can open; counts for other members' shared entries can be lower than the owner's.
- No "Places" group until extraction emits a place type.
- The rename touches shared extraction and merge code: `_upsert_entity`'s merge path fires `ENTITY_CREATED` and writes its Qdrant point with the entry's canonical name and stored type, and both personal point writers (extraction, merge Phase B) use one builder `personal_entity_point`, which gives merge's winner point the `scope` and `entity_type` payload it lacked.
- A rename refreshes the search point embed-first: if the embedder is down the old-name points stay, so the entry stays findable under its old name.

## Implementation notes (as built)

- Routes and services as decided: `orchestrator/routes/api_v1/kg.py` (`_viewer` resolves `allows_shared` once per request; PATCH mounts `require_same_origin`), `orchestrator/services/entity_browser.py` (list, mentions, the moved LUM-581 share helpers), `orchestrator/services/entity_edit.py` (the one write path; advisory-lock salt `ENTITY_NAMES_ADVISORY_KEY1 = 8421161`). `visibility.visible_filter` gained `qualifier=` and replaced the `.replace("user_id", …)` hack in `/related`.
- Error bodies use the v1 `{"detail": {"error", "code", …}}` convention so `ApiError.code` works in Lumogis Web.
- **Implementation deviated (additive):** `_upsert_entity`'s merge path no longer subtracts the incoming surface form (lower-cased) from the alias set. The old `- {entity.name.lower()}` deleted a lowercase alias as soon as a mention matched it, so after a rename that kept the old name as `"alex"` the next mention removed it and the one after created a second entry. The canonical name is still filtered out. Test: `test_lowercase_alias_survives_the_mention_that_matched_it`.
- Not proven live: the Qdrant point after a rename (real Qdrant) and the FalkorDB name after reconcile; both are unit-tested calls (plan § Deferred tests).

## Revisit conditions

- A member-facing page (LUM-196 map) shows entry names read from FalkorDB → add `ENTITY_UPDATED` push (Option C2).
- Extraction gains a place type → add the Places group.
- A household's entity count makes offset paging slow (list p95 over ~300 ms) → switch to keyset paging.
- `entity_relations` gains a stable document id instead of a file path → simplify the mentions resolver.

## Status history
- 2026-11-16: Draft created by /explore
- 2026-11-16: Revised during /review-plan --arbitrate R1 — collision rule narrowed to new surface forms; alias edits are deltas (extraction merges do not bump `version`); graph staleness via `graph_projected_at = NULL`; consequences now record the extraction/merge payload changes.
- 2026-11-16: Finalised by /verify-plan — implementation confirmed decision; one additive deviation recorded (extraction merge keeps a lowercase alias the mention matched by)
