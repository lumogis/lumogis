# ADR 135: MCP stdio bridge for Cursor (LUM-292)

**Status:** Finalised

**Created:** 2026-06-25

**Last updated:** 2026-06-25

**Decided by:** /explore --headless LUM-292; implemented per `.cursor/plans/LUM-292-mcp-stdio-bridge.plan.md`

**Finalised by:** /verify-plan 2026-06-25

**Plan:** `.cursor/plans/LUM-292-mcp-stdio-bridge.plan.md`

**Exploration:** `.cursor/explorations/LUM-292-mcp-stdio-bridge.md`

**Draft mirror:** `.cursor/adrs/lum_292_mcp_stdio_bridge.md`

**Linear:** [LUM-292](https://linear.app/lumogis/issue/LUM-292)

**Extends:** [ADR 017](017-mcp-token-user-map.md) (bearer forwarding), [ADR 132](132-lum-296-mcp-origin-dns-rebinding-guard-shipped.md) (absent-Origin stdio path)

## Context

LUM-292 bundled two phases: a stdio entrypoint and a Streamable HTTP endpoint for Cursor. **Phase 2 (Streamable HTTP at `POST /mcp/`) already shipped** on `dev` (`orchestrator/mcp_server.py`, ADRs 017/126–134, Origin guard ADR 132). The remaining work is **Phase 1**: a thin AGPL client that speaks MCP over stdio to Cursor and forwards `list_tools` / `call_tool` to Core's loopback HTTP surface, plus `make lumogis-cursor-install` and git-based `uvx` distribution.

## Decision

1. Ship **`lumogis-mcp`** under `clients/lumogis-mcp/` — AGPL client package using MCP SDK v1 `streamablehttp_client` + low-level `Server` + `stdio_server`.
2. Handle stdio **`initialize` locally**; forward **`list_tools` / `call_tool`** only — tool surface stays single-sourced in `mcp_server.py` (no local `@tool` registration).
3. Forward optional `lmcp_…` bearer via `LUMOGIS_MCP_TOKEN`; omit `Authorization` when unset (Core `_check_mcp_bearer` decides).
4. Send **no `Origin`** header on upstream HTTP (ADR 132 pass-through).
5. Enforce **loopback-only** `LUMOGIS_MCP_URL` at bridge startup (Persona A defence-in-depth).
6. **`make lumogis-cursor-install`** merges `mcpServers.lumogis` into `~/.cursor/mcp.json` with `${env:LUMOGIS_MCP_TOKEN}` and pinned `git+…@<ref>#subdirectory=clients/lumogis-mcp`.
7. Document **`sparfenyuk/mcp-proxy`** as zero-code fallback; **no Core changes**.

## Alternatives considered

- **`mcp-proxy` off-the-shelf** — kept as fallback/spike only (external CLI, drifting flags).
- **FastMCP 2.x `create_proxy`** — rejected (parallel fast-moving dep).
- **Re-declaring tools / in-process Core imports** — rejected (surface drift / coupling).

Full comparison: `.cursor/explorations/LUM-292-mcp-stdio-bridge.md`.

## Consequences

**Easier:**

- One-line Cursor install for Persona A self-hosters.
- Tool additions in `mcp_server.py` appear automatically in Cursor.
- AGPL-clean; ships in public export like `clients/lumogis-search/`.

**Harder / committed:**

- LUM-299's Cursor integration harness must launch/auth via this stdio entrypoint.
- Hand-rolled forwarding on MCP SDK v1 must track Core's `mcp>=1.27.2,<2` pin.
- Installer pins a git ref until the next public AGPL export includes `clients/lumogis-mcp/`.

**Deferred:**

- PyPI publish (`uvx lumogis-mcp` without git URL).
- Fixture bank + p95 latency + real Cursor CI → **LUM-299**.

## Status history

- 2026-06-25: Draft created by /explore (headless).
- 2026-06-25: Revised during /review-plan --arbitrate R1 (optional token, `mcpServers` nesting, pinned ref).
- 2026-06-25: Finalised by /verify-plan — implementation confirmed.
