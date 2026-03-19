# Lumogis Architecture

This document explains how the lumogis orchestrator is structured and how the pieces fit together. Read this before contributing code. For decisions on *why* specific technologies were chosen, see `docs/decisions/`.

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

Plugins may import from: `ports/`, `models/`, `events.py`, `hooks.py`.
Plugins must **never** import from: `services/`, `adapters/`, `config.py`.

Named plugin directories are listed in `.gitignore` so that plugin packages developed outside this repository can be mounted at deploy time without being tracked here. The orchestrator loads whatever is present — plugins are additive, never required. See [ADR-005](docs/decisions/005-open-core-boundary.md).

---

## Running tests and linting

```bash
make test               # unit tests — no Docker needed (mock adapters in conftest.py)
make lint               # ruff check + ruff format --check
make test-integration   # full-stack integration tests — requires docker compose up -d
```

Unit tests use mock adapters from `tests/conftest.py`. No running Docker services are needed for the unit suite. Integration tests in `tests/integration/` run against the live stack via `httpx`.
