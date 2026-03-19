# lumogis

**The AI comes to your data. Not the other way around.**

---

You know the moment. You are about to paste something private into ChatGPT — a draft contract, a medical question, a business plan you have been building for months — and you feel it. A half-second of hesitation. A small voice that says: *should I be doing this?*

And then you do it anyway. Because the alternative is worse.

Lumogis fixes the thing that causes the hesitation. Your files, documents, and conversations are indexed and stored entirely on your own machine. When you ask a question, Lumogis assembles the relevant context from your local index and sends a composed prompt — your question plus the pieces of your thinking that bear on it — to whichever model is best suited. Claude for deep reasoning. GPT-4 for breadth. A local model for anything you want to keep completely offline. Your archive never travels. What travels is a question.

This is not a privacy policy. It is not a setting. It is physically impossible for your files to reach a Lumogis server, because there is no Lumogis server. The core is open source. Anyone can verify it.

*Privacy is not a setting here. It is the architecture.*

---

## What it does (summary)

**lumogis processes, stores, and serves all data locally.** Every document you ingest, every entity extracted, every signal scored — it happens on your machine, in your containers, under your control.

- Ingests and indexes your documents (PDF, DOCX, text, images via OCR)
- Runs semantic search with two-stage retrieval (vector + reranker)
- Maintains session memory across conversations
- Extracts and stores entities (people, organisations, projects, concepts)
- Monitors signal sources (RSS feeds, web pages, calendars)
- Scores signals by relevance and sends a daily digest via ntfy
- Executes actions with full audit logging and Ask/Do safety enforcement
- Routes queries to any LLM — local via Ollama, or cloud via API key
- Loads plugins automatically from `plugins/` — drop one in and it activates

---

## How it works

**Private semantic search.** Documents are chunked, embedded, and stored in a local Qdrant vector database using Nomic Embed via Ollama. Search runs entirely on your machine. No outbound calls, no external embedding APIs.

**Two-stage retrieval.** Vector search narrows the candidate set. A local BGE reranker re-scores by relevance before context is assembled. The answer reflects the best match, not just the nearest neighbour.

**Session memory.** Conversation summaries are embedded and stored locally. Context from past sessions is retrieved and injected into future ones. A question you asked three months ago, and the conclusion you reached, can inform the answer you get today.

**Entity extraction.** People, organisations, projects, and concepts mentioned across conversations and documents are extracted and stored in a local knowledge base.

**Signal monitoring and digest.** RSS feeds, web pages, and calendar events are polled on a schedule. Each signal is scored for importance and relevance. A configurable daily digest sends the top signals via ntfy. Plugins extend what happens with signals beyond the built-in digest.

**Action execution.** Actions are defined, registered, and executed with a full audit trail. The Ask/Do safety model controls what runs automatically and what requires your approval.

**Model routing.** Route queries to Claude, GPT-4, a local Llama or Qwen model, or any OpenAI-compatible endpoint. Adding a provider is a config entry in `config/models.yaml` — zero code changes.

**File ingestion.** Plain text, Markdown, PDF, DOCX, and scanned images (via OCR) are supported out of the box. Drop a file in your indexed folder and it is searchable in seconds.

---

## Security model: Ask and Do

Every action in lumogis belongs to one of two modes:

| Mode | Behaviour |
|---|---|
| **Ask** | Proposed to you for approval before execution. Used for anything that writes, deletes, or sends. |
| **Do** | Executed immediately without confirmation. Used for reads and reversible, low-risk operations. |

Actions that accumulate a clean approval record are eligible for routine elevation — they move from Ask to Do automatically after a configurable threshold. You can always demote an action back to Ask. This is not a capability system. Trust is earned, recorded, and revocable.

---

## Architecture

Five concepts. Everything in the codebase maps to one of them.

```
╔══════════════════════════════════════════════════════════════════╗
║                        your machine                              ║
║                                                                  ║
║  LibreChat :3080 ──────▶  orchestrator (FastAPI)                 ║
║                               │                                  ║
║                ┌──────────────┼──────────────────┐               ║
║                │              │                  │               ║
║           services/      signals/           actions/             ║
║         ingest · search  feed · page        executor             ║
║         memory · entities calendar         registry              ║
║         tools · routines system            audit log             ║
║                │                                                  ║
║           adapters/                                               ║
║         qdrant · postgres · ollama                               ║
║         bge · pdf · docx · ocr                                   ║
║         rss · ntfy · calendar                                    ║
║                │                                                  ║
║            plugins/    (optional extensions)                     ║
║                                                                  ║
║  Qdrant     ── vector store (documents, entities, memory)        ║
║  Postgres   ── metadata + file index + audit log                 ║
║  Ollama     ── local embedder + LLM                              ║
║  FalkorDB   ── graph store (optional graph plugin)               ║
║  Redis      ── FalkorDB protocol layer (optional graph plugin)   ║
╚══════════════════════════════════════════════════════╤═══════════╝
                                                       │ composed
                                                       │ prompt only
                                                       ▼
                                          Claude · GPT-4 · local
```

| Concept | Lives in | Purpose |
|---|---|---|
| **Services** | `services/` | Business logic — ingest, search, memory, entity extraction, routines |
| **Adapters** | `adapters/` | One file per external system. Swap a backend by writing one adapter. |
| **Plugins** | `plugins/<name>/` | Optional, self-contained extensions. Core works without any. |
| **Signals** | `signals/` | Source monitors that detect and score incoming signals |
| **Actions** | `actions/` | Executable operations with audit logging and Ask/Do enforcement |

---

## Hardware requirements

`make setup` detects your hardware and selects the appropriate model tier automatically.

| Tier | GPU VRAM | RAM | Recommended for |
|---|---|---|---|
| **minimal** | No GPU / < 4 GB | 8 GB | Testing and evaluation. CPU inference only. Slow but functional. |
| **standard** | 4–8 GB | 16 GB | Daily use on mid-range hardware (GTX 1070, RTX 3060, etc.) |
| **recommended** | 8–16 GB | 32 GB | Comfortable everyday use (RTX 3080, RTX 4070, etc.) |
| **power** | 16 GB+ | 64 GB | Large context, parallel inference (RTX 4090, A100, etc.) |

Minimum to run: 8 GB RAM, 20 GB free disk. No API keys required — all models run locally via Ollama. Add `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` to `.env` to enable cloud model routing.

---

## Prerequisites

| Requirement | Linux | macOS | Windows |
|---|---|---|---|
| **Git** | usually pre-installed | usually pre-installed | [git-scm.com](https://git-scm.com) |
| **Docker Desktop** | [docs.docker.com](https://docs.docker.com/desktop/install/linux/) | [docs.docker.com](https://docs.docker.com/desktop/install/mac/) | [docs.docker.com](https://docs.docker.com/desktop/install/windows/) |
| **make** | usually pre-installed | `xcode-select --install` | via WSL2 (see below) |

**Windows:** Lumogis requires a Unix shell. Install [WSL2](https://learn.microsoft.com/en-us/windows/wsl/install) (`wsl --install` in PowerShell), then install Docker Desktop with the WSL2 backend enabled. Everything else runs inside WSL2 identically to Linux.

---

## Getting started

### Step 1: Clone

```bash
git clone https://github.com/lumogis/lumogis.git
cd lumogis
cp .env.example .env
```

### Step 2: Run setup

```bash
make setup
```

Detects your GPU, selects the right model tier, prompts for the folder to index, generates configs, starts all services, pulls models, runs a health check, and triggers the initial ingest — all in one command.

To skip the folder prompt:

```bash
make setup ROOT=/path/to/your/files
```

To add cloud model support, set API keys in `.env` before running setup:

```bash
# ANTHROPIC_API_KEY=sk-ant-...
# OPENAI_API_KEY=sk-...
```

Safe to re-run after a hardware change or to add a new model tier.

### Step 3: Start using Lumogis

[http://localhost:3080](http://localhost:3080) — create an account and start chatting.

**Optional:** explore the API at [http://localhost:8000/docs](http://localhost:8000/docs) — interactive Swagger UI for all endpoints.

---

## Configuration

All backend selection is driven by `.env`. The defaults work out of the box.

```bash
# Backend selection — swap by changing one value
VECTOR_STORE_BACKEND=qdrant       # qdrant (default), chroma, milvus
METADATA_STORE_BACKEND=postgres   # postgres (default), sqlite
EMBEDDER_BACKEND=ollama           # ollama (default), sentence-transformers
RERANKER_BACKEND=bge              # bge (default), none
EXTRACTOR_OCR_ENABLED=true        # true (default), false

# Connection details — defaults match docker-compose service names
QDRANT_URL=http://qdrant:6333
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_USER=lumogis
POSTGRES_PASSWORD=lumogis-dev
POSTGRES_DB=lumogis
OLLAMA_URL=http://ollama:11434
EMBEDDING_MODEL=nomic-embed-text
RERANKER_MODEL=BAAI/bge-reranker-base

# Graph plugin — only used if plugins/graph/ is present
FALKORDB_URL=redis://falkordb:6379

# Safety model
DEFAULT_ACTION_MODE=ask           # ask (default) or do
ROUTINE_ELEVATION_THRESHOLD=10    # clean approvals before auto-elevation
```

---

## Extending the stack

Add optional infrastructure (push notifications, automation UI, LiteLLM proxy, JS rendering) or extend the orchestrator with new adapters, signal sources, action handlers, and plugins.

See [docs/extending-the-stack.md](docs/extending-the-stack.md) for the full guide.

---

## Project structure

```
orchestrator/
  main.py              # FastAPI app, startup health checks, plugin loading
  loop.py              # Tool-calling loop (LLM ↔ tools)
  config.py            # Reads .env, returns cached adapter singletons
  hooks.py             # Event dispatch: fire() sync, fire_background() threaded
  events.py            # Event name constants (Event class)
  auth.py              # Authentication
  permissions.py       # Permission enforcement

  services/            # Business logic (five concepts: services)
    ingest.py          # Document ingest pipeline
    search.py          # Semantic search + reranking
    memory.py          # Session memory
    entities.py        # Entity extraction and resolution
    tools.py           # Tool definitions and dispatcher
    signal_processor.py
    routines.py
    feedback.py

  adapters/            # One file per external system (five concepts: adapters)
    anthropic_llm.py   # Claude (Anthropic SDK)
    openai_llm.py      # OpenAI-compatible (Ollama, ChatGPT, Perplexity, …)
    qdrant_store.py
    postgres_store.py
    ollama_embedder.py
    bge_reranker.py
    text_extractor.py  # Auto-discovered by file extension
    pdf_extractor.py
    docx_extractor.py
    ocr_extractor.py
    rss_source.py
    ntfy_notifier.py

  signals/             # Source monitors (five concepts: signals)
    feed_monitor.py    # RSS and Atom feeds
    page_monitor.py    # Web page change detection
    calendar_monitor.py
    system_monitor.py
    digest.py          # Periodic top-signals digest via notifier

  actions/             # Executable operations (five concepts: actions)
    registry.py        # Action registration
    executor.py        # Ask/Do enforcement + execution
    audit.py           # Immutable audit log
    reversibility.py   # Reversibility metadata
    handlers/          # One file per action domain

  plugins/             # Optional extensions — drop a package here and it loads automatically
    # Start from the template in docs/examples/example_plugin/

  models/              # Pydantic request/response models
  routes/              # FastAPI routers (chat, data, signals, actions, admin)
  ports/               # Protocol interfaces (internal — rarely touched)
  tests/               # Unit tests (no Docker needed)

config/
  models.yaml          # Model registry — adapters, capabilities, endpoints

postgres/
  init.sql             # Schema: file_index, entities, entity_relations, review_queue

scripts/
  setup.sh             # Hardware detection + model pulls
  detect-hardware.sh   # Standalone hardware detection (used by setup.sh)

docs/
  decisions/           # Architecture Decision Records (ADRs)
  examples/            # Example plugin template
  graph-schema.md      # FalkorDB schema reference

tests/
  integration/         # Full-stack integration tests (requires Docker stack)
```

---

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full guide.

The short version: find the right layer, follow the existing pattern.

- **New file type extractor:** one new file in `adapters/`, auto-discovered
- **New signal source:** implement `SignalSource` from `ports/signal_source.py`
- **New action handler:** one new file in `actions/handlers/`
- **New vector store:** implement `VectorStore` from `ports/vector_store.py`
- **New plugin:** one directory in `plugins/`, any hooks and routes you need

All PRs must pass `make lint` and `make test`. Include tests for new functionality.

**The one rule:** services never import concrete adapters. Always go through `config.get_*()`.

---

## Community plugins

See [COMMUNITY-PLUGINS.md](COMMUNITY-PLUGINS.md) for community-contributed adapters and plugins.

---

## Security

To report a vulnerability, see [SECURITY.md](SECURITY.md). Do not open a public issue.

The initial security audit (SQL injection, path traversal, MCP boundary, Ask/Do enforcement) is documented in [`docs/SECURITY-AUDIT-001.md`](docs/SECURITY-AUDIT-001.md).

---

## Code of conduct

This project follows the [Contributor Covenant v2.1](CODE_OF_CONDUCT.md).

---

## License

[AGPL-3.0](LICENSE). The core is open source and always will be.

---

*Private, local, yours. The AI comes to your data. Not the other way around.*
