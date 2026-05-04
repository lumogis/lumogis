![Lumogis](branding/readme-banner.svg)

**[Quickstart](#getting-started)** · **[Architecture](#architecture)** · **[Reference manual](docs/LUMOGIS_REFERENCE_MANUAL.md)** · **[Operator runbook](docs/connect-and-verify.md)** · **[Extending Lumogis](docs/extending-the-stack.md)** · **[Community Plugins](COMMUNITY-PLUGINS.md)** · **[Security](SECURITY.md)**

# lumogis

**The AI comes to your data. Not the other way around.**

Lumogis is a **self-hosted, local-first, privacy-first** household and personal AI platform that you run yourself with Docker Compose under **AGPL-3.0-only**. Your primary UI is **[Lumogis Web](#lumogis-web)** (same origin behind **[Caddy](docker/caddy/Caddyfile)**). **Core** is the **[FastAPI](orchestrator/main.py)** orchestrator. **[LibreChat](config/librechat.coldstart.yaml)** stays available behind an optional Compose profile (`librechat`) for OpenAI-compatible chat—not the main product surface.

---

![Lumogis demo](branding/demo2.gif)

*Ask about a decision captured in notes or an earlier conversation. Retrieval and storage run locally—you are not handing the archive to a SaaS indexer.*

---

## Why Lumogis

You want to ask an LLM questions **grounded in your documents and sessions**, without exporting your corpus to someone else’s cloud.

- **Indexing and retrieval stay on your machine.** Default Compose brings up **Qdrant** for vectors, **Postgres** for metadata (fresh volumes bootstrap from `postgres/init.sql`), and **Ollama** for local embeddings/models—see **`docker-compose.yml`**.

- **When you choose a cloud model**, the provider receives a **composed prompt**: your query plus excerpts Core selected from local retrieval—not your full corpus or embeddings. With **purely local inference**, the **LLM call stays on your host** (your usual outbound traffic, logging, and supply-chain realities still apply).

The source code is **[AGPL-3.0-only](LICENSE)**. There is no Lumogis-operated SaaS substrate in this story—verification is cloning and reading code.

---

## What it does (summary)

**All processing defaults to containers on your machine.** Ingest → chunk → embed → search → sessions → signals → audited actions—all under your Compose project.

| Area | Capability |
|---|---|
| Documents | PDF, DOCX, text, images (OCR when enabled)—see ingestion in [`orchestrator/services/ingest.py`](orchestrator/services/ingest.py) |
| Search | Dense vectors + optional hybrid / reranking—[`services/search.py`](orchestrator/services/search.py), [`ARCHITECTURE.md`](ARCHITECTURE.md) |
| Memory | Sessions and summaries embedded locally |
| Signals | RSS, pages, calendars, digest—[`signals/`](orchestrator/signals/) |
| Actions | **[Ask / Do](#security-model-ask-and-do)** with audit logging—[`actions/`](orchestrator/actions/) |
| Models | Local via Ollama; cloud via adapters and `config/models.yaml` |
| Plugins | **[Optional packages](docs/examples/example_plugin/)** under [`orchestrator/plugins/`](orchestrator/plugins/)—loaded at startup |

---

## Security model: Ask and Do

Every action lands in **Ask** or **Do**:

| Mode | Behaviour |
|---|---|
| **Ask** | Proposed for approval before anything writes, deletes, or sends externally. |
| **Do** | Executes immediately within a scoped, reversible, low-risk contract. |

Details and examples: **[`docs/LUMOGIS_REFERENCE_MANUAL.md`](docs/LUMOGIS_REFERENCE_MANUAL.md)** (operator narrative) and audit discussion in **[`docs/SECURITY-AUDIT-001.md`](docs/SECURITY-AUDIT-001.md)**.

---

## Architecture

**Five concepts**—every module maps to one: **actions**, **signals**, **services**, **plugins**, and **adapters**. Full layering (routes → services → ports ← adapters; plugins via hooks): **[`ARCHITECTURE.md`](ARCHITECTURE.md)**.

![Lumogis system architecture: browser and optional LibreChat through Caddy to Core and Lumogis Web; domain core, plugins, ports plus adapters, and backing services on the host; optional lumogis-graph and LLM providers.](branding/lumogis_architecture.svg)

† **Graph store:** FalkorDB is optional. Merge **`docker-compose.falkordb.yml`** for the in-process graph plugin and Falkor-backed paths; Falkor speaks the **Redis wire protocol** (no separate Redis container in that overlay)—see **`docker-compose.falkordb.yml`**. **`lumogis-graph`** (out-of-process KG capability) merges **`docker-compose.premium.yml`**—the **`premium` filename is historical**, not proprietary scope; see **`services/lumogis-graph/README.md`** and **`docs/kg_reference.md`** (`GRAPH_MODE=inprocess|service`).

| Concept | Path | Purpose |
|---|---|---|
| Services | [`orchestrator/services/`](orchestrator/services/) | ingest, search, memory, entities, tools, routines |
| Adapters | [`orchestrator/adapters/`](orchestrator/adapters/) | Concrete backends implementing **ports** (one swap = one adapter + factory branch) |
| Plugins | [`orchestrator/plugins/`](orchestrator/plugins/) | Optional extensions—Core runs without them |
| Signals | [`orchestrator/signals/`](orchestrator/signals/) | Monitors and scoring |
| Actions | [`orchestrator/actions/`](orchestrator/actions/) | Registry, executor, audit |

**Reference:** [`docs/LUMOGIS_REFERENCE_MANUAL.md`](docs/LUMOGIS_REFERENCE_MANUAL.md) · Automated testing overview: [`docs/testing/automated-test-strategy.md`](docs/testing/automated-test-strategy.md).

---

## Lumogis Web

First-party SPA: **[`clients/lumogis-web/`](clients/lumogis-web/)**, served behind Caddy (**[`docker-compose.yml`](docker-compose.yml)** `lumogis-web` + `caddy`). Same-origin preserves strict cookie + CSRF assumptions.

| Where | URL (defaults) |
|---|---|
| **Recommended** | **http://localhost/** — SPA; `/api/*`, `/events`, `/v1/*`, `/mcp/*`, `/health`, and legacy orchestrator HTML routes proxied per **[`docker/caddy/Caddyfile`](docker/caddy/Caddyfile)** |
| **Core directly** | **http://localhost:8000** — Swagger at `/docs` |
| **LibreChat** (`COMPOSE_PROFILES` contains `librechat`) | **http://localhost:3080** — targets **`http://orchestrator:8000/v1`** (**[`config/librechat.coldstart.yaml`](config/librechat.coldstart.yaml)**)

**Operators:** pin **`LUMOGIS_PUBLIC_ORIGIN`** (see **`.env.example`**) when `AUTH_ENABLED=true`; set **`LUMOGIS_TRUSTED_PROXIES`** whenever a trusted reverse proxy terminates TLS (**[`docker-compose.yml`](docker-compose.yml)** passes them through). Playwright/Lighthouse/header checks live in **`clients/lumogis-web/README.md`** and **`Makefile`** targets `web-e2e*`, `web-caddy-headers*`.

---

## Getting started

**Linux / macOS**

```bash
git clone https://github.com/lumogis/lumogis.git ~/lumogis
cd ~/lumogis && cp .env.example .env && docker compose up -d
```

**Windows (PowerShell)**

```powershell
git clone https://github.com/lumogis/lumogis.git $HOME\lumogis
cd "$HOME\lumogis"; Copy-Item .env.example .env; docker compose up -d
```

Open **http://localhost/** after health checks settle. Inspect **`.env.example`** for `COMPOSE_PROFILES`, model pulls (`OLLAMA_EXTRA_MODELS`), and auth knobs.

---

## Prerequisites · hardware hints

**Prerequisites:** Git + Docker Desktop (see **`.env.example`** for platform notes). End users do **not** need Python or Make.

**Rough sizing** — RAM/VRAM rises quickly with bigger local models or optional **`RERANKER_BACKEND=bge`**; see **`docs/gpu-setup.md`** and the capacity discussion in **`docs/LUMOGIS_REFERENCE_MANUAL.md`**.

---

## Composition: required vs optional

**Base `docker compose up -d`** (from **`docker-compose.yml`**) pulls up **Orchestrator + Qdrant + Postgres + Ollama + Lumogis Web + Caddy + stack-control** (internal restart helper)—see service list in **`docker-compose.yml`**.

| Add-on | How | Notes |
|---|---|---|
| FalkorDB (graph backends) | `docker-compose.yml` + [`docker-compose.falkordb.yml`](docker-compose.falkordb.yml) | In-process **`plugins/graph`** and adapters use Redis-protocol Falkor—no separate Redis service in this overlay |
| `lumogis-graph` service | … + **`docker-compose.premium.yml`** + `GRAPH_MODE=service` | Historical filename—**[`services/lumogis-graph/README.md`](services/lumogis-graph/README.md)** |
| LiteLLM | **`docker-compose.litellm.yml`** | Unified proxy overlay |
| Activepieces | **`docker-compose.activepieces.yml`** | Automation UI |
| GPU | **`docker-compose.gpu.yml`** | NVIDIA Container Toolkit (**[`docs/gpu-setup.md`](docs/gpu-setup.md)**) |
| Speech-to-text sidecar | **`docker-compose.stt.yml`** | Speaches-backed **`POST /api/v1/voice/transcribe`**—**[`docs/architecture/lumogis-speech-to-text-foundation-plan.md`](docs/architecture/lumogis-speech-to-text-foundation-plan.md)** |
| LibreChat | `COMPOSE_PROFILES=librechat` (often default in **`.env.example`** for continuity) | **[`docker-compose.yml`](docker-compose.yml)** profile comments |

Merge overlays with **`COMPOSE_FILE`** in `.env` (patterns in **`.env.example`**).

---

## Configuration pointers

Operational truth lives in **`.env.example`** (committed) and **`orchestrator/config.py`** factories. Typical defaults bind **Postgres**, **Qdrant**, **Ollama**, optional **BGE reranker**, and optional **graph** backends. **Do not assume every configuration snippet reflects code that exists in-tree** — today **`get_vector_store`**, **`get_metadata_store`**, and **`get_embedder`** only instantiate the backends implemented in **`orchestrator/config.py`** (`qdrant` / `postgres` / `ollama` unless you extend the factories).

---

## Extending Lumogis

- **Compose / capability manifests / MCP bridging:** **`docs/extending-the-stack.md`**
- **ADR for ecosystem plumbing:** **`docs/decisions/010-ecosystem-plumbing.md`**
- **Operator verification steps:** **`docs/connect-and-verify.md`**
- **Optional local STT (Speaches overlay, troubleshooting, CUDA notes):** **`docs/architecture/lumogis-speech-to-text-foundation-plan.md`**

---

## Contributing

See **[CONTRIBUTING.md](CONTRIBUTING.md)** — code boundaries (“services never import concrete adapters”), `make lint` / `make test`, and Docker-based wrappers in **`Makefile`**.

---

## FAQ

**Cloud models mandatory?** No—omit API keys and run locally via **Ollama**.

**Where is data?** Host volumes mapped in Compose (**`docker-compose.yml`**) plus your indexed folder (`FILESYSTEM_ROOT`).

**Production-ready?** Solid self-hosted/developer preview—not a turnkey consumer appliance; run it, tighten auth, observe logs.

More depth: **`docs/troubleshooting.md`**, **`docs/LUMOGIS_REFERENCE_MANUAL.md`**.

---

## Community plugins · Security · Licence

- **Community adapters/plugins:** **`COMMUNITY-PLUGINS.md`**
- **Report vulnerabilities:** **`SECURITY.md`** (no public tickets for undisclosed bugs)
- **Backups / portability:** households use **`POST /api/v1/me/export`** and related admin import flows — manifest and refusal semantics in **`docs/per-user-export-format.md`**, curl walkthrough steps in **`docs/connect-and-verify.md`** (**`GET /api/v1/admin/export`** is **`410 Gone`** by design).
- **Public AGPL export / hygiene tooling** (`scripts/create-upstream-export-tree.sh`, `scripts/check-public-export.sh`): **`docs/maintainers.md`**.

Lumogis is **`AGPL-3.0-only`** — **`LICENSE`** and SPDX headers (`AGPL-3.0-only`).

---

This project follows the **[Contributor Covenant v2.1](CODE_OF_CONDUCT.md)**.

*Private, local, yours. The AI comes to your data. Not the other way around.*
