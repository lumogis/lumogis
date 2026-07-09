# ADR 132: MCP `/mcp/` Origin-header DNS-rebinding guard (LUM-296)

**Status:** Finalised

**Created:** 2026-06-24

**Last updated:** 2026-06-24

**Decided by:** as-shipped implementation (retrospective)

**Finalised by:** /record-retro 2026-06-24

**Plan:** none — shipped on MCP epic integration before formal plan / verify for this slice

**Exploration:** `.cursor/explorations/lum-296-mcp-origin-dns-rebinding-guard-retro.md`

**Draft mirror:** `.cursor/adrs/lum_296_mcp_origin_dns_rebinding_guard.md`

**Linear:** [LUM-296](https://linear.app/lumogis/issue/LUM-296) (milestone v1.3 MCP security)

**Extends:** [ADR 017](017-mcp-token-user-map.md) (bearer gate remains primary; this is defence-in-depth)

## Context

The Streamable HTTP MCP endpoint at `/mcp/` is bearer-gated per ADR 017, but a malicious web page could still issue a cross-origin browser `fetch` to `http://localhost:<port>/mcp/`. Bearer auth blocks unauthorized writes, yet validating the `Origin` header is MCP-spec defence-in-depth against DNS rebinding. LUM-296 adds an explicit Core-layer check before token evaluation.

## Decision

1. **Reject foreign origins** on `/mcp/*` with `403 {"error":"origin not allowed"}` when the `Origin` header is present and its host is not loopback.
2. **Allow** absent `Origin` (non-browser clients: Cursor, curl, future stdio bridge).
3. **Allow** loopback origins: `localhost`, `127.0.0.0/8`, `::1` (any port/scheme).
4. **Fail closed** on malformed Origin values (no 500 escape).
5. **Run before** `_check_mcp_bearer` in `auth_middleware` — additive; does not reorder bearer semantics (ADR 017 D6).
6. **Do not** honour `LUMOGIS_PUBLIC_ORIGIN` for `/mcp/` — SDK enforces localhost-only downstream; matching policy at Core avoids masking refusals.

## Alternatives considered

- **Rely on MCP SDK only:** rejected — version-dependent; Core wants explicit auditable gate.
- **Honour `LUMOGIS_PUBLIC_ORIGIN`:** rejected — would pass here but fail in SDK; misleading for operators.
- **Block absent Origin:** rejected — breaks Cursor/curl/stdio (primary local clients).

## Consequences

**Easier:**

- Clear 403 for browser rebinding attempts; logged warning with origin + path.
- Ordering test ensures foreign Origin blocked even with valid bearer.

**Harder:**

- Remote-browser MCP against non-loopback hostnames is unsupported (acceptable for v1 household LAN).

**Future chunks must know:**

- Keep origin check first on `/mcp/*`.
- LUM-292 stdio bridge will not send `Origin` — remains allowed.

## As-implemented surface

| Artifact | Path |
| --- | --- |
| Classifier | `orchestrator/auth.py::_origin_host_is_local` |
| Gate | `orchestrator/auth.py::_check_mcp_origin` |
| Middleware hook | `orchestrator/auth.py::auth_middleware` (`/mcp` prefix) |
| Tests | `orchestrator/tests/test_mcp_server.py` (LUM-296 section) |

## Testing retrospective

- **7 tests** in `test_mcp_server.py`; all pass with `AUTH_ENABLED=false`.
- **P2 gap:** allow-path tests assume open MCP gate; default dev `AUTH_ENABLED=true` yields 401 — fixture hygiene follow-up.

## Linear linkage (Product OS)

- **LUM-296** — Done on integrated `dev` @ `dc716c0f5` (prior `/linear-update` closure).

## Status history

- 2026-06-24: Shipped (`d6eba93b1`, `b29a12704`) on MCP epic branch; merged to `dev`.
- 2026-06-24: Finalised by `/record-retro`.
