# lumogis

**The AI comes to your data. Not the other way around.**

---

You know the moment. You are about to paste something private into ChatGPT — a draft contract, a medical question, a business plan you have been building for months — and you feel it. A half-second of hesitation. A small voice that says: *should I be doing this?*

And then you do it anyway. Because the alternative is worse.

Lumogis fixes the thing that causes the hesitation. Your files, documents, and conversations are indexed and stored entirely on your own machine. When you ask a question, Lumogis assembles the relevant context from your local index and sends a composed prompt — your question plus the pieces of your thinking that bear on it — to whichever model is best suited. Claude for deep reasoning. GPT-4 for breadth. A local model for anything you want to keep completely offline. Your archive never travels. What travels is a question.

This is not a privacy policy. It is not a setting. It is physically impossible for your files to reach a Lumogis server, because there is no Lumogis server. The core is open source. Anyone can verify it.

---

## What it does

**Private semantic search.** Your documents are chunked, embedded, and stored in a local Qdrant vector database using Nomic Embed via Ollama. Search runs entirely on your machine. No outbound calls, no external embedding APIs.

**Context enrichment.** Before your message reaches any model, Lumogis retrieves the relevant context from your local index and enriches your prompt automatically and invisibly. The model receives not just your question — but the accumulated intelligence of everything you have indexed that bears on it. Enterprise RAG, running on your hardware, for personal use.

**Two-stage retrieval.** Vector search narrows the candidate set. A local BGE reranker re-scores by relevance before context is assembled. The answer you get reflects the best match, not just the nearest neighbour.

**Session memory.** Conversation summaries are embedded and stored locally. Context from past sessions is retrieved and injected into future ones. A question you asked three months ago, and the conclusion you reached, can inform the answer you get today — without you having to remember to include it.

**Entity extraction.** People, organisations, projects, and concepts mentioned across your conversations and documents are extracted and stored in a local knowledge base. Ask what Lumogis knows about a person or topic and it draws from every session and document where they appeared.

**Model routing.** Route queries to Claude, GPT-4, a local Llama model, or any LiteLLM-compatible endpoint. You choose what travels and to which brain. Sensitive queries can stay entirely local.

**File ingestion.** Plain text, Markdown, PDF, DOCX, and scanned images (via OCR) are all supported out of the box. Drop a file in your indexed folder and it is searchable in seconds.

**LibreChat frontend.** A full, polished chat interface included and pre-connected. No configuration needed.

---

## Architecture

```
                         ┌─────────────────────────────────┐
                         │           your machine           │
                         │                                  │
  LibreChat :3080 ───────┤  orchestrator (FastAPI :8000)    │
                         │    │                             │
                         │    ├── services/                 │
                         │    │     ingest · search         │
                         │    │     memory · entities       │
                         │    │                             │
                         │    ├── adapters/                 │
                         │    │     qdrant · postgres       │
                         │    │     ollama · bge · ocr      │
                         │    │     pdf · docx · text       │
                         │    │                             │
                         │    └── plugins/ (optional)       │
                         │                                  │
                         │  Qdrant    ── vector store       │
                         │  Postgres  ── metadata + index   │
                         │  Ollama    ── embedder + LLM     │
                         │  FalkorDB  ── graph store        │
                         └──────────────┬──────────────────┘
                                        │ composed prompt only
                                        ▼
                             Claude · GPT-4 · local model
```

Three contributor-facing layers:

| Layer | What lives here |
|---|---|
| `services/` | Business logic — ingest, search, memory, entity extraction. Where behaviour lives. |
| `adapters/` | One file per external system. Swap a backend = write one adapter + change one `.env` value. |
| `plugins/` | Optional extensions. Core works without them. |

Internal `Protocol` interfaces in `ports/` keep services decoupled from specific adapters. `config.py` wires everything together from `.env`. Services never import concrete adapters directly.

---

## Stack

| Component | Role |
|---|---|
| `python:3.12-slim` | Orchestrator — FastAPI, tool loop, all services |
| `qdrant/qdrant` | Vector store for documents, conversations, entities |
| `postgres:16` | Metadata store — file index, entities, relations, review queue |
| `ollama/ollama` | Local embedding (Nomic) and LLM (Llama 3.2) |
| `ghcr.io/berriai/litellm` | Model routing proxy — Claude, GPT-4, local, any OpenAI-compatible endpoint |
| `falkordb/falkordb` | Graph store for the graph plugin |
| `ghcr.io/danny-avila/librechat` | Chat UI |
| `mongo:7` | LibreChat persistence |

---

## Getting started

### Prerequisites

- Docker and Docker Compose
- An NVIDIA GPU (recommended for Ollama; CPU works but is slow)
- An Anthropic or OpenAI API key if you want cloud model routing

### 1. Clone and configure

```bash
git clone https://github.com/Thoko14/lumogis.git
cd lumogis
cp .env.example .env
```

Open `.env` and set at minimum:

```bash
FILESYSTEM_ROOT=/path/to/your/files   # indexed read-only
JWT_SECRET=something-long-and-random
JWT_REFRESH_SECRET=something-else-long-and-random

# Optional — for cloud model routing
ANTHROPIC_API_KEY=sk-ant-...
```

### 2. Start

```bash
docker compose up -d
```

### 3. Pull the embedding model

```bash
docker compose exec ollama ollama pull nomic-embed-text
```

### 4. Open LibreChat

[http://localhost:3080](http://localhost:3080) — create an account and start.

### 5. Index your files

```bash
curl -s -X POST http://localhost:8000/ingest \
  -H "Content-Type: application/json" \
  -d '{"path": "/data"}'
```

Your files are now searchable. Every conversation you have from this point enriches the local index.

---

## Configuration

All backend selection is driven by `.env`. The defaults work out of the box.

```bash
# Backend selection — swap by changing one value
VECTOR_STORE_BACKEND=qdrant       # alternatives: chroma, milvus, weaviate
METADATA_STORE_BACKEND=postgres   # alternative: sqlite (for dev/testing)
EMBEDDER_BACKEND=ollama           # alternative: sentence-transformers
RERANKER_BACKEND=bge              # set to "none" to disable reranking
EXTRACTOR_OCR_ENABLED=true        # set to "false" to skip OCR

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
LITELLM_URL=http://litellm:4000

# Graph plugin — ignored if plugins/graph/ is absent
FALKORDB_URL=redis://falkordb:6379
```

---

## Project structure

```
orchestrator/
  main.py              # FastAPI app, endpoints, startup health checks
  loop.py              # Tool-calling loop
  config.py            # Reads .env, returns cached adapter singletons
  hooks.py             # Event system: fire() sync and fire_background() threaded

  ports/               # Internal Protocol interfaces — rarely touched by contributors
    vector_store.py    # upsert, search, delete, count, ping
    metadata_store.py  # execute, fetch_one, fetch_all, ping
    embedder.py        # embed, embed_batch, vector_size, ping
    reranker.py        # rerank(query, candidates, limit)
    graph_store.py     # create_node, create_edge, query, ping

  adapters/            # One file per external system
    qdrant_store.py
    postgres_store.py
    ollama_embedder.py
    bge_reranker.py
    text_extractor.py
    pdf_extractor.py
    docx_extractor.py
    ocr_extractor.py

  clients/
    litellm.py         # Thin LiteLLM wrapper used by loop.py

  services/            # Business logic
    ingest.py          # Document ingest pipeline
    search.py          # Semantic search + reranking
    memory.py          # Session memory
    entities.py        # Entity extraction and resolution
    tools.py           # Tool definitions and dispatcher

  plugins/             # Optional extensions — core works without them
    graph/             # Graph intelligence — NOT in lumogis-core

postgres/
  init.sql             # Schema: file_index, entities, entity_relations, review_queue

config/
  litellm.yaml         # Model routing
  librechat.yaml       # LibreChat config
```

---

## What is and isn't in this repo

`lumogis` (this repo) contains everything needed for private local RAG, semantic search, session memory, and entity extraction. The graph intelligence plugin (`plugins/graph/`) adds knowledge graph construction and traversal on top — it is proprietary and not included here.

Everything in this repo is open source under AGPL-3.0.

---

## Contributing

The architecture makes contribution straightforward. Find the right layer and follow the pattern already there.

**Add a new file type:**
Write an `extract_xyz(path: str) -> str` function in `adapters/`, add one entry to the registry in `config.get_extractors()`. No Protocol, no port, no factory method needed.

**Add a new vector store:**
Implement the `VectorStore` Protocol from `ports/vector_store.py`, add a factory branch in `config.get_vector_store()`, add the backend name to the docs.

**Add a new embedder or reranker:**
Same pattern — implement the Protocol, add a factory branch, update `.env.example`.

**Fix a bug or improve a service:**
Open an issue for anything non-trivial before writing code. Services live in `services/` and call into `config.get_*()` — never into concrete adapters directly.

**The one rule:** services never import concrete adapters. Always go through `config.get_*()`. This is what makes backend swaps possible without touching business logic.

---

## License

AGPL-3.0. See `LICENSE`.

---

*Private, local, yours. The AI comes to your data. Not the other way around.*
