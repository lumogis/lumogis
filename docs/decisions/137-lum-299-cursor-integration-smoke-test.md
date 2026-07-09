# ADR 137: Cursor integration smoke test — unified-surface harness (LUM-299)

**Status:** Finalised

**Created:** 2026-06-25

**Last updated:** 2026-06-26

**Decided by:** /explore --headless LUM-299; implemented per `.cursor/plans/LUM-299-cursor-integration-smoke-test.plan.md`

**Finalised by:** /verify-plan 2026-06-25; tier-2 full gate amended 2026-06-26 ([LUM-540](https://linear.app/lumogis/issue/LUM-540))

**Plan:** `.cursor/plans/LUM-299-cursor-integration-smoke-test.plan.md`

**Exploration:** `.cursor/explorations/LUM-299-cursor-integration-smoke-test.md`

**Draft mirror:** `.cursor/adrs/lum_299_cursor_integration_smoke_test.md`

**Linear:** [LUM-299](https://linear.app/lumogis/issue/LUM-299)

**Extends:** [ADR 135](135-lum-292-mcp-stdio-bridge.md) (stdio bridge — fixture bank + p95 + Cursor CI completed here), [ADR 133](133-lum-295-tempr-recall-fusion.md) (TEMPR recall), ADRs 017/126–134 (MCP write surface, annotations, scopes, Origin guard)

## Context

LUM-299 is the end-to-end definition of done for the LUM-284 MCP-memory cluster. Linear acceptance criteria were authored against external de-facto surfaces (OpenMemory-4, KG-9, bank triple) while `dev` ships one unified namespaced surface in `orchestrator/mcp_server.py`. ADR 135 deferred fixture bank, p95 measurement, and Cursor CI breadth to this chunk.

## Decision

1. **Reconcile by mapping, not by aliasing.** The harness tests the as-shipped unified surface. `delete_all_memories` and `read_graph` remain intentional non-goals (LUM-529 / community tier). No OpenMemory/KG-9 alias layer in Core.
2. **Two-tier harness shipped.** In-process ASGI breadth (`orchestrator/tests/test_cursor_integration.py` + `cursor_integration/` helpers) plus one subprocess stdio slice (`clients/lumogis-mcp/tests/test_cursor_integration_stdio.py`). `make test-cursor-integration` runs both (25 + 17 tests).
3. **Gates.** `readOnlyHint` annotation matrix on all 12 tools; bank isolation across 10 randomised queries plus bm25/temporal call-shape assertions on the composite fake store; `forget` soft-archive observable via subsequent `recall`. **Two-tier p95:** default gate (`LUMOGIS_CURSOR_INTEGRATION=1`) asserts p95 &lt; **500ms** on seeded fakes via MCP JSON-RPC; opt-in full gate (`make test-cursor-integration-full` / `make prove-cursor-integration-full`) asserts p95 &lt; **200ms** on real Postgres+Qdrant after seeding `coding_bank.json` via `make seed-cursor-integration-fixture` ([LUM-540](https://linear.app/lumogis/issue/LUM-540)).
4. **Fixture bank.** `tests/fixtures/coding_bank.json` (~50 `coding` memories + `personal` isolation slice) seeds deterministic recall assertions.
5. **CI.** Orchestrator harness auto-collects in `pytest tests/ -x`; stdio slice path-gate extended for `tests/fixtures/coding_bank.json`.

## Alternatives considered

- Full OpenMemory/KG-9 alias suite — rejected (collision risk, doubled surface).
- Full Compose stack as default gate — rejected (slow/flaky); kept opt-in.
- Pure in-memory MCP client only — rejected (never exercises stdio transport).

Full comparison: `.cursor/explorations/LUM-299-cursor-integration-smoke-test.md`.

## Consequences

**Easier:**

- Deterministic CI proxy for Cursor read auto-approval (`readOnlyHint`).
- Canonical contract for the unified MCP surface as Cursor memory backend.
- Reuses LUM-292 stdio bridge without modifying proxy logic.

**Harder / deferred:**

- Entity/entity_edges slice from `coding_bank.json` not seeded in tier-2 gate (fixture slugs vs UUID schema) — recall smokes use memory rows only.
- 10k-bank scale perf and real Cursor GUI dogfood remain manual/separate issues ([LUM-542](https://linear.app/lumogis/issue/LUM-542), [LUM-541](https://linear.app/lumogis/issue/LUM-541)).
- LUM-299 Linear AC must be updated via `/linear-update comment` with tightened tool-name mapping.

## Status history

- 2026-06-25: Draft created by /explore --headless LUM-299.
- 2026-06-25: Revised during /review-plan --arbitrate R1 — two-tier p95 documented.
- 2026-06-25: Finalised by /verify-plan — implementation confirmed; harness green (`make test-cursor-integration` 42 passed).
- 2026-06-26: Amended by /verify-plan LUM-540 — tier-2 seed script + full gate implemented; `docker-compose.test.yml` publishes Postgres for host-side probes; stdio fake store hydrate aligned with LUM-293 recall SQL.
