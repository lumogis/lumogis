# ADR-127: MCP tool annotations + spec-compliance posture (LUM-290 / LUM-297)

**Status:** Finalised

**Created:** 2026-06-23

**Last updated:** 2026-06-23

**Decided by:** as-shipped implementation

**Issue:** [LUM-290](https://linear.app/lumogis/issue/LUM-290) (duplicate: [LUM-297](https://linear.app/lumogis/issue/LUM-297))

**Related:** [ADR-010](010-ecosystem-plumbing.md) (capability plumbing); [ADR-017](017-mcp-token-user-map.md) (MCP bearer tokens); parent **LUM-284** (Lumogis as MCP memory server)

## Context

MCP clients (Cursor, Claude Desktop, other agents) use **tool annotations** to
decide which tools to auto-approve versus prompt on. Without annotations, every
tool — including pure reads like `memory.search` — triggers an approval prompt
on each call, which breaks the ambient memory pattern and pushes users to
abandon the integration. Tool annotations were introduced in MCP spec
2025-03-26 and carried forward since.

Lumogis exposes two MCP surfaces, both via the official `mcp` SDK's `FastMCP`:

- **Core** (`orchestrator/mcp_server.py`, AGPL): five community tools —
  `memory.search`, `memory.get_recent`, `entity.lookup`, `entity.search`,
  `context.build`.
- **KG** (`services/lumogis-graph/kg_mcp/server.py`, private): six `graph.*`
  tools — five reads plus `graph.backfill`.

Neither surface advertised annotations.

## Decision

Attach `mcp.types.ToolAnnotations` to **every** registered MCP tool.

| Tool class | readOnlyHint | destructiveHint | idempotentHint | openWorldHint |
| --- | --- | --- | --- | --- |
| Core reads (all 5) | `true` | `false` | `true` | `false` |
| KG reads (`query_ego`, `query_path`, `query_mentions`, `get_context`, `health`) | `true` | `false` | `true` | `false` |
| KG `graph.backfill` (write) | `false` | `false` | `false` | `false` |

- **`openWorldHint=false` everywhere.** Both surfaces read/write the operator's
  own **closed** memory / entity / graph store, not an open external world (web,
  email). This refines the LUM-290 draft matrix (which proposed `true` for read
  tools); LUM-297's matrix already specified `false`, which is correct.
- **`graph.backfill`** is the only state-mutating tool today: `readOnlyHint=false`,
  and `idempotentHint=false` because a re-run can produce further writes. It is
  **not** destructive (additive reconciliation, no irreversible data loss).
- **No genuinely destructive tool exists yet.** When the LUM-291 write surface
  lands (`add_memory`, `add_entity`, `forget`, `checkpoint`, …), `forget(mode="hard")`
  is the one tool that must carry `destructiveHint=true`; all other writes stay
  append-only / soft-archive (`destructiveHint=false`).

### Spec version + JSON-RPC error codes

The `initialize` handshake `protocolVersion`, the `capabilities` advertisement,
and JSON-RPC error codes (`-32602` invalid params, `-32601` unknown tool) are
owned by the `mcp` SDK, not Lumogis code. The pin was tightened to
`mcp>=1.27.2,<2` (from the previous open `>=1.10.0`), in lockstep across
`orchestrator/requirements-core.txt`, `requirements-test.txt`, and the KG
service's `requirements.txt`. The floor is **1.27.2 — the exact version pinned
in the bundled Core lock** (`orchestrator/locks-bundled/*.lock.txt`), so the
hash-pinned lock stays consistent without a regenerate; it guarantees
`ToolAnnotations` and a negotiated protocol ≥ 2025-06-18 (CI installs the latest
in range, currently 1.28.0 → 2025-11-25). The `<2` upper bound prevents a future
major from silently breaking the semi-private stateless-mount internals
(`settings.streamable_http_path`, `session_manager`, `streamable_http_app`)
without review. A guard test (`test_advertised_protocol_version_is_at_least_2025_06_18`)
fails if the SDK ever drops below the 2025-06-18 floor.

## Alternatives considered

- **Annotate via the `CapabilityManifest` only** — rejected: the manifest is
  Lumogis's own `/capabilities` contract (guarded by `test_mcp_manifest_unchanged`),
  not what MCP clients read for approval decisions. Annotations must live on the
  `FastMCP` tool registration.
- **`openWorldHint=true` for reads** (per the LUM-290 draft) — rejected: the
  stores are local and closed; `false` is the accurate, client-useful signal.
- **Pin `mcp` to exactly 2025-06-18** — rejected: regressive; the SDK
  auto-negotiates a higher mutually-supported version and 2025-11-25 is a
  superset.

## Consequences

- MCP clients can auto-approve the read tools without per-call prompts; only
  `graph.backfill` (and future writes) prompt.
- Graceful degradation is preserved: the annotation helpers return `None` when
  the SDK is absent, so Core still boots with no MCP surface.
- New MCP tools **must** pass `annotations=…`; the annotation tests
  (`test_mcp_annotations.py`, `test_kg_mcp_annotations.py`) assert full coverage
  and will fail on an unannotated tool.

## Status history

- 2026-06-23: Annotations added to all 11 tools across both surfaces; annotation
  + protocol-floor tests added; LUM-290/297 consolidated (duplicates).
- 2026-06-23: `mcp` pin tightened `>=1.10.0` → `>=1.27.2,<2` across Core +
  KG requirements (floor = bundled-lock version; major-version guard for the
  stateless-mount internals); bundled lock-inputs stamp regenerated.
