# lumogis-graph

Standalone, out-of-process knowledge-graph capability service for Lumogis.

This is the Pass-2 build artefact of the [lumogis-graph extraction
plan](../../.cursor/plans/lumogis_graph_service_extraction.plan.md). It runs
as a separate FastAPI process behind Core, owning all FalkorDB writes and
exposing six `graph.*` tools via a mounted FastMCP server at `/mcp`.

## Quick reference

| Concern        | Where it lives in this tree |
| -------------- | --------------------------- |
| HTTP entrypoint     | `main.py`                                  |
| Wire contracts      | `models/webhook.py` (vendored from Core)   |
| Auth shim           | `auth.py` (single-user default; opt-in JWT)|
| Webhook intake      | `routes/webhook.py` → `webhook_queue.submit` |
| Synchronous context | `routes/context.py` (35 ms in-route budget) |
| Operator UI         | `routes/mgm.py` + `static/graph_mgm.html`  |
| Graph projection    | `graph/writer.py`                          |
| Graph queries       | `graph/query.py`                           |
| Reconciliation      | `graph/reconcile.py` (incl. orphan GC)     |
| Quality jobs        | `quality/{deduplication,edge_quality,...}.py` |
| MCP server          | `kg_mcp/server.py` (named `kg_mcp/` to avoid shadowing the `mcp` PyPI package) |
| Tests               | `tests/` + `conftest.py` (pytest, in-container)         |

## HTTP authentication

KG endpoint auth is **not** the same as Core’s FastAPI `require_admin` matrix.
**Canonical table:** [§6.4 — lumogis-graph service endpoints](../../docs/kg_reference.md#64-lumogis-graph-service-endpoints) in `docs/kg_reference.md` (open paths, `AUTH_ENABLED` + JWT, `GRAPH_WEBHOOK_SECRET`, `X-Graph-Admin-Token`, `MCP_AUTH_TOKEN`).

## Build & run

The service is built from the **repo root** because the Dockerfile pulls in
`services/lumogis-graph/` as part of the build context. Compose orchestrates
both pieces — see `docker-compose.premium.yml` (Pass 3).

```bash
# Standalone build (sanity check):
docker build -f services/lumogis-graph/Dockerfile -t lumogis-graph:dev .

# In a full Lumogis stack (the supported path):
docker compose -f docker-compose.yml -f docker-compose.premium.yml up -d
```

## Tests

```bash
# In-container (the recommended path — matches CI):
make compose-test-kg

# Local venv (contributors only):
make test-kg
```

`make compose-test-kg` builds the `test` stage of the Dockerfile (which
extends the production runtime venv with pytest/pytest-asyncio/ruff and
copies `tests/` + `conftest.py` back in) and runs the full suite inside it.
The production image (default `docker build` target) excludes test
artefacts entirely — verified by the runtime stage's explicit
`rm -rf /app/tests /app/conftest.py /app/requirements-dev.txt` step.

Default test env (set by the Make target so unit tests never touch a real
backend):

| Var | Value | Why |
| --- | ----- | --- |
| `GRAPH_BACKEND`              | `falkordb` | satisfies `main.py:_hard_fail_if_no_falkordb` |
| `KG_ALLOW_INSECURE_WEBHOOKS` | `true`     | webhook auth tests turn this off explicitly |
| `KG_SCHEDULER_ENABLED`       | `false`    | keeps `register_scheduled_jobs` a no-op  |
| `LOG_LEVEL`                  | `ERROR`    | quieter test output                      |

## Vendored files

`models/webhook.py` is a byte-identical copy of the canonical
`orchestrator/models/webhook.py`. After editing the Core copy run:

```bash
make sync-vendored
```

…and commit both files together. CI will fail if they drift.

## Environment

Configuration is shared with Core via the same env var names. See the
extraction plan §"Environment variables (full list)" for authoritative
docs. Service-specific knobs:

| Variable | Default | Purpose |
| -------- | ------- | ------- |
| `KG_SERVICE_PORT`           | `8001`  | uvicorn bind port |
| `KG_SCHEDULER_ENABLED`      | `true`  | turn off the daily reconcile / weekly quality jobs (e.g. dual-cluster setups) |
| `KG_ALLOW_INSECURE_WEBHOOKS`| `false` | dev-only opt-in to accept `/webhook` and `/context` without `GRAPH_WEBHOOK_SECRET` set |
| `GRAPH_WEBHOOK_SECRET`      | unset   | bearer token Core presents on `/webhook` and `/context` |
| `GRAPH_ADMIN_TOKEN`         | unset   | if set, `X-Graph-Admin-Token` on mutating `/kg/*`, `GET /graph/health` (this service), etc. — see [§6.4](../../docs/kg_reference.md#64-lumogis-graph-service-endpoints) |
| `MCP_AUTH_TOKEN`            | unset   | gates `/mcp/*` calls from external MCP clients |

## License

AGPL-3.0-only. This service ships in the public `lumogis/lumogis`
repository. Premium or commercial **distributions** may omit or replace it per
product packaging; the extraction keeps the graph capability boundary clear in
compose and manifests. See `LICENSE` at repo root.
