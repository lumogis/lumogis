# Integration tests

These tests call the live HTTP API (`http://127.0.0.1:8000` by default).

## Prerequisites

1. `docker compose up -d` (orchestrator + qdrant + postgres + ollama + embedder working).
2. From repo root: `pip install -r orchestrator/requirements.txt` (or your venv) so `pytest` and `httpx` are available — `make test-integration` runs pytest from `orchestrator/`.
3. For **session memory** and **ingest/search**: Ollama must have embedding + chat models pulled (`make setup`).

## Run

```bash
make test-integration
```

Slow tests (65s RSS poll wait) are skipped. To run everything:

```bash
make test-integration-full
```

Override base URL:

```bash
LUMOGIS_API_URL=http://host.docker.internal:8000 make test-integration
```

CI runs **unit tests only**; integration is manual pre-release (see CONTRIBUTING.md).
