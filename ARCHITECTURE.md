# Lumogis Architecture

This document explains how the lumogis orchestrator is structured and how the pieces fit together. Read this before contributing code. For a **single consolidated overview** aimed at operators and contributors (components, mental model, Web surfaces, deployment, roadmap disambiguation), see [`docs/LUMOGIS_REFERENCE_MANUAL.md`](docs/LUMOGIS_REFERENCE_MANUAL.md). For decisions on *why* specific technologies were chosen, see `docs/decisions/`. For the **post-remediation** framing (Core as policy kernel, tool catalog overlay, household-control `/api/v1` facades), see [ADR 028 — Self-hosted extension architecture and household control surfaces](docs/decisions/028-self-hosted-extension-architecture-and-household-control-surfaces.md).

---

## Five concepts

Everything in the codebase maps to exactly one of five concepts:

| Concept | Lives in | Purpose |
|---|---|---|
| **Services** | `orchestrator/services/` | Business logic — ingest, search, memory, entity extraction, routines |
| **Adapters** | `orchestrator/adapters/` | One file per external system. Swap a backend by writing one adapter. |
| **Plugins** | `orchestrator/plugins/` | Optional, self-contained extensions. Core works without any. |
| **Signals** | `orchestrator/signals/` | Source monitors that detect and score incoming events |
| **Actions** | `orchestrator/actions/` | Executable operations with audit logging and Ask/Do enforcement |

---

## Lumogis Web and Caddy

The default `docker-compose.yml` runs **Caddy** (host **80** / **443**), **`lumogis-web`** (nginx serving the Vite SPA from `clients/lumogis-web/`), and the **orchestrator** on **8000**. Browsers should use the **same origin** as Caddy (e.g. `http://localhost/`) so refresh cookies and `csrf.require_same_origin` line up.

**Routing (Caddy → upstream):** `/api/*`, `/events`, `/v1/*`, `/mcp/*`, `/health`, and root-mounted **legacy** orchestrator routes from `routes/admin.py` (e.g. `/dashboard`, `/settings`, `/graph/*`, `/kg/*`, `/review-queue`, `/backup`, `/restore`, `/permissions`, `/browse`, `/export`, `/entities/*`) go to `orchestrator:8000` **before** the catch-all to `lumogis-web:80`. The SPA’s nginx `try_files` would otherwise return `index.html` for those paths and hide the FastAPI pages.

**Operators:** set `LUMOGIS_PUBLIC_ORIGIN` to the browser’s canonical URL when `AUTH_ENABLED=true`; set `LUMOGIS_TRUSTED_PROXIES` for `X-Forwarded-For` when a reverse proxy is in front. See `.env.example` and root `README.md`.

---

## Dependency direction

```
routes → services → ports ← adapters
                  ↕
               config
                  ↑
               plugins (via hooks only)
               signals (fire hooks on receipt)
               actions (registered via hooks, executed by executor)
```

Arrows point in the direction of imports. The rule: **nothing imports inward past its layer boundary**.

- Routes import services (never adapters or ports directly)
- Services import ports (never concrete adapters)
- Adapters implement ports
- Plugins, signals, and actions import from `ports/`, `models/`, `events.py`, and `hooks.py` — never from `services/` or `adapters/`
- **Plugins (precise list):** see [`docs/architecture/plugin-imports.md`](docs/architecture/plugin-imports.md) — in-tree first-party code may have a **documented** `config` import; everything else should stay on the default allow-list
- `config.py` is the only place that instantiates concrete adapters

---

## Ports and the config factory

`ports/` contains Protocol interfaces — Python structural typing contracts. Every swappable backend has a corresponding port:

| Port | Methods |
|---|---|
| `VectorStore` | `upsert`, `search`, `delete`, `count`, `ping` |
| `MetadataStore` | `execute`, `fetch_one`, `fetch_all`, `ping` |
| `Embedder` | `embed`, `embed_batch`, `vector_size`, `ping` |
| `Reranker` | `rerank(query, candidates, limit)` |
| `LLMProvider` | `chat`, `chat_stream` |
| `GraphStore` | `create_node`, `create_edge`, `query`, `ping` |
| `SignalSource` | `poll() -> list[Signal]`, `source_id: str` |
| `ActionHandler` | `handle(action: Action) -> ActionResult` |
| `Notifier` | `send(subject, body, tags)`, `ping` |

`config.py` reads environment variables, constructs the appropriate concrete adapter, and returns it. Subsequent calls return the same cached instance (singleton pattern):

```python
from config import get_vector_store

vs = get_vector_store()   # QdrantStore, or any future alternative
vs.upsert(...)
```

Swapping a backend: write a new adapter satisfying the Protocol, add a factory branch in `config.py`, set the env var. Zero service-layer changes.

### Singleton caching

`config.py` uses module-level `_cache` dict to store constructed adapters. The pattern is identical for every `get_*()` function:

```python
_cache: dict[str, Any] = {}

def get_vector_store() -> VectorStore:
    if "vector_store" not in _cache:
        backend = os.getenv("VECTOR_STORE_BACKEND", "qdrant")
        if backend == "qdrant":
            _cache["vector_store"] = QdrantStore(url=os.getenv("QDRANT_URL"))
        else:
            raise ValueError(f"Unknown VECTOR_STORE_BACKEND: {backend}")
    return _cache["vector_store"]
```

### Startup health checks

`main.py` calls `ping()` on every service-backed adapter during the FastAPI lifespan startup. A failed `ping()` raises `RuntimeError` and prevents startup. `Reranker` has no `ping()` — it is a loaded model, not a network service.

### VectorStore hybrid search

`VectorStore.search()` accepts an optional `sparse_query: str | None = None` parameter. The `QdrantStore` adapter uses it to perform BM25 + dense hybrid search on the `documents` collection with Reciprocal Rank Fusion (RRF). Community adapters must include the parameter in their `search()` signature; they may safely ignore it if their backend does not support sparse vectors.

---

## Extractor auto-discovery

File type extractors in `adapters/` are auto-discovered by `config.get_extractors()`. The pattern: any function in `adapters/` that matches `extract_<extension>(path: str) -> str` is automatically registered. No factory branches, no port, no Protocol — just a function with the right signature.

```python
# adapters/epub_extractor.py
def extract_epub(path: str) -> str:
    ...
```

This is deliberately minimal. Extractors are pure functions with no dependencies on the rest of the system.

---

## Event constants and hook dispatch

`events.py` defines all hook event names as string constants on the `Event` class. Never use raw strings for hook events — always use `Event.*`:

```python
class Event:
    DOCUMENT_INGESTED      = "on_document_ingested"
    ENTITY_CREATED         = "on_entity_created"
    SESSION_ENDED          = "on_session_ended"
    TOOL_REGISTERED        = "on_tool_registered"
    CONTEXT_BUILDING       = "on_context_building"
    SIGNAL_RECEIVED        = "on_signal_received"
    FEEDBACK_RECEIVED      = "on_feedback_received"
    ACTION_EXECUTED        = "on_action_executed"
    ACTION_REGISTERED      = "on_action_registered"
    ROUTINE_ELEVATION_READY = "on_routine_elevation_ready"
```

`hooks.py` provides two dispatch modes — the choice matters:

| Function | Behaviour | When to use |
|---|---|---|
| `hooks.fire(event, *args)` | Calls all listeners **synchronously** in the request thread | Lightweight, in-request work where the caller needs completion before continuing |
| `hooks.fire_background(event, *args)` | Submits all listeners to a **ThreadPoolExecutor** | Slow or non-critical work (graph updates, signal scoring, telemetry) |

Using `fire_background` for slow work (like graph construction) prevents the ingest endpoint from blocking on plugin processing. Using `fire` for tool registration ensures tools are available before the first request is served.

---

## Route file structure

| File | Prefix | Key endpoints |
|---|---|---|
| `routes/chat.py` | — | `POST /ask`, `POST /v1/chat/completions` |
| `routes/data.py` | — | `POST /ingest`, `POST /search`, `POST /session/end`, `POST /entities/extract`, `GET /entities` |
| `routes/signals.py` | `/signals` | `GET /signals`, `POST /signals/sources`, `DELETE /signals/sources/{id}`, `POST /signals/poll` |
| `routes/actions.py` | `/actions` | `GET /actions`, `POST /actions/execute`, `GET /actions/audit`, `POST /actions/{id}/approve` |
| `routes/events.py` | — | `GET /events` (SSE stream) |
| `routes/admin.py` | — | `GET /`, `GET /health`, `GET /permissions`, `GET /review-queue`, `POST /backup`, `POST /restore`, `GET /export` |

`main.py` does only four things: create the FastAPI app, define the lifespan (startup health checks, collection init, plugin loading, executor shutdown), call `app.include_router()` for each route file, and call `plugins.load_plugins()`.

---

## Typed models

`models/` contains Pydantic models for API and service contracts:

- **Pydantic models** at route boundaries and stable service contracts (request/response shapes, serialisation)
- **Plain dataclasses or dicts** for lightweight internal data passing within a single service
- **`ToolSpec`** (frozen dataclass) — mandatory metadata for every tool registered via hooks, used by `run_tool()` for structural permission enforcement
- **Read-only tool catalog** — `services/unified_tools.py::build_tool_catalog` assembles a deterministic snapshot of LLM, MCP, capability, and action-registry tool surfaces (not wired into the chat loop). Terminology: [`docs/architecture/tool-vocabulary.md`](docs/architecture/tool-vocabulary.md).

New routes must define Pydantic request and response models. Services pass plain dicts internally.

---

## Signal infrastructure

`signals/` contains source monitors — classes that implement `SignalSource` and poll external systems for new events:

| Monitor | Source |
|---|---|
| `feed_monitor.py` | RSS and Atom feeds |
| `page_monitor.py` | Web page change detection (hash-based) |
| `calendar_monitor.py` | Calendar event stream |
| `system_monitor.py` | Local filesystem and system events |

The signal processing pipeline:

1. `SignalSource.poll()` returns a list of `Signal` objects
2. `signal_processor.py` scores each signal and stores it via `MetadataStore`
3. `hooks.fire(Event.SIGNAL_RECEIVED, signal)` notifies plugins
4. Plugins decide what to do — store, surface, route to an action

Signal sources are registered at startup via `config.get_signal_sources()`. Adding a new source: implement `SignalSource`, add a factory branch.

---

## Actions foundation

`actions/` implements the Ask/Do safety model:

| Component | Purpose |
|---|---|
| `registry.py` | Stores registered action definitions |
| `executor.py` | Enforces Ask/Do mode, calls handlers, records results |
| `audit.py` | Immutable append-only audit log (stored in Postgres) |
| `reversibility.py` | Metadata about whether an action can be undone |
| `handlers/` | One file per action domain (filesystem, calendar, etc.) |

**Ask/Do enforcement:**
- `mode=ask` → executor writes the action to the review queue and returns a pending result. Execution waits for approval.
- `mode=do` → executor runs the handler immediately and logs the result.
- **Routine elevation:** actions that accumulate `ROUTINE_ELEVATION_THRESHOLD` clean approvals are promoted from `ask` to `do` automatically. Demotion is manual.

Every action execution (success, failure, approval, rejection) is logged to `audit_log` in Postgres. The log is append-only — no updates, no deletes.

---

## Ecosystem plumbing — out-of-process capability services and the MCP surface

In addition to in-process plugins, Core can discover and interact with **out-of-process capability services** (separate containers that expose tools over HTTP) and exposes its own **MCP server** so external clients can use Lumogis as infrastructure.

```
                                ┌──────────────────────────┐
                                │   Out-of-process         │
                                │   capability services    │
                                │   (declared via env var) │
                                └────────────┬─────────────┘
              GET /capabilities              │  HTTP
              GET /health                    ▼
        ┌──────────────────────┐    ┌────────────────┐
        │ CapabilityRegistry   │◄───┤ httpx async    │
        │  (services/          │    │ client +       │
        │   capability_        │    │ APScheduler    │
        │   registry.py)       │    │ (5 min refresh,│
        │                      │    │  60 s health)  │
        └──────────┬───────────┘    └────────────────┘
                   │
        Surfaced on │
                   ▼
        ┌──────────────────────────────────────────────┐
        │ GET /            mcp_enabled, mcp_auth_      │
        │                  required, capability_       │
        │                  services{...}               │
        │ GET /health      capability_services summary │
        │ GET /capabilities  Core's own manifest       │
        │ /mcp/            FastMCP streamable HTTP     │
        │                  (stateless, JSON, 5 tools)  │
        └──────────────────────────────────────────────┘
```

Four pieces, all additive — Core boots cleanly when no capability services are configured and when the MCP SDK is absent.

### Capability manifest (`models/capability.py`)

A `CapabilityManifest` is the contract that any out-of-process service publishes at `GET /capabilities`. It declares the service's `id`, `version`, `transport` (`http` or `mcp`), `license_mode` (`community` or `commercial`), `maturity` (`preview` / `stable` / `deprecated`), the list of `tools` it exposes (with hand-coded JSON input/output schemas), required permissions, a config schema, the minimum Core version it needs, and a maintainer string. Core's own manifest is built by `mcp_server.build_core_manifest()` and served at `GET /capabilities`.

### CapabilityRegistry (`services/capability_registry.py`)

Discovers services declared in the `CAPABILITY_SERVICE_URLS` env var (comma-separated base URLs). For each URL it fetches `/capabilities`, validates the manifest, compares `min_core_version` against `__version__.py` using `packaging.version.Version`, then probes `/health`. Discovery and health checking are both async (`httpx.AsyncClient`) and parallel; `discover_sync()` and `check_all_health_sync()` wrap them with `asyncio.run()` so APScheduler — which is synchronous — can call them on its threadpool. A `transport=` constructor parameter is reserved for tests (`httpx.MockTransport`).

The registry is a singleton on `config.get_capability_registry()` and is owned by the FastAPI lifespan. Sequence at startup is strict: discover → immediate one-shot health probe → register the two interval jobs (5-minute discovery refresh, 60-second health refresh).

### Health surface

| Endpoint | What changed | Why |
|---|---|---|
| `GET /` | Adds `capability_services{<id>: {healthy, version, tools, last_seen_healthy}}` plus `mcp_enabled` and `mcp_auth_required` | The dashboard already polls this for live status; it is the natural home for detailed per-service info |
| `GET /health` | Adds a minimal `capability_services{registered, healthy}` summary | Symmetry — keeps the JSON shape predictable for external monitors without making them parse the full registry |

Capability service failures are **never** escalated to Core's overall health status. A service being down is informational; the 200/503 contract on `/health` continues to track Postgres only.

### MCP server surface (`mcp_server.py` + `routes/capabilities.py`)

Core exposes five read-only community tools over MCP at `/mcp/`:

| MCP tool | Backing service helper |
|---|---|
| `memory.search` | `services.memory.retrieve_context` |
| `memory.get_recent` | `services.memory.recent_sessions` |
| `entity.lookup` | `services.entities.lookup_by_name` |
| `entity.search` | `services.entities.search_by_name` |
| `context.build` | `services.search.semantic_search` + `services.memory.retrieve_context` + `services.context_budget.truncate_text` |

All tools are thin wrappers — no business logic in `mcp_server.py`. The three new helpers in `services/memory.py` and `services/entities.py` mirror the existing `routes/data.py::list_entities` error-handling pattern: warn + return empty answer (`[]` or `None`) on any DB failure, never raise.

Transport: `FastMCP(stateless_http=True, json_response=True)` mounted at `/mcp` via `app.mount`. Stateless mode is a deliberate scope choice — every tool is read-only and self-contained, so no sessions, no streaming, no server→client notifications. A future stateful MCP surface (e.g. for long-running KG queries) belongs in a separate capability service rather than Core. The canonical client URL is **`/mcp/`** with the trailing slash; `POST /mcp` triggers a 307 redirect that some HTTP clients drop the `Authorization` header on.

Lifespan integration: the SDK's `StreamableHTTPSessionManager` requires its anyio task group to be active even in stateless mode, so the FastAPI lifespan calls `mcp.session_manager.run().__aenter__()` after building a fresh `FastMCP` via `build_fastmcp()`. The factory is rebuilt per lifespan startup because `session_manager.run()` is single-shot per `FastMCP` instance — production lifespans run once so reuse would work, but `TestClient(main.app)` starts a fresh lifespan per test.

### Auth

The `auth_middleware` in `auth.py` gates `/mcp/*` independently of `AUTH_ENABLED` using `MCP_AUTH_TOKEN`. When the env var is set, every `/mcp/*` request must present `Authorization: Bearer <token>`; comparison is timing-safe via `hmac.compare_digest`. When unset, `/mcp/*` is open — the documented single-user-local default. `GET /capabilities` is **never** gated; the manifest is the public discovery contract.

### What is NOT in scope here

- **Plugin loading** is unchanged. Capability services are out-of-process containers; plugins are in-process Python packages under `plugins/`. They serve different extension points.
- **`plugins/graph/`** is untouched.
- The MCP surface intentionally exposes only community tools. Premium/commercial tools (graph queries, multi-source context packs, etc.) belong in their own capability services that Core discovers via `CAPABILITY_SERVICE_URLS`, not in `mcp_server.py`.

See [ADR-010 — Ecosystem plumbing](docs/decisions/010-ecosystem-plumbing.md) for the rationale and known technical debt.

---

## Plugin system

Plugins live in `plugins/<name>/` with an `__init__.py`. The plugin loader scans subdirectories on startup and imports each one.

Plugin interaction points:

```python
# Listen for events
hooks.register(Event.DOCUMENT_INGESTED, my_callback)

# Register a tool
hooks.fire(Event.TOOL_REGISTERED, ToolSpec(
    name="my_tool",
    description="...",
    input_schema={...},
    mode="ask",
))

# Register an action handler
hooks.fire(Event.ACTION_REGISTERED, MyActionHandler())

# Expose an API route
router = APIRouter(prefix="/my-plugin")

@router.get("/status")
def status(): ...
```

**Default for plugin packages:** import only from `ports/`, `models/`, `events.py`, and `hooks.py`. Do not import from `services/` or `adapters/`.

**`config`:** In-tree, first-party plugins (e.g. the shipped `plugins/graph` package) may import `config` for documented graph-mode and factory access only. That is a narrow exception, not a general second wiring layer — see [`docs/architecture/plugin-imports.md`](docs/architecture/plugin-imports.md) for the full rule and the distinction from out-of-tree plugins.

Named plugin directories are listed in `.gitignore` so that plugin packages developed outside this repository can be mounted at deploy time without being tracked here. The orchestrator loads whatever is present — plugins are additive, never required. See [ADR-005](docs/decisions/005-plugin-boundary.md).

---

## Running tests and linting

```bash
make test               # unit tests — no Docker needed (mock adapters in conftest.py)
make lint               # ruff check + ruff format --check
make test-integration   # full-stack integration tests — requires docker compose up -d
```

Unit tests use mock adapters from `tests/conftest.py`. No running Docker services are needed for the unit suite. Integration tests in `tests/integration/` run against the live stack via `httpx`.
