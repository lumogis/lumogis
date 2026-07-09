# ADR-139: Multi-bank FalkorDB export and bank-aware graph purge on MCP archive (LUM-544)

**Status:** Finalised

**Created:** 2026-06-26

**Last updated:** 2026-06-26

**Decided by:** as-shipped implementation (retrospective)

**Finalised by:** /record-retro 2026-06-26

**Plan:** none — shipped as LUM-293 verify-plan P2 follow-up without a dedicated plan/verify cycle

**Exploration:** `.cursor/explorations/lum_544_multi_bank_export_forget_graph_purge_retro.md`

**Draft mirror:** `.cursor/adrs/lum_544_multi_bank_export_forget_graph_purge.md`

**Linear:** [LUM-544](https://linear.app/lumogis/issue/LUM-544) (child of LUM-293)

**Extends:** [ADR-138](138-lum-293-bank-isolation.md) (per-bank FalkorDB routing)

## Context

ADR-138 (LUM-293) isolated MCP memory and graph **routing** by bank (Qdrant payload tenancy, FalkorDB graph-per-bank, `recall(bank="*")`). Verify-plan deferred two gaps as P2 child **LUM-544**:

1. `user_export` exported only the default household graph (`personal`), not coding/default banks.
2. MCP `forget` / `update_observation` archived Postgres `entity_edges` but never removed projected FalkorDB relationships on the correct bank graph.

## Decision

1. **`user_export`** iterates `banks.KNOWN_BANKS` and exports each configured graph under `falkordb/{bank}/nodes.json` and `falkordb/{bank}/edges.json`. The manifest records `falkordb_banks`. Legacy combined `falkordb/nodes.json` / `falkordb/edges.json` (union of all banks) are still written for backward-compatible import.
2. **`import_user`** restores nodes from per-bank paths into `get_graph_store(bank)` when present; otherwise falls back to legacy combined paths on the default graph.
3. **`forget` and `update_observation`** snapshot active edges via `fetch_active_edges_for_memory`, archive Postgres rows, then call `purge_graph_projections_for_edges` to `DELETE` allowlisted relationship types on the bank-scoped graph (best-effort; failures logged, Postgres remains SoR).
4. Edge restoration on import remains **out of scope** (pre-existing v1 limitation — re-derive via re-ingest).

## Alternatives considered

- **Export only per-bank paths (drop legacy combined files):** rejected — would break import of archives produced before LUM-544 and older single-graph exports.
- **Hard-delete FalkorDB nodes on forget:** rejected — soft-archive model (LUM-526) applies to memories/edges; only projected **relationships** tied to archived edges are purged.
- **Synchronous graph purge inside Postgres transaction:** rejected — graph is derived data; same best-effort posture as `_project_edge`.

## Consequences

**Easier:**

- Multi-bank households get faithful FalkorDB export snapshots per bank.
- Archived MCP memories no longer leave stale projected edges on the wrong bank graph.

**Harder:**

- Export zips are slightly larger (per-bank sections + legacy union).
- Operators must understand dual FalkorDB layout in archives.

**Future chunks must know:**

- Any new bank-aware write path that archives edges should use the fetch → archive → purge pattern.
- Import edge MERGE remains a separate feature if ever required.

## Revisit conditions

- Requirement to restore FalkorDB edges on import (entity_id-based MERGE contract).
- Live FalkorDB integration coverage for purge isolation (optional; unit tests mock graph today).
- `verify-public-rc` multi-bank export round-trip (P3 from LUM-293).

## Linear linkage (Product OS)

- **LUM-544** — Done 2026-06-26; evidence commit `744eef3c4`.
- Parent **LUM-293** — already Done; this child closed the deferred P2 from verify-plan.

## Testing retrospective

| Item | Detail |
| --- | --- |
| Tests added | `test_export_user_writes_per_bank_falkordb_sections`; entity_edges fetch/purge tests; mcp supersede ordering tests |
| Commands | `pytest` targeted 45 passed; `make test` 2490 passed (session) |
| Gaps | No live FalkorDB purge integration test; import edges still not restored |
| Matrix | Extends coverage under row **1.7.15** (bank isolation family); no new matrix row required |

## Status history

- 2026-06-26: Finalised by /record-retro (retrospective as-built record for LUM-544).
