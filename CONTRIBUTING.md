# Contributing to lumogis

Thank you for your interest in contributing. This document covers everything you need to get started.

For architecture internals, read [ARCHITECTURE.md](ARCHITECTURE.md) first.

All participants must follow the [Code of Conduct](CODE_OF_CONDUCT.md).

---

## Contributor Licence Agreement (CLA)

**All contributors must sign the CLA before a PR can be merged.**

lumogis is AGPL-3.0. The CLA grants Lumogis the right to relicence your contribution for distribution outside AGPL terms while you retain full copyright.

**Sign the CLA here:** [cla-assistant.io/lumogis/lumogis](https://cla-assistant.io/lumogis/lumogis)

The CLA Assistant bot will comment on your PR if your CLA is not yet signed. You only sign once — all future PRs are covered.

What the CLA says:
- You retain full copyright over your contribution
- You grant Lumogis a perpetual, irrevocable licence to use, modify, and relicence your contribution
- You grant a patent licence covering your contribution
- You confirm you have the right to submit the work
- Contributions are provided as-is, with no warranty

Read the full CLA: [gist.github.com/Thoko14/433708c1599f7e1068b7de6af7c7cf6f](https://gist.github.com/Thoko14/433708c1599f7e1068b7de6af7c7cf6f)

If you have questions about the CLA, open a Discussion before submitting code.

---

## Development setup

### Prerequisites

- Docker and Docker Compose
- Python 3.12 (for running tests and linting locally)
- `make`

### First-time setup

```bash
git clone https://github.com/lumogis/lumogis.git
cd lumogis
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
cp .env.example .env
make setup      # detects hardware, pulls models
make dev        # starts the stack with hot reload
```

`make dev` uses `docker-compose.dev.yml` which mounts the orchestrator source and reloads on file changes. You do not need to rebuild the Docker image during development.

### Python dependencies for tests and lint

Production images install only each service’s `requirements.txt`. For **local** pytest and ruff, install the dev extras (not used in Docker builds):

| Component | Runtime | Dev/test (local + CI) |
|-----------|---------|------------------------|
| **Orchestrator** | `pip install -r orchestrator/requirements.txt` | `pip install -r orchestrator/requirements-dev.txt` |
| **stack-control** | `pip install -r stack-control/requirements.txt` | `pip install -r stack-control/requirements-dev.txt` |

For a full local dev venv that runs `make test` and `make lint`:

```bash
pip install -r orchestrator/requirements.txt
pip install -r orchestrator/requirements-dev.txt
pip install -r stack-control/requirements-dev.txt
```

Orchestrator unit tests alone only need `orchestrator/requirements.txt` + `orchestrator/requirements-dev.txt`. stack-control unit tests live in `stack-control/test_main.py` and need `stack-control/requirements-dev.txt`.

### Running tests

```bash
make test       # orchestrator + stack-control unit tests — no Docker needed
make lint       # ruff check + format check
```

Unit tests use mock adapters (`orchestrator/tests/conftest.py`) — no running services required.

```bash
make test-integration   # full-stack tests — requires docker compose up -d
```

Integration tests run against the live stack. See [Integration tests](#integration-tests) below.

---

## How to write a new extractor

Extractors are auto-discovered by file extension. Add one file to `adapters/`:

```python
# orchestrator/adapters/epub_extractor.py

def extract_epub(path: str) -> str:
    """Extract plain text from an EPUB file."""
    import ebooklib
    from ebooklib import epub
    from bs4 import BeautifulSoup

    book = epub.read_epub(path)
    chapters = []
    for item in book.get_items_of_type(ebooklib.ITEM_DOCUMENT):
        soup = BeautifulSoup(item.get_content(), "html.parser")
        chapters.append(soup.get_text())
    return "\n\n".join(chapters)
```

That is the entire change. The function name `extract_<extension>` is the registration mechanism. No factory branches, no Protocol, no config changes. The ingest pipeline picks it up automatically.

Add any new dependencies to `orchestrator/requirements.txt`.

**Reference extractor:** `orchestrator/adapters/pdf_extractor.py`

---

## How to write a new adapter

Adapters implement a port (Protocol interface) from `ports/`. Here is a complete example replacing Qdrant with Chroma as the vector store.

**Step 1: Implement the Protocol**

```python
# orchestrator/adapters/chroma_store.py

import chromadb
from ports.vector_store import VectorStore, SearchResult


class ChromaStore(VectorStore):
    def __init__(self, path: str) -> None:
        self._client = chromadb.PersistentClient(path=path)

    def upsert(self, collection: str, id: str, vector: list[float], payload: dict) -> None:
        col = self._client.get_or_create_collection(collection)
        col.upsert(ids=[id], embeddings=[vector], metadatas=[payload])

    def search(
        self,
        collection: str,
        vector: list[float],
        limit: int = 10,
        sparse_query: str | None = None,   # required by Protocol; Chroma ignores it
    ) -> list[SearchResult]:
        col = self._client.get_or_create_collection(collection)
        results = col.query(query_embeddings=[vector], n_results=limit)
        return [
            SearchResult(id=id_, score=1 - dist, payload=meta)
            for id_, dist, meta in zip(
                results["ids"][0],
                results["distances"][0],
                results["metadatas"][0],
            )
        ]

    def delete(self, collection: str, id: str) -> None:
        col = self._client.get_or_create_collection(collection)
        col.delete(ids=[id])

    def count(self, collection: str) -> int:
        return self._client.get_or_create_collection(collection).count()

    def ping(self) -> bool:
        try:
            self._client.heartbeat()
            return True
        except Exception:
            return False
```

**Step 2: Add a factory branch in `config.py`**

```python
def get_vector_store() -> VectorStore:
    if "vector_store" not in _cache:
        backend = os.getenv("VECTOR_STORE_BACKEND", "qdrant")
        if backend == "qdrant":
            _cache["vector_store"] = QdrantStore(url=os.getenv("QDRANT_URL"))
        elif backend == "chroma":                              # ← add this
            _cache["vector_store"] = ChromaStore(
                path=os.getenv("CHROMA_PATH", "/data/chroma")
            )
        else:
            raise ValueError(f"Unknown VECTOR_STORE_BACKEND: {backend}")
    return _cache["vector_store"]
```

**Step 3: Update `.env.example`**

```bash
# VECTOR_STORE_BACKEND=chroma
# CHROMA_PATH=/data/chroma
```

**Reference adapter:** `orchestrator/adapters/qdrant_store.py`

---

## How to write a new plugin

Plugins are directories in `orchestrator/plugins/` with an `__init__.py`. They are auto-loaded at startup.

```python
# orchestrator/plugins/my_plugin/__init__.py

from events import Event
from hooks import register, fire
from models.tool_spec import ToolSpec
from fastapi import APIRouter

router = APIRouter(prefix="/my-plugin")


def _on_document_ingested(doc_id: str, path: str, chunks: int) -> None:
    # Called after every document is ingested
    print(f"[my-plugin] ingested {path} → {chunks} chunks")


register(Event.DOCUMENT_INGESTED, _on_document_ingested)


@router.get("/status")
def status():
    return {"plugin": "my_plugin", "status": "ok"}
```

The plugin loader checks for a `router` attribute. If present, it is registered with `app.include_router()`.

**Reference plugin:** `docs/examples/example_plugin/` — a minimal working plugin with routes, hooks, and a README.

**Plugin rules:**
- Import from `ports/`, `models/`, `events.py`, `hooks.py` only
- Never import from `services/` or `adapters/`
- Never call `config.get_*()` — request adapters via hook injection or route dependencies

---

## How to write a new signal source

Implement the `SignalSource` protocol and register it:

```python
# orchestrator/adapters/hackernews_source.py

import httpx
from ports.signal_source import SignalSource, Signal


class HackerNewsSource(SignalSource):
    source_id = "hackernews-top"

    def poll(self) -> list[Signal]:
        resp = httpx.get("https://hacker-news.firebaseio.com/v0/topstories.json")
        ids = resp.json()[:10]
        signals = []
        for story_id in ids:
            item = httpx.get(
                f"https://hacker-news.firebaseio.com/v0/item/{story_id}.json"
            ).json()
            signals.append(Signal(
                source_id=self.source_id,
                title=item.get("title", ""),
                url=item.get("url", ""),
                content=item.get("text", ""),
                score=item.get("score", 0),
            ))
        return signals
```

Add a factory branch in `config.get_signal_sources()`. The signal processor polls all registered sources on a schedule.

---

## How to contribute an MCP connector

MCP connectors extend the tool-calling loop with new tool categories. An MCP connector is a plugin that:

1. Registers tools via `hooks.fire(Event.TOOL_REGISTERED, ToolSpec(...))`
2. Handles tool calls in a `TOOL_REGISTERED` listener
3. Enforces Ask/Do mode via `ToolSpec.mode`

Each `ToolSpec` must include:
- `name: str` — unique tool name (snake_case)
- `description: str` — description shown to the LLM
- `input_schema: dict` — JSON Schema for tool inputs
- `mode: Literal["ask", "do"]` — safety mode

The `run_tool()` dispatcher in `services/tools.py` enforces the mode before calling the handler.

---

## Integration tests

Integration tests live in `tests/integration/` and run against the live Docker stack. They use `httpx` and `pytest`.

```bash
docker compose up -d
make test-integration
```

Use `make test-integration-full` to include slow cases (e.g. waiting for RSS poll).

Tests cover the full pipeline: ingest → search → entity extraction → session memory → signal source → routine run → audit log → feedback → export.

**Important:** CI runs unit tests only. Integration tests are run manually before each release. If you are adding a new service or adapter, add an integration test that exercises the full path.

---

## Submitting a community plugin

To add your plugin to [COMMUNITY-PLUGINS.md](COMMUNITY-PLUGINS.md):

1. Publish your plugin to a public GitHub repository
2. Ensure it has a README explaining installation and usage
3. Open a PR to lumogis that adds one entry to `COMMUNITY-PLUGINS.md` in the appropriate section
4. The entry format:

```markdown
| [Plugin Name](https://github.com/you/your-plugin) | One-sentence description | @yourhandle |
```

No code changes to lumogis are required. The PR modifies only `COMMUNITY-PLUGINS.md`.

---

## Governance

Maintainers review PRs. We aim for a first response within **48 hours**.

**Pushing to the public GitHub repo:** see [docs/maintainers.md](docs/maintainers.md) so only paths meant for the public tree are published.

**PRs must:**
- Pass `make lint` (ruff check + format)
- Pass `make test` (unit tests)
- Include tests for new functionality
- Sign the CLA

**Integration tests** are run manually before each release — not required per-PR, but your PR description should explain how you tested it against the live stack.

**For design decisions:** open a Discussion first — not every idea needs a PR. If you are proposing a new port, changing a Protocol signature, or adding a dependency, start a Discussion so the approach can be agreed before you write code. This saves everyone time.

**For bug reports:** open an Issue with reproduction steps, your OS, and Docker version.

**For security issues:** do not open a public Issue. Email [lumogis@pm.me](mailto:lumogis@pm.me).

---

## Code of conduct

This project follows the [Contributor Covenant v2.1](CODE_OF_CONDUCT.md). Be kind.
