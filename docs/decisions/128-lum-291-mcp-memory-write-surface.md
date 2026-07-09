# ADR-128: MCP memory write surface (add_memory / add_entity / add_relation)

**Status:** Finalised
**Created:** 2026-06-24
**Last updated:** 2026-06-24
**Decided by:** `/create-plan` → `/review-plan --self` (R1+R2) → `/review-plan --critique sonnet` → `/review-plan --arbitrate` → `/implement` → `/verify-plan`
**Finalised by:** /verify-plan 2026-06-24
**Linear:** [LUM-291](https://linear.app/lumogis/issue/LUM-291) (epic [LUM-284](https://linear.app/lumogis/issue/LUM-284))
**Plan:** `.cursor/plans/LUM-291-mcp-memory-write-surface.plan.md`
**Supersedes/extends:** [ADR 017](017-mcp-token-user-map.md) (per-user MCP tokens + scopes)

## Context

Lumogis exposed five **read-only** MCP tools at `/mcp/`. To serve as a memory
backend for Cursor / Claude Code (epic LUM-284), it needs **write** primitives.
The KG had mature entity create/merge/dedup but **no** observation store, **no**
bitemporal validity, **no** typed inter-entity relation store, and **no** bank
namespacing. MCP token `scopes` existed in schema (ADR 017) but were never
enforced.

## Decision

Ship an MVP write surface — **`add_memory`, `add_entity`, `add_relation`** —
on the existing FastMCP `/mcp/` mount, gated by an **`mcp:write`** token scope.

1. **Storage.** New `memories` table (the atomic observation; bank-scoped;
   `valid_from`/`valid_until` bitemporal-ready, always `valid_until=NULL` on
   write here) and new `entity_edges` table (**Postgres is the system-of-record**
   for typed relations; bank-scoped; UNIQUE-keyed idempotency). Entities are
   reused as-is and are **user-scoped, not bank-scoped** (a referent shared
   across a user's banks); banks isolate *statements* (memories, edges), not
   referents.
2. **FalkorDB is a projection, not the SoR.** Writes succeed with the default
   `GRAPH_BACKEND=none`; when a graph backend is enabled, edges project to
   `RELATES_TO` via the `GraphStore` port. No relations are lost on the default
   install.
3. **No `entity_relations` migration.** `relation_type` is unconstrained free
   TEXT (no CHECK), so MCP-originated provenance (`MENTIONED_IN_MEMORY`) is
   enabled in code: `_upsert_entity` maps `evidence_type="MEMORY"`, and the
   value is added to both `entity_constraints.py` `_ALLOWED_EDGE_TYPES` sets.
4. **Scope enforcement.** `mint()` gains an optional `scopes` parameter
   (`NULL` = unrestricted, preserving every existing token); `_check_mcp_bearer`
   stashes the token's scopes into a `_current_mcp_scopes` ContextVar
   (default `None`); each write tool calls `_require_scope("mcp:write")` first.
   JWT/legacy paths leave the ContextVar at `None` (unrestricted).
5. **Cypher injection control.** The relation-type allowlist (`RELATION_TYPES`,
   UPPERCASE `^[A-Z_]+$`) is a **security control**, not cosmetics: the projector
   interpolates the relationship type into Cypher (which cannot bind rel-types),
   so only allowlisted tokens may reach the Cypher string; node ids stay bound
   parameters.
6. **Quality-gate asymmetry.** `add_memory`'s LLM-extracted entities stay gated;
   explicit `add_entity` / `add_relation` endpoints bypass the gate
   (`store_entities(skip_quality_gate=True)`) so user-asserted data is never
   silently discarded.
7. **Input bounds + isolation.** Pydantic models cap content (8 KB), metadata
   (4 KB), and list sizes. `user_id` is always server-derived; new tables are
   registered in `_USER_EXPORT_TABLES` (data portability) and their scope-less
   per-user reads are `# SCOPE-EXEMPT`-tagged.

## Alternatives considered

- **FalkorDB as system-of-record for typed relations** — rejected: relations
  would vanish on the default `GRAPH_BACKEND=none` install.
- **Reusing `sessions` as the observation store** — rejected: a session blob
  cannot be superseded/forgotten per-fact.
- **A new CHECK constraint on `entity_relations.relation_type`** — rejected:
  no constraint exists today; adding one risked breaking `RELATED_TO` rows.
- **`forget` / `update_observation` / `checkpoint`** — deferred (LUM-291
  follow-up); the bitemporal columns land now, the superseding behaviour later.

## Consequences

- **Positive:** Cursor can persist to the KG; first enforced MCP scope; bank
  seam in place for LUM-293; bitemporal substrate for the deferred destructive
  tools.
- **Negative / watch:** `NULL scopes = unrestricted` is fail-*open* per token —
  write protection is opt-in (mint a scoped token); a route/Web UI to choose
  scopes is a follow-up. The graph-side `Memory` node label + a dedicated
  `writer.py::project_edge` were deferred in favour of a minimal port-level
  projection (graph default-off).
- **Cross-bank entity behaviour:** entity-name existence is a weak cross-bank
  signal (resolution is cross-bank); all statements stay bank-isolated. LUM-293
  decides whether real multi-graph isolation also partitions entities.

## Revisit conditions

- The deferred `forget`/`update_observation`/`checkpoint` land (supersession via
  `valid_until`, reconciliation with purge tombstones).
- LUM-293 introduces real FalkorDB multi-graph bank isolation.
- A decision to default legacy/unscoped tokens to read-only (close the
  fail-open default).

## Status history

- **2026-06-24:** Finalised by /verify-plan — implementation confirmed the
  decision. 26 LUM-291 tests + full suite (2310 passed; 2 new guard-test
  regressions fixed: `_USER_EXPORT_TABLES` + `# SCOPE-EXEMPT` tags). Adversarial
  code + security review: no critical/high findings.
