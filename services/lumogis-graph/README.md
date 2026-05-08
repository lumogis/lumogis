# lumogis-graph

Standalone, out-of-process knowledge-graph capability service for Lumogis.

This is the first-party implementation of the [lumogis-graph service extraction ADR](../../docs/decisions/011-lumogis-graph-service-extraction.md) and related closeout docs. It runs
as a separate FastAPI process behind Core, owning FalkorDB writes and
exposing six `graph.*` tools via a mounted FastMCP server at `/mcp`.

The supported Compose merge is **`docker-compose.yml` + `docker-compose.falkordb.yml` + `docker-compose.premium.yml`** (the **`premium`** filename is historical; the overlay adds the `lumogis-graph` service and env wiring for `GRAPH_MODE=service`).

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
**Summary:** Public/health-style paths stay open by default; **`/webhook`** and **`/context`** require **`GRAPH_WEBHOOK_SECRET`** when not using the dev-only insecure flag; mutating **`/kg/*`** may require **`X-Graph-Admin-Token`** when **`GRAPH_ADMIN_TOKEN`** is set; the FastMCP mount can be gated with **`MCP_AUTH_TOKEN`**. See **`routes/`** (health, capabilities, webhook, context, kg, MCP) and **`auth.py`** for the exact matrix.

## Build & run

The service is built from the **repo root** because the Dockerfile pulls in
`services/lumogis-graph/` as part of the build context. Compose orchestrates
both pieces — merge **`docker-compose.yml`**, **`docker-compose.falkordb.yml`**, and **`docker-compose.premium.yml`** (see comments in those files).

```bash
# Standalone build (sanity check):
docker build -f services/lumogis-graph/Dockerfile -t lumogis-graph:dev .

# In a full Lumogis stack (the supported path):
docker compose -f docker-compose.yml -f docker-compose.falkordb.yml -f docker-compose.premium.yml up -d
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
| `GRAPH_ADMIN_TOKEN`         | unset   | if set, `X-Graph-Admin-Token` on mutating `/kg/*`, `GET /graph/health` (this service), etc. — see **HTTP authentication** above and `routes/` |
| `MCP_AUTH_TOKEN`            | unset   | gates `/mcp/*` calls from external MCP clients |

## License

AGPL-3.0-only. This service ships in the public `lumogis/lumogis`
repository. Premium or commercial **distributions** may omit or replace it per
product packaging; the extraction keeps the graph capability boundary clear in
compose and manifests. See `LICENSE` at repo root.
