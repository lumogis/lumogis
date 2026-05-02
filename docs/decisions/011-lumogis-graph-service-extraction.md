# ADR: Lumogis Graph Service Extraction (`lumogis-graph`)

## Status

Finalised by `/verify-plan` on 2026-04-17 — implementation matches the architectural decision recorded here. Copied to `docs/decisions/011-lumogis-graph-service-extraction.md`.

### Status history

- 2026-04-17: Created (post-implementation) by `/verify-plan` because the original plan declared `adr: none` despite the work being a major architectural decision.
- 2026-04-17: Finalised by `/verify-plan` — implementation confirmed every recorded decision (Core ↔ KG boundary, webhook contract, mode switch, premium tier).
- 2026-04-30: Docs Librarian — corrected §Decision text that implied `services/lumogis-graph/` exists only in a private fork; source ships in the public `lumogis/lumogis` repo; commercial **packaging** may still omit or replace the image (see `services/lumogis-graph/README.md`).

## Context

Up to 0.3.0rc1, all knowledge-graph functionality (entity projection into FalkorDB, daily reconciliation, weekly quality jobs, the `query_graph` LLM tool, `/context` injection during chat, the `/mgm` operator UI) lived inside Core's `orchestrator/plugins/graph/` directory and ran in-process under the orchestrator's Python runtime. ADR-007 (graph plugin architecture) and ADR-008 (graph provenance model) recorded that decision.

This in-process posture became a constraint as the graph layer grew:

1. **Independent release cadence.** The graph quality pipeline (Splink, DuckDB, edge scoring, deduplication) evolves on a different timeline from Core's chat/ingest path. Pinning Splink versions inside Core forces operators to upgrade graph code every time they upgrade Core, and vice versa.
2. **Commercial / community boundary.** Lumogis Core is AGPL-3.0; the graph quality pipeline is a candidate for a commercial premium tier. A package that lives inside the public `lumogis/lumogis` repo cannot be commercial.
3. **Resource isolation.** Splink quality jobs can balloon to ~1–2 GB on a 50k-entity corpus; an in-process job can OOM the orchestrator process and take chat/ingest down with it.
4. **Heavy / conflicting dependencies.** `splink>=4.0,<5` and `duckdb>=1.0,<2` are large, GPU-irrelevant, and have no business in the chat-path container image.

ADR-010 (ecosystem plumbing — capability services and MCP server surface) put the infrastructure in place — `CapabilityManifest`, `CapabilityRegistry`, `CAPABILITY_SERVICE_URLS`, health probing — but no first-class out-of-process service had yet been built. The graph extraction is the first concrete consumer of that infrastructure.

## Decision

### 1. Out-of-process service named `lumogis-graph`

Extract the graph layer into a standalone FastAPI service under `services/lumogis-graph/`, packaged as a Docker image and added to the stack via a separate `docker-compose.premium.yml` overlay. The service:

- Shares Postgres, Qdrant, and FalkorDB instances with Core (phase 1; full database decoupling is deferred).
- Exposes `GET /health`, `GET /capabilities`, `GET /mgm`, `POST /webhook`, `POST /context`, `POST /tools/query_graph`, `GET /graph/health`, plus an internal `/mcp` FastMCP surface with six `graph.*` tools.
- Auto-discovered by Core via the existing `CAPABILITY_SERVICE_URLS` mechanism (no new discovery infrastructure).
- Owns its own APScheduler (daily reconciliation + weekly quality job).
- Runs as a non-root user, mem-limited (`KG_MEM_LIMIT=4g` default, env-overridable for small dev machines).
- Does NOT publish a host port by default. Operators who need direct browser access route via reverse proxy.

`services/lumogis-graph/` ships in this repository under the AGPL (see `services/lumogis-graph/README.md`). A **commercial packaging** of the same capability may ship only in a private product fork; the capability boundary is expressed via `CapabilityManifest.license_mode = "commercial"` where applicable.

### 2. `GRAPH_MODE` switch on Core (`inprocess` | `service` | `disabled`)

A new env var on Core gates which graph path is active. Default is `inprocess`, which preserves all existing behaviour byte-for-byte; existing operators upgrade with no configuration change.

| Mode | Plugin | Webhook dispatch | `/context` lookup | Weekly KG quality job | `query_graph` tool |
|------|--------|------------------|-------------------|------------------------|---------------------|
| `inprocess` (default) | active | n/a | in-process via `CONTEXT_BUILDING` hook | scheduled on Core | in-process plugin spec |
| `service` | self-disables | Core POSTs to KG `/webhook` per hook event | Core POSTs to KG `/context` (40 ms hard timeout) | NOT scheduled on Core (KG owns it) | proxy ToolSpec POSTs to KG `/tools/query_graph` |
| `disabled` | self-disables | none | none | NOT scheduled | not registered |

`get_graph_mode()` is `@functools.cache`-decorated and read once per process; switching modes requires a Core restart (acceptable per O6 in the plan).

### 3. Webhook + `/context` HTTP contract is the long-term boundary

Core ↔ KG communication uses a small, stable HTTP contract:

- **Webhook envelope** (`orchestrator/models/webhook.py`):
  ```
  WebhookEnvelope { schema_version: int, event: WebhookEvent, occurred_at: datetime, payload: dict }
  ```
  with six concrete payload models (`DocumentIngestedPayload`, `EntityCreatedPayload`, `SessionEndedPayload`, `EntityMergedPayload`, `NoteCapturedPayload`, `AudioTranscribedPayload`). The `WebhookEvent` enum values are the names of the corresponding `graph.writer.on_*` handlers, which makes routing trivial.
- **`POST /context`** request: `{query, user_id, max_fragments}` → response: `{fragments: list[str]}`. The 35 ms in-route budget on the KG side and the 40 ms client-side `httpx` timeout on the Core side together guarantee the chat hot path never blocks for more than the budget.
- **`POST /tools/query_graph`**: pass-through of the LLM's tool input dict; response is the same JSON the in-process plugin produces, so prompt parity is preserved.
- **Bearer auth** via `GRAPH_WEBHOOK_SECRET`. Comparison uses `hmac.compare_digest`. Default-safe: when no secret is configured AND `KG_ALLOW_INSECURE_WEBHOOKS=true` is not set, the KG service refuses webhooks with 503.

This contract is committed to as the long-term boundary. Future graph-capability service implementations (different storage, different language, different vendor) MUST honour it. Adding a new event is a four-step coordinated change (Core hook `Event` constant, KG `WebhookEvent` member, payload class, `_PAYLOAD_BY_EVENT` entry); existing events do not change.

### 4. Plugin self-disables; the legacy plugin stays in Core for one release

`orchestrator/plugins/graph/__init__.py` reads `config.get_graph_mode()` at import time. When the value is anything other than `inprocess`, it sets `router = None` and skips hook/scheduler registration entirely. The plugin loader (`plugins/__init__.py:load_plugins`) already tolerates `router = None`, so no loader change was needed.

The plugin code itself stays in `orchestrator/plugins/graph/` for the 0.3.x release line. Its removal is a follow-up plan: removing it now would force every operator onto `service` mode before that mode has burned in.

### 5. New `management_url` field on `CapabilityManifest`

Added an optional `management_url: str | None = None` to `CapabilityManifest` so capability services that ship a UI panel (the KG service ships `/mgm`) can advertise an absolute, operator-browser-resolvable URL. Core's status page renders it as a clickable link. The default for the KG service points at the in-network URL; deployments behind a reverse proxy override `KG_MANAGEMENT_URL`.

This is now a public manifest contract. Future capability services SHOULD use it whenever they ship a UI.

## Consequences

### Positive

- The graph layer can be released, versioned, licensed, and resource-scoped independently of Core.
- The premium tier has a clean physical boundary (a different Docker image, a different repo path) that maps to a clean licence boundary.
- Splink/DuckDB are no longer in Core's chat-path image (~150 MB removed from the orchestrator dependency closure).
- Operators who don't want graph functionality (`GRAPH_MODE=disabled`) get a strictly smaller, faster Core.
- The webhook contract is the first concrete validation of ADR-010's CapabilityManifest infrastructure; it surfaced the need for `management_url` and proves the discovery flow works for a real second service.

### Negative / accepted trade-offs

- **Cross-process writes to Core-owned tables (O2).** Phase 1 KG writes `graph_projected_at` directly to Core's `entities`, `sessions`, `documents`, `notes`, `audio_memos`, `file_index`. This is the simplest path (writer code byte-identical to the in-process version) but sets a precedent. A KG-owned `graph_projection_state` table is the planned phase-2 fix.
- **Plugin code duplicated for one release.** `orchestrator/plugins/graph/` still exists; `services/lumogis-graph/graph/` is its near-twin. A single source of truth ships in the cleanup plan.
- **Vendored models.** `models/webhook.py` and `models/capability.py` are re-vendored under `services/lumogis-graph/models/`. Drift is gated by `make sync-vendored`.
- **`GRAPH_MODE` is read once per process.** Live mode-switch is not supported. Operators who want to switch must restart Core (and start/stop the KG container). Acceptable for the phase-1 single-operator deployment model.

### Operational notes

- `docker-compose.premium.yml` declares `depends_on: lumogis-graph: service_healthy` on the `orchestrator` service so Core does not start firing webhooks before KG is ready.
- KG's lifespan performs an explicit FalkorDB warm-up so the first `/context` call hits a warm connection inside the 35 ms budget.
- Webhook-loss recovery is via daily reconciliation + a new `garbage_collect_orphan_nodes` pass that handles the `ENTITY_MERGED` corner case where the loser row is already deleted.

## Alternatives considered

1. **Keep the graph plugin in Core.** Rejected: forces every operator onto Splink's release cadence; cannot become a commercial tier; the AGPL boundary stays at the wrong place.
2. **Extract via a message queue (Kafka, RabbitMQ).** Rejected: introduces a hard new dependency for what is fundamentally a single-operator self-hosted product; HTTP webhooks + a four-worker `ThreadPoolExecutor` on the KG side cover the actual throughput need with zero new infrastructure.
3. **Do not vendor models — KG imports from Core via a wheel.** Rejected: would make the public `lumogis/lumogis` wheel a hard dependency of the private `lumogis-graph` image. The vendoring + `make sync-vendored` gate is a smaller surface to maintain than a published wheel.
4. **Make the `/context` lookup async and remove the 40 ms cap.** Rejected: the chat path is synchronous today; introducing async there is a bigger change than this plan can absorb. The 40 ms budget plus the KG warm-up step is enough to keep `[Graph]` lines flowing without changing the chat-path threading model.

## Related ADRs

- [ADR-007: Graph plugin architecture](007-graph-plugin-architecture.md) — the in-process design this extraction supersedes for `service` mode.
- [ADR-008: Graph provenance model](008-graph-provenance-model.md) — preserved unchanged (KG writes the same provenance fields).
- [ADR-010: Ecosystem plumbing — capability services and MCP server surface](010-ecosystem-plumbing.md) — the infrastructure layer this extraction is the first consumer of.

## Implementation references

- Plan: *(maintainer-local only; not part of the tracked repository)*
- Implementation commits: `c3bc036` (Pass 1), `016b37f` / `f9aa501` / `f94f236` (Pass 2), `ef2098b` (Pass 3)
- Verification: `/verify-plan` 2026-04-17 — 575 Core tests + 55 KG service tests passing; parity test infrastructure built (`make test-graph-parity`) but not run live in this verification pass.
