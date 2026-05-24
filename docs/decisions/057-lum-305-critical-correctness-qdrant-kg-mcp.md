# ADR 057: LUM-305 — critical correctness fixes (Qdrant `should` filters + KG MCP `max_fragments`)

**Status:** Finalised
**Created:** 2026-05-21
**Last updated:** 2026-05-21
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-05-21 (Composer)
**Plan:** none — shipped via Cursor agent branches before formal plan / verify cycle
**Exploration:** `.cursor/explorations/lum_305_critical_correctness_bugs_retro.md`
**Draft mirror:** `.cursor/adrs/lum_305_critical_correctness_bugs.md`
**Linear:** [LUM-305](https://linear.app/lumogis/issue/LUM-305) (critical correctness bugs — Qdrant isolation + KG MCP cap)

## Context

Two independent correctness bugs shipped on **`dev`** via **`cursor/critical-correctness-bugs-0ea8`** and **`cursor/critical-correctness-bugs-2dd8`** (merged **`fd948ed83`**, **`9601b3055`**) without a Product OS plan/verify loop. **LUM-305** tracks the programme in Linear.

**Bug A (security):** **`visibility.visible_qdrant_filter`** (ADR **015**) emits a top-level **`should`** household union. **`QdrantStore._build_filter`** only translated flat **`must`** lists, producing **`Filter(must=[])`** for the default filter — Qdrant applied **no payload restriction**, breaking multi-user isolation for semantic search and CONTEXT_BUILDING semantic top-up (**LUM-210**).

**Bug B (contract):** **`kg_mcp.server.graph_get_context`** used **`fragments[:cap] if cap else fragments`**, so **`max_fragments=0`** skipped slicing and returned **all** fragments — opposite of the HTTP route's **`ge=1`** validation and operator intent to disable graph context.

## Decision

Record the as-shipped fixes as canonical behaviour:

1. **`QdrantStore._build_filter`** implements:
   - top-level **`should`** → **`Filter(should=[...])`** with each branch either nested **`must`** or a single field clause;
   - top-level **`must`** → **`Filter(must=[...])`** with **`MatchValue`** or **`MatchAny`** per clause.
2. **`graph_get_context` (MCP)** returns **`{"fragments": []}`** when **`max_fragments is not None and max_fragments < 1`**; otherwise caps with **`fragments[:cap]`** using an explicit numeric cap (default **`min(get_context_entity_budget(), 20)`** when unset).
3. **`POST /context` (HTTP)** slices with **`fragments[: body.max_fragments]`** without a falsy guard (Pydantic still rejects **`0`** at the HTTP boundary).

### As-implemented surface

| Area | Path | Behaviour |
| --- | --- | --- |
| Qdrant adapter | `orchestrator/adapters/qdrant_store.py` | `_build_filter`, `_field_condition_from_clause`, `_should_branch_to_filter` |
| Visibility source | `orchestrator/visibility.py` | `visible_qdrant_filter` — unchanged; adapter must mirror |
| Qdrant unit tests | `orchestrator/tests/test_qdrant_store_filter_build.py` | 3 tests |
| KG MCP tool | `services/lumogis-graph/kg_mcp/server.py` | `graph_get_context` |
| KG HTTP route | `services/lumogis-graph/routes/context.py` | cap slice |
| KG MCP tests | `services/lumogis-graph/tests/test_kg_mcp_get_context.py` | 3 tests |
| Changelog | `CHANGELOG.md` `[Unreleased]` **Security** | Qdrant household-union filter note |

## Alternatives considered

- **Rely on Postgres-only visibility** for semantic paths — rejected; Qdrant ANN runs before Postgres hydration and must filter at payload layer.
- **Reject `max_fragments=0` on MCP with validation error** — rejected; empty list is clearer for “disable graph context” without failing the tool call.
- **Broad audit PR (`critical-correctness-bugs-d622`)** — not part of this retro; other bugs may remain on separate Linear items.

## Consequences

**Positive:** Restores vector-layer isolation for default household reads; MCP and HTTP cap semantics agree on “zero means none”.

**Negative / limits:** Unit tests pin translation and MCP stub behaviour only — not end-to-end Qdrant with two tenants.

## Revisit conditions

- Change to **`visible_qdrant_filter`** dict shape without updating **`_build_filter`**.
- Cross-user data leak report on Qdrant semantic paths — add **`tests/integration/`** coverage (see **FP-061** / **`LUM-305`** follow-up).
- New numeric cap parameters on MCP tools — grep for **`if <cap>`** falsy patterns in review.

## Linear linkage (Product OS)

- **LUM-305:** covers this retrospective scope (both fixes merged to **`dev`** **`9601b3055`**)
- **New issue needed:** no
- **Optional follow-up:** P2 integration test — **FP-061** in portfolio with **`LUM-305`** reference

## Testing retrospective

Targeted pytest after merge: **`test_qdrant_store_filter_build.py`** **3/3**; **`test_kg_mcp_get_context.py`** **3/3**. Full **`make test`** not re-run in retro pass. See exploration § *Testing retrospective*.

## Status history

- **2026-05-21:** Finalised by **`/record-retro`** — product merges **`fd948ed83`**, **`9601b3055`** on **`dev`**.
