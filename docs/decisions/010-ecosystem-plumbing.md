# ADR-010: Ecosystem plumbing — capability services and MCP server surface

> **Numbering note.** This ADR was originally scoped as "ADR-009" in the
> ecosystem-plumbing prompt, but `docs/decisions/009-knowledge_graph_visualization.md`
> already exists. The next available number is 010.

## Status

Accepted. Implemented in Lumogis Core 0.3.0rc1 across four sequential areas:

1. **Capability manifest format** — `orchestrator/models/capability.py`, `orchestrator/__version__.py`
2. **Service discovery and registration** — `orchestrator/services/capability_registry.py`, lifespan integration in `main.py`, `CAPABILITY_SERVICE_URLS` env var
3. **Out-of-process health checking** — `RegisteredService.check_health`, `CapabilityRegistry.check_all_health[_sync]`, dashboard surface
4. **MCP server surface** — `orchestrator/mcp_server.py`, `orchestrator/routes/capabilities.py`, `MCP_AUTH_TOKEN` middleware, dashboard MCP card

## Context

Up to 0.3.0, every Lumogis extension point lived inside the Core process: file-type extractors as `adapters/extract_*` functions, swappable backends as `Adapter` classes implementing `ports/*` Protocols, and self-contained features as Python packages under `plugins/<name>/`. This is fine for extensions that can run in the orchestrator's Python runtime, but it does not work for:

- Code with heavy or conflicting dependencies (PyTorch builds, models pinned to specific Python versions, GPU-isolated runtimes)
- Commercial tiers that need an independent release cadence and licence boundary
- Tools that only make sense as separate containers or hosts (graph services, scratchpad workers, cloud bridges)

Two pieces of plumbing close that gap symmetrically:

1. A standard manifest contract so out-of-process services can declare themselves to Core, plus a registry inside Core that discovers, validates, and health-probes them.
2. An MCP server surface inside Core so external MCP-speaking clients (Thunderbolt, Claude Desktop, future agents) can use Lumogis as infrastructure with the same discovery contract pointing at Core itself.

## Decision

### 1. Capability manifest as the single contract

`models/capability.py` defines `CapabilityManifest` as a strict Pydantic v2 model with enums for `transport` (`http` / `mcp`), `license_mode` (`community` / `commercial`), and `maturity` (`preview` / `stable` / `deprecated`). Tools are declared with hand-coded JSON input/output schemas. Every out-of-process service publishes one of these at `GET /capabilities`. Core publishes its own at `GET /capabilities` too (built by `mcp_server.build_core_manifest()`), so external systems discover Core through the same contract used by everything else.

### 2. CapabilityRegistry — async core, sync wrappers for APScheduler

`services/capability_registry.py` is async-first: `discover()` and `check_all_health()` use `httpx.AsyncClient` and run in parallel with `asyncio.gather`. Both methods have `_sync` siblings that wrap them in `asyncio.run()` so APScheduler — which is synchronous — can call them on its threadpool. The registry is the first piece of async business logic in the codebase; see [Known technical debt](#known-technical-debt-syncasync-consistency) below for the migration plan.

A `transport=` constructor parameter on both `CapabilityRegistry` and `RegisteredService.check_health` accepts an `httpx.MockTransport` so tests can drive the network behaviour deterministically without spinning up real services.

`min_core_version` is compared with `packaging.version.Version` (already a transitive dependency, no new SemVer library needed). Core's own version lives in `orchestrator/__version__.py` and must be kept in lockstep with `pyproject.toml`.

### 3. Health surface — extend GET / for detail, GET /health for symmetry

`GET /` already drives the dashboard's main panel, so it gained a detailed `capability_services` block plus the `mcp_enabled` and `mcp_auth_required` fields. `GET /health` gained only a minimal `{registered, healthy}` summary so external monitors can detect the registry without parsing the full structure. **Capability service failures never escalate Core's overall health** — `/health`'s 200/503 contract continues to track Postgres reachability only. A service being down is informational, not fatal.

GET `/health` returning `{"registered": 0, "healthy": 0}` (rather than `{}`) when no services are registered is a deliberate departure from the prompt; predictable shape > absence of keys for monitoring tooling.

### 4. MCP server surface — stateless, JSON-only, mounted at /mcp/

`mcp_server.py` constructs a `FastMCP(stateless_http=True, json_response=True)` server and registers five read-only community tools: `memory.search`, `memory.get_recent`, `entity.lookup`, `entity.search`, `context.build`. Each tool function is a thin wrapper around an existing service helper — no business logic in `mcp_server.py`. Three new helpers in `services/memory.py` and `services/entities.py` (`recent_sessions`, `lookup_by_name`, `search_by_name`) back the tools that previously had no service-layer entry point; they mirror the existing `routes/data.py::list_entities` pattern (warn + return empty answer on DB failure, never raise).

Stateless mode is a deliberate scope choice: every community tool is read-only and self-contained, so no sessions, no streaming, no server→client notifications. A future stateful MCP surface (e.g. for long-running KG queries) belongs in a separate capability service, not in Core.

Tool manifest schemas are **hand-coded** in `MCP_TOOLS_FOR_MANIFEST` rather than introspected from FastMCP's runtime registry. This couples the public manifest to a single source of truth and avoids drift across SDK versions (which auto-generate titles like `memory_searchArguments` from Pydantic model names).

### 5. Auth — Bearer token on /mcp/* with timing-safe comparison

`auth_middleware` was extended to gate `/mcp/*` independently of `AUTH_ENABLED` using `MCP_AUTH_TOKEN`. Comparison uses `hmac.compare_digest` for timing-safe string equality. When the env var is unset, `/mcp/*` is open — the documented single-user-local default. `GET /capabilities` is never gated; the manifest is the public discovery contract.

The MCP auth check lives in `auth_middleware` rather than as middleware on the mounted MCP sub-app because layering middleware onto a FastAPI-mounted Starlette sub-app is fragile and order-dependent.

## Plan deviations and the reasons for them

### Deviation 1: `session_manager.run()` is required even in stateless mode

The Area 4 plan claimed that `stateless_http=True, json_response=True` removed the need to merge the MCP SDK's lifespan with FastAPI's. Verification proved that incorrect: even in stateless mode, `StreamableHTTPSessionManager.handle_request` requires `self._task_group` to be active, and the task group is created by `mcp.session_manager.run()` (an async context manager). Without it, the first `/mcp/` POST raises `RuntimeError: Task group is not initialized. Make sure to use run().`

The fix is small: enter `mcp.session_manager.run()` inside the existing FastAPI `lifespan`, store the context manager on `app.state.mcp_run_cm`, and exit it on shutdown. See `orchestrator/main.py`'s `lifespan` for the implementation. The misleading "no lifespan merging required" comment near the mount has been corrected.

### Deviation 2: `build_fastmcp()` factory pattern for test lifespan correctness

`StreamableHTTPSessionManager.run()` can only be called **once per `FastMCP` instance** — the second call raises `RuntimeError: StreamableHTTPSessionManager .run() can only be called once per instance.` In production, the FastAPI lifespan runs once per process so naive reuse would work, but `TestClient(main.app)` starts a fresh lifespan for every test against the same module-level `app`, immediately tripping the single-shot constraint on the second test.

Rather than special-casing tests, both code paths were unified by introducing `mcp_server.build_fastmcp()`: a factory that returns a fresh `FastMCP` with all five tools registered. The lifespan calls it on every startup, mutates `mcp_server.mcp` to the fresh instance, swaps the existing `Mount`'s `route.app` to point at the freshly built sub-app, and only then enters the new instance's `session_manager.run()`. Cost is ~1 ms per startup; benefit is identical behaviour in production and tests with no test-only reset hooks.

### Deviation 3: canonical MCP endpoint is `/mcp/`, not `/mcp`

The dashboard, docs, and tests all use **`/mcp/` with the trailing slash** as the canonical client URL. `POST /mcp` works in production (FastAPI/Starlette returns a 307 redirect to `/mcp/`), but the redirect causes two real-world problems worth documenting:

- httpx (and therefore the FastAPI `TestClient`) drops the `Authorization` header on cross-origin redirects. Since `TestClient` originates requests with `Host: testserver` and the redirect Location is `http://testserver/mcp/`, the redirect is technically same-origin but the test harness's URL-vs-Host mismatch causes the header to be stripped. This made the auth tests flaky until the canonical URL was switched.
- Production MCP clients vary in how they handle 307s for POST. Telling users to configure `/mcp/` directly avoids the redirect entirely.

The dashboard MCP card now displays `/mcp/` as the URL to copy. Tests POST to `/mcp/` directly. `/mcp` (no slash) still works via the 307 for clients that handle it.

## Operational notes

### `cryptography` is now a transitive dependency

Adding `mcp>=1.10.0` to `orchestrator/requirements.txt` pulls `pyjwt[crypto]`, which in turn pulls `cryptography` (currently 46.0.7 on Python 3.12). The wheel is precompiled `manylinux2014_x86_64` (~5 MB), so no apt-level system dependencies are needed for the existing Docker image. Worth flagging to ops at the next image rebuild — image size grows by ~5 MB. No action required beyond a normal `docker compose build`.

### `_DEFAULT_USER_ID = "default"` — the future per-token user mapping site

`mcp_server.py` hard-codes `_DEFAULT_USER_ID = "default"` and passes it into every tool's underlying service call. This is correct for single-user local deployment, which is the documented design point for 0.3.0. It is **not** correct for multi-tenant operation.

When the requirement to map MCP `Authorization` tokens to per-user identities arrives — driven by either a multi-user deployment or a hosted/cloud variant — `_check_mcp_bearer` and the MCP tool wrappers in `mcp_server.py` are the integration sites. The likely shape:

1. Replace `MCP_AUTH_TOKEN` (single shared secret) with a token store that maps tokens → user identities.
2. Have `_check_mcp_bearer` resolve the presented token to a `user_id` and stash it on `request.state.user`.
3. Replace `_DEFAULT_USER_ID` references in `mcp_server.py` with a lookup that reads the bearer-resolved user from a request-scoped context (e.g. a `contextvars.ContextVar` set by the middleware).

This is a bounded refactor — five tool functions, one middleware function — but should not be undertaken until the underlying multi-user data model exists, since `services.memory.*` and `services.entities.*` currently treat `user_id` as an opaque string with no real isolation guarantees.

## Known technical debt: sync/async consistency

The entire service and adapter layer of Lumogis is synchronous. Routes, services, and adapters all use blocking calls into psycopg, the Qdrant client, the Ollama HTTP client, and so on. FastAPI runs synchronous handlers on a threadpool and this has been adequate for the single-user local deployment that the project is designed for.

`CapabilityRegistry.discover()` introduced the **first piece of async business logic in the codebase**. It was implemented async because:

- `httpx.AsyncClient` makes parallel manifest fetches across multiple capability services trivial.
- The registry runs entirely outside the request path (in lifespan startup and APScheduler jobs), so it is the safest place to introduce async without touching anything else.

This is **acceptable for 0.3.0's single-user local deployment target**. The registry's sync wrappers (`discover_sync`, `check_all_health_sync`) bridge the two worlds for APScheduler and have predictable behaviour.

It is **not** the right long-term posture. A future migration to:

- `asyncpg` in place of `psycopg`
- the async Qdrant client in place of the sync one
- async `services/*` and `routes/*`

…should be **triggered by the first multi-user deployment requirement**, not by 0.3.0 features. Doing it earlier would burn budget on a refactor whose benefits — concurrent handlers, real fan-out — are invisible to the only user the project currently serves. Doing it lazily when multi-user lands risks a piecemeal migration where some routes are async and some are sync; the migration should be a single coordinated change.

Concretely, when that day comes:

1. Migrate `adapters/postgres_store.py` to `asyncpg` behind the existing `MetadataStore` Protocol (the Protocol becomes async).
2. Migrate `adapters/qdrant_store.py` to the async client behind the `VectorStore` Protocol.
3. Convert `services/*` to async one file at a time, using the now-async ports.
4. Convert `routes/*` to async handlers.
5. Drop the `_sync` wrappers in `CapabilityRegistry` — APScheduler integration becomes `add_job(asyncio.create_task, args=[coro])` or moves to an async scheduler.

A summary of this debt has been added to `docs/decisions/DEBT.md` so it remains discoverable independently of this ADR.

## Consequences

### Positive

- **Symmetric extension model.** Out-of-process services and external MCP clients both speak the same `CapabilityManifest` contract. There is exactly one way to discover a Lumogis capability.
- **Additive everywhere.** Core boots cleanly with no capability services configured and with the `mcp` package absent. None of the existing 495 tests changed behaviour; 31 new tests were added.
- **Operational visibility.** Registry status is on the dashboard's main panel and the MCP card is on Settings. Operators can see what is registered, what is healthy, and what URL to give external clients without reading the docs.
- **Test-deterministic transport.** `transport=httpx.MockTransport` on the registry and `build_fastmcp()` factory in `mcp_server` mean every network and lifespan path is covered in unit tests.

### Negative / accepted trade-offs

- **First async surface in a sync codebase.** Tracked as known technical debt above.
- **Stateless MCP only.** No streaming, no sessions, no notifications. Acceptable for the five community tools; future stateful surfaces belong in separate capability services.
- **Hand-coded manifest schemas in two places.** `MCP_TOOLS_FOR_MANIFEST` and the `@mcp.tool` decorators duplicate tool descriptions. Drift is mitigated by the round-trip test (`test_build_core_manifest_round_trips_through_pydantic`) and the per-tool wrapper tests.
- **Cryptography pulled transitively.** ~5 MB image-size impact, no functional concern.

## References

- Implementation: `orchestrator/__version__.py`, `orchestrator/models/capability.py`, `orchestrator/services/capability_registry.py`, `orchestrator/mcp_server.py`, `orchestrator/routes/capabilities.py`, `orchestrator/auth.py`, `orchestrator/main.py`, `orchestrator/routes/admin.py`
- Tests: `orchestrator/tests/test_capability.py`, `test_capability_registry.py`, `test_capability_health.py`, `test_mcp_tools.py`, `test_mcp_server.py`
- Operator docs: [`ARCHITECTURE.md` § Ecosystem plumbing](../../ARCHITECTURE.md), [`docs/extending-the-stack.md` § Out-of-process capability services](../extending-the-stack.md), `README.md` § Extending the stack
- Env: `.env.example` (`CAPABILITY_SERVICE_URLS`, `MCP_AUTH_TOKEN`)
