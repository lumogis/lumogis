# ADR 058: LUM-306 — KG MCP `graph.get_context` non-positive `max_fragments` cap

**Status:** Finalised
**Created:** 2026-05-21
**Last updated:** 2026-05-21
**Decided by:** as-shipped implementation (retrospective)
**Finalised by:** /record-retro 2026-05-21 (Composer)
**Plan:** none — shipped via Cursor agent branch before formal plan / verify cycle
**Exploration:** `.cursor/explorations/lum_306_kg_mcp_max_fragments_cap_retro.md`
**Draft mirror:** `.cursor/adrs/lum_306_kg_mcp_max_fragments_cap.md`
**Linear:** [LUM-306](https://linear.app/lumogis/issue/LUM-306/fixkg-mcp-guard-max-fragments0-in-graphget-context)

## Context

**LUM-306** records a correctness fix merged to **`dev`** as **`9601b3055`** (commit **`c64e44152`**, branch **`cursor/critical-correctness-bugs-2dd8`**). The KG MCP tool **`graph.get_context`** used **`fragments[:cap] if cap else fragments`**, so **`max_fragments=0`** skipped slicing and returned **all** fragments — the opposite of “disable graph context.”

The HTTP **`POST /context`** route already rejected **`max_fragments=0`** via Pydantic **`ge=1`**; only the MCP path lacked an explicit non-positive guard.

**Note:** **ADR 057** (`/record-retro` LUM-305) combined this fix with the Qdrant isolation bug in one umbrella record. **LUM-306** is the **Linear-canonical** record for the KG MCP slice only.

## Decision

1. **`graph_get_context`** (MCP) returns **`{"fragments": []}`** when **`max_fragments is not None and max_fragments < 1`**.
2. When **`max_fragments`** is **`None`**, default cap is **`min(get_context_entity_budget(), 20)`**; slice with **`fragments[:cap]`** using explicit numeric **`cap`**.
3. **`POST /context`** continues to reject **`0`** at validation; route handler slices with **`fragments[: body.max_fragments]`** without a falsy guard as defence in depth.

### As-implemented surface

| Path | Role |
| --- | --- |
| `services/lumogis-graph/kg_mcp/server.py` | `graph_get_context` — early return + cap slice |
| `services/lumogis-graph/routes/context.py` | HTTP cap slice |
| `services/lumogis-graph/tests/test_kg_mcp_get_context.py` | 3 regression tests |

## Alternatives considered

- **Raise validation error on MCP for `max_fragments=0`** — rejected; empty list is a valid “disabled” result without failing the tool call.
- **Align HTTP to accept `0`** — not chosen at ship time; MCP-only semantics documented here.

## Consequences

**Positive:** MCP and operator intent align: zero cap means no graph fragments.

**Limits:** Surface divergence remains — HTTP rejects **`0`**, MCP accepts it as **`[]`**.

## Revisit conditions

- HTTP **`ContextRequest`** allows **`max_fragments=0`** — update both surfaces and tests together.
- New MCP tools with numeric caps — code review must reject **`if cap`** falsy patterns.

## Linear linkage (Product OS)

- **LUM-306:** Done — merge **`9601b3055`** on **`dev`**
- **Related:** **LUM-305** (Qdrant isolation — **ADR 057**)
- **New issue needed:** no

## Testing retrospective

**`pytest services/lumogis-graph/tests/test_kg_mcp_get_context.py -q`** — **3 passed** at retro time. See exploration for gaps (no live MCP round-trip).

## Status history

- **2026-05-21:** Finalised by **`/record-retro`** LUM-306.
