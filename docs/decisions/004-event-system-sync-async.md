# ADR-004: Two hook dispatch modes (fire vs fire_background)

## Context

Plugins subscribe to events (document ingested, signal received, etc.). Some handlers are fast (register a tool); others are slow (graph updates, IO). A single global async event bus was considered.

## Decision

Expose two explicit APIs: **`hooks.fire()`** (synchronous, same thread) and **`hooks.fire_background()`** (thread pool).

## Consequences

- **Predictability:** Callers know whether completion is guaranteed before return; tool registration can rely on sync fire before first request.
- **Latency:** Ingest and chat paths are not blocked by heavy plugin work when plugins use `fire_background`.
- **Simplicity:** No asyncio re-entrancy issues inside sync FastAPI routes and background tasks.
- **Trade-off:** Background callbacks must be thread-safe; document in plugin guidelines.
