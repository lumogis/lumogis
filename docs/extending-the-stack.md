# Extending the stack

lumogis is built to be extended at every layer. This document covers two kinds of extension:

- **Stack add-ons** — optional Docker Compose overlays that activate additional capability
- **Code extensions** — adapters, plugins, signal sources, and action handlers written in Python

Both work independently. You can add a stack add-on without touching code, and you can ship a new adapter without changing any Docker configuration.

---

## How the overlay mechanism works

The stack is assembled from Compose files using the `COMPOSE_FILE` environment variable. `make setup` sets this automatically based on your hardware, but you can extend it by appending overlays:

```bash
# In your .env
COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml:docker-compose.litellm.yml
```

Each overlay is a standard Compose file that adds new containers or extends existing ones. Docker Compose merges them in order — later files extend or override what earlier files defined. This is how `docker-compose.gpu.yml` adds GPU resources to the Ollama container without touching the base file: it just redefines the `ollama` service with `deploy.resources` added.

After changing `COMPOSE_FILE`:

```bash
docker compose up -d
```

---

## Optional backends

These are containers that specific adapters in the orchestrator connect to — the same relationship that Qdrant and Postgres have to the core. They are optional because each has a fallback: a simpler adapter that handles the common case without the extra container.

### FalkorDB — graph store for graph plugins

**File:** `docker-compose.falkordb.yml`  
**Why it is included:** The `GraphStore` port (`ports/graph_store.py`) defines the interface any graph backend must implement. FalkorDB is the reference backend — a lightweight, MIT-licensed property graph store that uses the Redis protocol. The orchestrator fires `Event.ENTITY_CREATED` and `Event.DOCUMENT_INGESTED` hooks; a graph plugin subscribes to these and writes nodes and edges. Graph plugins define their own schemas. Core itself never imports or connects to FalkorDB — it is entirely the plugin's concern.

Enable this overlay when you are building or running a graph plugin.

**Enable:**

```bash
# Add to COMPOSE_FILE in .env
COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml:docker-compose.falkordb.yml

# Add to .env
FALKORDB_URL=redis://falkordb:6379
```

```bash
docker compose up -d falkordb
```

---

### ntfy — push notifications

**File:** `docker-compose.yml` (commented block)  
**Why it is included:** The orchestrator has a `Notifier` port (`ports/notifier.py`). The signal processor calls it for every high-relevance signal, and the daily digest calls it on schedule. By default, `config.get_notifier()` returns a `NullNotifier` that drops all notifications silently. Enabling ntfy wires up the real `NtfyNotifier` adapter (`adapters/ntfy_notifier.py`) — no code changes, just a config switch.

ntfy is the reference `Notifier` implementation for self-hosters: free, self-hosted, no external cloud dependency, and has a mobile app for receiving push notifications.

**Enable:** Uncomment the `ntfy` service and `ntfy_data` volume in `docker-compose.yml`, then add to `.env`:

```bash
NOTIFIER_BACKEND=ntfy
NTFY_URL=http://ntfy:80
NTFY_TOPIC=lumogis
# NTFY_TOKEN=   # set if you enable ntfy access control
```

```bash
docker compose up -d ntfy
```

ntfy web UI: [http://localhost:8088](http://localhost:8088). Subscribe on mobile via the ntfy app using your server address and the `lumogis` topic.

---

### Playwright — JS-rendered page fetching

**File:** `docker-compose.playwright.yml`  
**Why it is included:** The signal pipeline has two page-fetching adapters: `page_scraper.py` (uses trafilatura — fast, no extra container, works for most sites) and `playwright_fetcher.py` (uses a Playwright browser — handles SPAs, JS-rendered content, and dynamically loaded feeds). Both implement the `SignalSource` protocol. When `PLAYWRIGHT_ENABLED=false`, the feed monitor routes `playwright` source types to the scraper as a fallback. When enabled, it routes them to the Playwright fetcher instead. No code changes required.

**Enable:**

```bash
# Add to COMPOSE_FILE in .env
COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml:docker-compose.playwright.yml

# Add to .env
PLAYWRIGHT_ENABLED=true
PLAYWRIGHT_URL=http://playwright:3000
```

```bash
docker compose up -d playwright
```

Set `source_type=playwright` on any signal source that requires full browser rendering.

---

## Proxy and observability

### LiteLLM — rate limiting and model observability

**File:** `docker-compose.litellm.yml`  
**Why it is included:** Both LLM adapters (`anthropic_llm.py` and `openai_llm.py`) already accept a `proxy_url` that overrides `base_url` transparently. LiteLLM is an OpenAI-compatible proxy that sits between the orchestrator and your LLM providers. The orchestrator has no idea it is there — it just sends requests to a different URL. Zero code changes. LiteLLM adds spend tracking, rate limiting, and a unified log across all providers.

**Enable:**

```bash
# Add to COMPOSE_FILE in .env
COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml:docker-compose.litellm.yml
```

Uncomment `proxy_url` on whichever models you want to route through it in `config/models.yaml`:

```yaml
models:
  claude:
    adapter: anthropic
    model: claude-sonnet-4-20250514
    api_key_env: ANTHROPIC_API_KEY
    proxy_url: http://litellm:4000   # ← enable this
```

```bash
docker compose up -d litellm
```

LiteLLM dashboard: [http://localhost:4000](http://localhost:4000)

---

## External integrations

These are third-party tools that call the Lumogis API — they are consumers of the stack, not part of it. They extend what you can do *with* Lumogis rather than what Lumogis can do internally.

### Activepieces — workflow automation

**File:** `docker-compose.activepieces.yml`  
**Why it is included:** Lumogis exposes a full REST API (browse it at [http://localhost:8000/docs](http://localhost:8000/docs)). Activepieces is an open-source workflow automation tool — similar to n8n or Zapier — that lets you build automation flows against that API without writing code. Scheduled ingestion, signal-triggered actions, outbox processing, and cross-system workflows are all natural use cases. It is bundled as an overlay because the API is the natural integration surface and Activepieces is a self-hosted option that fits the privacy model.

Activepieces does not call any internal orchestrator code and has no Python adapter. It communicates entirely via HTTP.

**Enable:**

```bash
# Generate secrets and add to .env
echo "AP_ENCRYPTION_KEY=$(openssl rand -hex 16)" >> .env
echo "AP_JWT_SECRET=$(openssl rand -hex 32)" >> .env

# Add to COMPOSE_FILE in .env
COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml:docker-compose.activepieces.yml
```

```bash
docker compose up -d activepieces
```

Activepieces UI: [http://localhost:8080](http://localhost:8080)

On first startup Postgres creates a separate `activepieces` database automatically.

---

## Adding your own overlay

Any container that should be on the same network as the orchestrator can be added as an overlay:

```yaml
# docker-compose.myservice.yml
services:
  myservice:
    image: myimage:latest
    ports:
      - "9000:9000"
    environment:
      SOME_VAR: ${SOME_VAR}
```

Add it to `COMPOSE_FILE` and run `docker compose up -d myservice`. Your container can reach the orchestrator, Qdrant, Postgres, and Ollama by their service names.

---

## Out-of-process capability services

Capability services are separate containers — your own or third-party — that expose tools over HTTP and are discovered by Core at startup. They are the right extension point when:

- Your code cannot live inside the Core process (different runtime, heavy dependencies, GPU isolation, separate licence)
- You want to ship a service with its own release cadence
- You need to run on a separate host or scale independently

In-process plugins (`plugins/<name>/`) are still the right choice for lightweight Python extensions that want to use Core's hooks, ports, and models directly. Capability services trade tight coupling for runtime independence.

### The contract

Every capability service exposes two endpoints:

- `GET /capabilities` — returns a `CapabilityManifest` JSON (schema in `orchestrator/models/capability.py`)
- `GET /health` — returns 200 when ready

The manifest declares the service's identity, version, transport, tools (with JSON schemas), and the minimum Core version it requires. A minimal example:

```json
{
  "name": "lumogis-memory-pro",
  "id": "lumogis.memory.pro",
  "version": "0.1.0",
  "type": "service",
  "transport": "http",
  "license_mode": "commercial",
  "maturity": "preview",
  "description": "Long-window memory tier with cross-session summarisation.",
  "tools": [
    {
      "name": "memory.long_search",
      "description": "Search across all archived sessions.",
      "license_mode": "commercial",
      "input_schema": { "type": "object", "properties": { "query": { "type": "string" } }, "required": ["query"] },
      "output_schema": { "type": "object", "properties": { "results": { "type": "array" } }, "required": ["results"] }
    }
  ],
  "health_endpoint": "/health",
  "capabilities_endpoint": "/capabilities",
  "permissions_required": [],
  "config_schema": { "type": "object", "properties": {} },
  "min_core_version": "0.3.0",
  "maintainer": "you@example.com"
}
```

### Registering with Core

Add the service's base URL to `.env`:

```bash
# One service
CAPABILITY_SERVICE_URLS=http://my-service:8001

# Multiple services — comma-separated, no spaces around commas required
CAPABILITY_SERVICE_URLS=http://lumogis-memory-pro:8001,http://lumogis-graph-pro:8002
```

Restart the orchestrator. Core will:

1. Fetch each service's `/capabilities` manifest at startup
2. Refuse to register the service if its `min_core_version` is greater than Core's `__version__`
3. Probe `/health` immediately, then every 60 seconds
4. Re-fetch every manifest every 5 minutes (so version bumps on the service container surface without a Core restart)

A registered service appears in `GET /` under `capability_services` and on the dashboard's main panel. Failures to reach a service are logged as warnings — Core boots cleanly even when every declared service is unreachable.

### Add the service to your stack

Capability services are just containers on the same Docker network as the orchestrator, so the standard overlay pattern from earlier in this guide applies:

```yaml
# docker-compose.my-capability.yml
services:
  my-capability:
    image: my-capability:latest
    environment:
      LUMOGIS_CORE_URL: http://orchestrator:8000
    # No port mapping needed — Core reaches it by service name on the
    # internal network. Add a port mapping only if you want to call it
    # directly from the host for debugging.
```

```bash
# .env
COMPOSE_FILE=docker-compose.yml:docker-compose.gpu.yml:docker-compose.my-capability.yml
CAPABILITY_SERVICE_URLS=http://my-capability:8000
```

```bash
docker compose up -d my-capability
```

### Calling Core back from a capability service

Capability services often need to read from Core (memory, entities, context). Use the MCP server surface at `http://orchestrator:8000/mcp/` rather than the REST API — it is the supported public contract for external consumers and exposes five read-only community tools (`memory.search`, `memory.get_recent`, `entity.lookup`, `entity.search`, `context.build`).

Configure your MCP client with:

- URL: `http://orchestrator:8000/mcp/` (note the trailing slash — the canonical, redirect-free path)
- Bearer token: set `MCP_AUTH_TOKEN` in `.env` and configure the same value on the client; leave both unset for local single-user setups

The MCP server is enabled automatically when the `mcp` Python package is installed (it is in `orchestrator/requirements.txt`). Status visible in the dashboard under **Settings → MCP server**.

### Discovering Core's manifest

Core publishes its own `CapabilityManifest` at `GET /capabilities` (no auth, never gated). External tools — Thunderbolt, future capability marketplaces, your own installer — can discover the running Core's tools and version through the same contract that out-of-process services use.

See [ADR-010 — Ecosystem plumbing](decisions/010-ecosystem-plumbing.md) for the full design rationale.

---

## Code extensions

Stack add-ons add containers. Code extensions add capability to the orchestrator itself. Drop a Python file in the right place and it is discovered automatically at startup.

| What you want to add | Where | Protocol |
|---|---|---|
| New file type extractor | `adapters/` | `extract_<extension>(path) -> str` function |
| New vector store | `adapters/` | `VectorStore` in `ports/vector_store.py` |
| New embedding model | `adapters/` | `Embedder` in `ports/embedder.py` |
| New LLM provider | `adapters/` | `LLMProvider` in `ports/llm_provider.py` |
| New signal source | `adapters/` | `SignalSource` in `ports/signal_source.py` |
| New push notifier | `adapters/` | `Notifier` in `ports/notifier.py` |
| New action handler | `actions/handlers/` | `ActionHandler` in `ports/action_handler.py` |
| New optional feature | `plugins/<name>/` | Any hooks, routes, and tools you need |

Every port is a Python `Protocol` in `orchestrator/ports/`. Read the port, implement the interface, register your adapter in `config.py`. For plugins, drop a directory into `orchestrator/plugins/` with an `__init__.py` — the loader discovers and mounts it at startup.

The same design principle runs from Docker all the way through the Python architecture: new capability is added by dropping things in, never by modifying the core.

See [CONTRIBUTING.md](../CONTRIBUTING.md) for worked examples of each type.
