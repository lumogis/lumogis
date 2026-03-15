# Lumogis Architecture

This document explains how the Lumogis orchestrator is structured and how the pieces fit together. Read this before contributing code.

## Three concepts

| Concept    | Lives in            | Purpose                                                |
| ---------- | ------------------- | ------------------------------------------------------ |
| **Service** | `services/`         | Business logic — ingest, search, entity extraction     |
| **Adapter** | `adapters/`         | Concrete backend implementation (Qdrant, Postgres, …)  |
| **Plugin**  | `plugins/<name>/`   | Optional, self-contained extension (graph, future MCP) |

Services call adapters **only via ports** (Protocol interfaces in `ports/`). A service never imports a concrete adapter directly.

## Port / Adapter pattern

```
services/ingest.py
    ↓ calls
ports/vector_store.py  (Protocol)
    ↑ implements
adapters/qdrant_store.py
```

`config.py` reads environment variables and returns cached singleton adapters. Every `get_*()` call returns the same object after first construction:

```python
from config import get_vector_store
vs = get_vector_store()  # QdrantStore or future alternative
vs.upsert(...)
```

Swapping a backend means writing a new adapter that satisfies the Protocol, adding a branch in `config.py`, and setting the env var. No service code changes.

### Ports with ping()

Every port representing a running service includes `ping() -> bool` for startup health checks. Exception: `Reranker` has no `ping()` — it is a loaded model, not a network service.

## Event constants and hooks

`events.py` defines string constants for all hook events:

```python
class Event:
    DOCUMENT_INGESTED = "on_document_ingested"
    ENTITY_CREATED    = "on_entity_created"
    SESSION_ENDED     = "on_session_ended"
    TOOL_REGISTERED   = "on_tool_registered"
```

`hooks.py` provides the dispatch mechanism:

- `hooks.register(event, callback)` — attach a listener
- `hooks.fire(event, *args)` — call all listeners synchronously (use for lightweight, in-request work)
- `hooks.fire_background(event, *args)` — call listeners in a thread pool (use for slow, non-blocking work like graph updates)

Plugins and services use `Event` constants for all hook calls. Never use raw strings.

## Route file structure

| File            | Endpoints                                          |
| --------------- | -------------------------------------------------- |
| `routes/chat.py`  | `/ask`, `/v1/chat/completions`                    |
| `routes/admin.py` | `/health`, `/permissions`, `/review-queue`         |
| `routes/data.py`  | `/ingest`, `/search`, `/session/end`, `/entities` |

`main.py` only does app creation, lifespan (health checks, collection init, plugin loading, shutdown), and `app.include_router()` calls.

## Typed models

`models/` contains Pydantic models for API/service contracts and the `ToolSpec` dataclass:

- **Pydantic models** at route boundaries and stable service contracts (request/response shapes)
- **Plain dataclasses or dicts** for lightweight internal data passing within a single service
- **`ToolSpec`** (frozen dataclass) — mandatory metadata for every tool, used by `run_tool()` for structural permission enforcement

## Plugin system

Plugins live in `plugins/<name>/` with an `__init__.py`. The plugin loader in `plugins/__init__.py` scans subdirectories on startup and imports each one.

Plugins interact with core via hooks:
1. Register event listeners: `hooks.register(Event.DOCUMENT_INGESTED, my_callback)`
2. Register tools: `hooks.fire(Event.TOOL_REGISTERED, ToolSpec(...))`

Plugins may import from `ports/`, `models/`, `events.py`, and `hooks.py`. They must **never** import from `services/` or `adapters/` directly.

## Open-core boundary

| Repository       | Contains                                                    |
| ---------------- | ----------------------------------------------------------- |
| **lumogis-core** | `orchestrator/` (ports, adapters, services, config, routes, models, hooks, events, tests), `docker-compose.yml`, `Makefile`, `pyproject.toml`, CI |
| **lumogis-app**  | `plugins/graph/`, `config/librechat.yaml`, LibreChat customizations |

## Dependency direction

```
routes → services → ports ← adapters
                  ↕
               config
                  ↑
               plugins (via hooks only)
```

Arrows point in the direction of imports. Plugins never import services; they interact via hooks.

## Running tests and linting

```bash
make test   # runs pytest inside orchestrator/tests/
make lint   # runs ruff check + ruff format --check
```

Tests use mock adapters from `tests/conftest.py` — no running Docker services needed for the unit test suite.
