# ADR 038: RELATES_TO edge directionality — canonical writes + projection MERGE fix

**Status:** Finalised
**Created:** 2026-05-14
**Last updated:** 2026-05-14
**Linear:** [LUM-208](https://linear.app/lumogis/issue/LUM-208/relates-to-edge-directionality-bug-cypher-direction-mismatch-causes) — parent **LUM-40** (FP-031 graph plugin removal / `graph_projection_state`)

## Context

FalkorDB / openCypher stores relationships with intrinsic direction; Cypher `MERGE` rejects undirected patterns on relationships. Lumogis projects co-occurrences between entities as `RELATES_TO` edges and treats them as symmetric for reads (`A` relates to `B` ≡ `B` relates to `A`). The repository documented the convention (`docs/private/kg/kg_reference.md` §2.3 + `services/lumogis-graph/graph/schema.py`): persist edges directionally as `lower_lumogis_id → higher_lumogis_id` and query with undirected `(a)-[r:RELATES_TO]-(b)`.

Audit (exploration + plan, 2026-05-14):

- Volume writes (`services/lumogis-graph/graph/writer.py`) sort endpoints and `MERGE` directed `lower→higher`.
- Read paths use undirected `MATCH` or `relDirection: 'both'` as appropriate.
- **Defect (latent until wiring):** `projection._sweep_incident_edges` historically emitted undirected `MERGE`, which FalkorDB rejects at parse time. On **`dev`** before LUM-40 wiring, **`project_entity_into_graph`** had no external callers; the fix prevents parse failure when the sweep is activated.

## Decision

- **Writes:** Every `RELATES_TO` `MERGE` orders endpoints lexicographically (`lumogis_id`) and uses a **directed** pattern (`->`).
- **Reads / updates:** Use **undirected** `(a)-[r:RELATES_TO]-(b)` (or shortest-path `both`) unless a future ADR narrows that.
- **Projection backward-sweep** (`services/lumogis-graph/projection.py`): Canonicalise with `CASE` on projection endpoints, `MERGE (lower)-[r2:RELATES_TO { scope: $target_scope }]->(higher)`, copy nullable lineage via **`SET`** only — **do not** put `evidence_id` in the `MERGE` property map when personal edges may carry NULL `evidence_id`.
- **Regression tests:** Hermetic tests on emitted Cypher (`services/lumogis-graph/tests/test_projection_relates_to_merge.py`); live **`TestFalkorDBCompatGate`** methods for undirected reachability + sweep parse/execute + second-call idempotency when `RUN_M1_COMPAT=1` and `FALKORDB_URL` are set.
- **Policy guard:** `scripts/check_graph_relates_to_merge_policy.py` + `make graph-relates-to-merge-policy-check`, chained from `verify-public-rc` after `compose-policy-check`.

No schema migration or new Docker services in this chunk.

## Alternatives considered

- Dual-edge writes — rejected (write amplification, cap interaction).
- Undirected `MERGE` — rejected by FalkorDB.
- Reify relations as nodes — deferred (LUM-104 scale).

## Consequences

**Easier:** Publish/reconcile wiring (LUM-40) can call `project_entity_into_graph` without Falkor parse failure on the sweep; multi-hop design (LUM-105) can assume symmetric traversal over canonical directed storage.

**Harder:** All new `RELATES_TO` writers must preserve canonical ordering; the policy script catches common regressions (not exhaustive for dynamic `JoinedStr`-only Cypher — see script docstring; extend under LUM-52 if needed).

## Revisit conditions

- LUM-104 / richer relation state may require reification or new edge labels.
- If FalkorDB gains undirected `MERGE`, ordering for idempotency under concurrency likely still desirable.

## Status history

- 2026-05-14: Draft created via `/explore --headless LUM-208`; revised through `/review-plan` arbitration rounds.
- 2026-05-14: **Finalised** by `/verify-plan --headless` — implementation matches decision; canonical copy recorded here.
