# Lumogis Technical Debt Register

Tracks known debt items: when they were introduced, their current status, and
any resolution notes. Resolved items are kept for audit purposes.

---

## entity_ids on sessions (resolved in M2 close-out)

**Status:** Resolved
**Introduced:** M2
**Resolved:** M2 close-out

### Background

During M2, `reconcile_sessions()` had to fall back to name-string resolution
for `DISCUSSED_IN` edges because the `sessions` table did not store the
resolved entity UUIDs from `store_entities()`.  This made reconciliation
weaker than the live hook path (`SESSION_ENDED` receives `entity_ids` directly
from the caller).

### Resolution

Sessions now persist `entity_ids` as a `TEXT[]` UUID array alongside the
session record (migration `003`, `ALTER TABLE sessions ADD COLUMN IF NOT EXISTS
entity_ids TEXT[] NOT NULL DEFAULT '{}'`).

`store_session()` in `services/memory.py` accepts `entity_ids` and upserts the
row into Postgres in addition to writing the semantic embedding to Qdrant.
`routes/data.py` reorders the call so `store_entities()` completes before
`store_session()` is called, ensuring UUIDs are always available.

`reconcile_sessions()` now reads `entity_ids` from the DB row and passes them
directly to `project_session()`.  The name-string fallback (`entity_ids=None`)
is retained only for historical rows where `entity_ids` is empty — sessions
recorded before this change was deployed.

### Impact

- Live projection and reconciliation are now equivalent in edge quality.
- No Qdrant-to-Postgres migration was required; Qdrant data is unchanged.
- The fallback path remains so old rows are not broken.

---

## Graph stats: FalkorDB node count hardcoded to `user_id = "default"` (M4)

**Status:** Open
**Introduced:** M4 (`GET /graph/stats` in `orchestrator/plugins/graph/viz_routes.py`)

### Background

The stats endpoint counts graph nodes with a Cypher filter scoped to
`user_id = "default"`. That matches today’s single-user posture and is
consistent with other graph paths that assume one logical tenant.

Postgres-backed fields on the same response (e.g. top entities by
`mention_count`) already use the authenticated `user_id` from `get_user()`.

### Debt

When multi-tenant isolation lands (Phase 6), the FalkorDB node count (and any
other graph-wide aggregates that assume a single tenant) must use the same
`user_id` as the rest of the request, not a literal `"default"`, so tenants
cannot infer global graph size and counts stay private per tenant.

### Resolution (when done)

- Parameterise the stats Cypher with `$uid` from auth-derived `user_id`.
- Revisit any similar literals in viz or admin graph endpoints added before Phase 6.

---

## Sync/async consistency across the service and adapter layer (0.3.0rc1)

**Status:** Open
**Introduced:** 0.3.0rc1 (`orchestrator/services/capability_registry.py` —
the first piece of async business logic in the codebase)

### Background

The entire service and adapter layer of Lumogis is synchronous. Routes,
services, and adapters all use blocking calls into `psycopg`, the Qdrant
client, the Ollama HTTP client, and so on. FastAPI runs synchronous
handlers on its threadpool and this has been adequate for the single-user
local deployment that the project is designed for.

`CapabilityRegistry.discover()` (Area 2 of the ecosystem-plumbing work)
introduced the **first async business logic** because `httpx.AsyncClient`
makes parallel manifest fetches across capability services trivial and
because the registry runs entirely outside the request path (in lifespan
startup and APScheduler jobs), so it is the safest place to introduce
async without touching anything else.

### Debt

The codebase now has two execution models. `discover_sync()` and
`check_all_health_sync()` bridge them with `asyncio.run()` for APScheduler,
but every future async addition compounds the asymmetry. This is acceptable
for 0.3.0's single-user local target; it is not the right long-term posture.

### Resolution (when done)

A coordinated migration should be **triggered by the first multi-user
deployment requirement**, not by a 0.3.x feature. When that day comes:

1. Migrate `adapters/postgres_store.py` to `asyncpg` behind the existing
   `MetadataStore` Protocol (the Protocol becomes async).
2. Migrate `adapters/qdrant_store.py` to the async client behind the
   `VectorStore` Protocol.
3. Convert `services/*` to async one file at a time, using the now-async ports.
4. Convert `routes/*` to async handlers.
5. Drop the `_sync` wrappers in `CapabilityRegistry`. APScheduler integration
   becomes `add_job(asyncio.create_task, args=[coro])` or moves to an async scheduler.

See [ADR-010 — Ecosystem plumbing](010-ecosystem-plumbing.md#known-technical-debt-syncasync-consistency)
for the original design decision and rationale.
