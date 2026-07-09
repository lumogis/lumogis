# Lumogis MCP (stdio bridge)

AGPL-3.0-only stdio MCP server for **Cursor** and other desktop clients. Speaks MCP over **stdio** locally and forwards **`tools/list`** and **`tools/call`** to Lumogis Core's existing Streamable HTTP surface at `POST http://127.0.0.1:8000/mcp/`.

Phase 2 (Streamable HTTP on Core) is already shipped; this package completes **Phase 1** — frictionless Cursor install without re-declaring tools in the client.

## Prerequisites

- Running Lumogis Core (`docker compose up -d` from the repo root)
- When Core requires auth: a minted **`lmcp_…`** token (`LUMOGIS_MCP_TOKEN`)

## Quick install (Cursor)

```bash
export LUMOGIS_MCP_TOKEN="lmcp_…"   # from Web → MCP tokens or Step 9d in connect-and-verify
make lumogis-cursor-install
# Restart Cursor
```

## Manual run

```bash
pip install -e clients/lumogis-mcp
export LUMOGIS_MCP_TOKEN="lmcp_…"   # optional when Core allows anonymous /mcp/
export LUMOGIS_MCP_URL="http://127.0.0.1:8000/mcp/"   # default
lumogis-mcp
```

Pre-release from git (before the next public export includes this tree):

```bash
uvx --from 'git+https://github.com/lumogis/lumogis@dev#subdirectory=clients/lumogis-mcp' lumogis-mcp
```

## Environment

| Variable | Default | Purpose |
| --- | --- | --- |
| `LUMOGIS_MCP_URL` | `http://127.0.0.1:8000/mcp/` | Upstream Core MCP endpoint (loopback only) |
| `LUMOGIS_MCP_TOKEN` | *(unset)* | Optional `lmcp_…` bearer forwarded when set |

## Advanced: direct HTTP transport

Cursor can also talk HTTP directly to Core (`url` + `transport: http` in `mcp.json`) — see `docs/private/ops/connect-and-verify.md` Step 10. The stdio bridge is the default for local Persona A installs.

## Fallback: `mcp-proxy` (spikes / emergencies)

Zero-code spike using [sparfenyuk/mcp-proxy](https://github.com/sparfenyuk/mcp-proxy):

```bash
export LUMOGIS_MCP_TOKEN="lmcp_…"
uvx mcp-proxy http://127.0.0.1:8000/mcp/ \
  --headers "Authorization: Bearer ${LUMOGIS_MCP_TOKEN}"
```

Configure Cursor with `command`/`args` pointing at that proxy instead of `lumogis-mcp` when debugging transport issues.

## Tests

From repo root:

```bash
make test-lumogis-mcp
```

## License

AGPL-3.0-only — same as Lumogis clients (`clients/lumogis-search/`).
