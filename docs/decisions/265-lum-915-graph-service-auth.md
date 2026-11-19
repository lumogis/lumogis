# ADR-265: Graph service auth — MCP tools take the caller from a verified Core access JWT; `tv` checked; PyJWT declared (LUM-915)

**Status:** Finalised
**Created:** 2026-11-16
**Last updated:** 2026-11-16
**Decided by:** claude-opus-5-5 (`/explore --headless LUM-915`)
**Finalised by:** /verify-plan 2026-11-16 (claude-opus-5-5)
**Plan:** `.cursor/plans/LUM-915-graph-service-auth.plan.md`
**Exploration:** `.cursor/explorations/LUM-915-graph_service_auth.md`
**Draft mirror:** `.cursor/adrs/LUM-915-graph_service_auth.md`
**Related:** [ADR 017](017-mcp-token-user-map.md) (Core is the only MCP token authority), [ADR 035](035-kg-graph-proxy-service-bearer-fail-closed.md) (graph service fail-closed), [ADR 243](243-lum-885-account-state-token-cascade.md) (`typ`-separated JWTs, `tv` bump on every account-state change), [ADR 229](229-lum-855-kg-agpl-relicence.md) and [ADR 239](239-lum-855-kg-publication-as-built.md) (the Knowledge Graph is AGPL and public)

## Context

The `lumogis-graph` service (AGPL, public since 0.12.0) has three authenticated surfaces. Core calls `/webhook`, `/context` and `/tools/*` behind `GRAPH_WEBHOOK_SECRET`. The operator pages and APIs (`/mgm`, `/graph/*`, `/kg/*`, `/codegraph/*`) take a Core access JWT when `AUTH_ENABLED=true`. External clients use the FastMCP mount at `/mcp`.

On `dev` @ `15dc98ad`, `/mcp` has no auth under any setting. `auth_middleware` waves it through, and the `MCP_AUTH_TOKEN` gate that the README and docstrings describe was never built. Four read tools (`graph.query_ego`, `query_path`, `query_mentions`, `get_context`) take `user_id` from the client, and `graph.backfill` (a write) has no role check. The JWT verifier checks `typ` but not `tv`, so a demoted or signed-out member keeps graph access until the token expires. The middleware's admin rule guards `/mgm`, `/graph/assets/*` and writes under `/api/graph` / `/api/viz`, prefixes no route uses, so with auth on any member can run graph writes (`/graph/backfill*`, `/kg/*`, `/codegraph/reindex`); Core's `/kg/*` twins are admin-only. PyJWT is not a declared runtime dependency, but the image already has it through the MCP SDK (`mcp` 1.30.0 requires `pyjwt[crypto]>=2.10.1`), so auth-on graph routes do verify Bearers today.

In the household overlay graph `/mcp` is reachable only from sibling containers on the compose network (Caddy routes `/mcp/*` to Core). The RC overlay published it on every host interface. The MCP SDK also answers 421 to a non-loopback `Host` and checks a sent `Origin`; that stops browsers and hostname-addressed siblings but is not authentication, because the client sets `Host`. The graph service has no users table: it trusts Core's JWT and reads Core's `users` table on the shared Postgres.

## Decision

Every authenticated path in the graph service, `/mcp` included, goes through one verifier, `auth._verify_token`. It checks the HS256 signature with `AUTH_SECRET`, expiry, `typ == "access"`, and now `tv` against `users.token_version`. The `tv` check follows `LUMOGIS_REQUIRE_TV_CLAIM` (default true), reads uncached, and fails closed (a lookup error is a 503, not a 401; the lookup runs off the event loop). With `AUTH_ENABLED=true`, graph `/mcp` requires a valid Core access JWT. The tools read the caller from a per-request context and no longer take `user_id`: a stray `user_id` argument is refused. `graph.backfill` needs `role == "admin"`, shares the HTTP route's single-flight flag, runs in a worker thread, and is refused over MCP while `GRAPH_ADMIN_TOKEN` is set. With auth on, the middleware's admin rule covers `/mgm`, `/graph/assets/*`, every `/kg/*` path and non-GET `/graph/*` and `/codegraph/*`; `GRAPH_ADMIN_TOKEN` stays an optional second check in the routes. With `AUTH_ENABLED=false`, `/mcp` runs as the single `"default"` user and is gated by `MCP_AUTH_TOKEN` when set, matching Core. PyJWT (`>=2.8.0`, lockstep with Core) becomes a declared runtime dependency instead of an accident of the MCP SDK's dependency list. The RC overlay binds the graph port to loopback.

## Alternatives Considered

Full detail: `.cursor/explorations/LUM-915-graph_service_auth.md`.

- **Verify Core's `lmcp_…` tokens in the graph by reading `mcp_tokens`:** not chosen. It would be a second copy of a security-critical verifier in a tree that cannot import Core (the LUM-938 drift class), against ADR 017 / 126–140, which make Core the only MCP token authority.
- **Core introspection endpoint for `lmcp_…` tokens:** not chosen now. It adds new Core API and a new failure mode for a client that does not exist. It is the upgrade path if one appears.
- **Retire graph `/mcp`:** a product decision about a public surface, left open (LUM-611 Later). It would not remove the `tv` and PyJWT work.
- **Shared secret only, every call as `"default"`:** cannot express household members when auth is on.
- **Network placement only:** the compose network is not a security boundary, and the RC overlay was LAN-reachable.

## Consequences

- **Easier:** one auth rule for the whole graph service, and one admin rule that matches the real routes. Auth-on no longer depends on the SDK pulling in PyJWT. A `tv` bump (demotion, password change, disable, logout-all) takes effect on the graph straight away. The docs match the code.
- **Harder:** a long-lived external MCP client cannot use graph `/mcp`, because access JWTs expire and cannot be refreshed there. Such clients use Core's `/mcp`. One extra indexed Postgres lookup per authenticated graph request.
- **Future chunks:** any new graph endpoint or MCP tool takes identity from the verified caller or Core's invoke envelope, never from arguments (LUM-611, LUM-107). Any new graph credential path (for example a LUM-960 cookie path for `/mgm`) calls `_verify_token`. Graph MCP tool signatures changed (no `user_id`).

## Revisit conditions

- A real long-lived MCP client needs graph `/mcp` directly → add Core introspection for `lmcp_…` tokens (Option 3) behind the same per-request context.
- Thomas decides graph `/mcp` has no future → retire it (Option 4) and let LUM-611 carry graph tools through Core.
- Graph request volume makes the uncached `token_version` lookup visible → add a short TTL with a Core-to-graph invalidation signal. A plain TTL would bring back the delay this ADR removes.
- Core adds `aud` / issuer claims or asymmetric signing → the graph verifier follows in the same change.

## Status history
- 2026-11-16: Draft created by /explore
- 2026-11-16: Revised during /review-plan --arbitrate R1 — Context corrected (PyJWT is already in the image through `mcp`; the phantom `/api/graph` admin prefixes; the SDK Host / Origin guard). Decision extended: admin rule on the real write paths, 503 on lookup failure, lookup off the event loop, MCP backfill single-flight and refused while `GRAPH_ADMIN_TOKEN` is set. The core decision (one verifier with `tv`, caller from the JWT, keep graph `/mcp`) is unchanged.
- 2026-11-16: Finalised by /verify-plan — implementation confirmed decision (`agent/lum-915`; `make test-kg` 401 passed, `make compose-test-kg` 397 passed)
