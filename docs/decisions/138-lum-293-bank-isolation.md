# ADR-138: Bank isolation for dev-tool context (coding / personal banks)

**Status:** Finalised
**Created:** 2026-06-25
**Last updated:** 2026-06-25
**Decided by:** /explore --headless LUM-293; implemented and verified LUM-293

## Context

LUM-291 (ADR-128) shipped a `bank` seam on Postgres `memories` and `entity_edges` and on every MCP write/read path (default bank `"coding"`), but deferred physical isolation in Qdrant and FalkorDB plus the entity-scoping decision to LUM-293. Linear AC originally proposed Qdrant collection-prefix per bank; research showed Qdrant discourages collection-per-tenant while FalkorDB's native pattern is graph-per-tenant.

## Decision

Isolate banks per backend using the substrate-appropriate pattern:

- **Qdrant** — single `memories` collection + `bank` payload tenancy (`KeywordIndexParams(is_tenant=True)` on `bank`), not collection-prefix. `bank: "*"` omits the bank filter on recall legs.
- **FalkorDB** — per-bank graph key (`coding` / `personal` / `default`) via native multi-graph; `get_graph_store(bank)` routes projections and KG reads.
- **Entities stay user-scoped (E1)** — shared referents across banks; memories, observations, and edges remain bank-scoped. Document cross-bank entity name visibility.
- **Postgres** — unchanged schema; migration `042` backfills legacy household rows to `personal` using `metadata.source='mcp'` provenance.

## Alternatives considered

- **Qdrant collection-per-bank** — rejected (vendor guidance; no v1 benefit over payload tenancy).
- **FalkorDB single graph + property filter** — rejected as target state (leak-prone).
- **Bank-partitioned entities (E2)** — deferred revisit path.

## Consequences

- Leakage tests cover Postgres recall and FalkorDB graph surfaces with positive controls.
- LUM-528, LUM-533, LUM-536 must follow the bank-graph convention (LUM-293 blocks them).
- Legacy `lumogis` FalkorDB graph: operator script `scripts/migrate-falkordb-lumogis-to-personal.py` before default bank flip on existing installs.
- Write bank allowlist narrowed to `{coding, personal, default}` (intentional per Linear AC).

## Status history

- 2026-06-25: Draft created by /explore --headless LUM-293
- 2026-06-25: Finalised by /verify-plan — implementation confirmed (LUM-293)
