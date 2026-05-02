# Integration tests

These tests call the live HTTP API (`http://127.0.0.1:8000` by default).

## Prerequisites

1. `docker compose up -d` (orchestrator + qdrant + postgres + ollama + embedder working).
2. From repo root: `pip install -r orchestrator/requirements.txt` and `pip install -r orchestrator/requirements-dev.txt` (or your venv) so `pytest` and `httpx` are available — `make test-integration` runs pytest from `orchestrator/`.
3. For **session memory** and **ingest/search**: Ollama must have embedding + chat models available. A normal `docker compose up -d` pulls defaults on first boot (see repo `README`); wait until the Ollama service is healthy.
4. For **graph tests**: FalkorDB must be in the stack. Set `COMPOSE_FILE=docker-compose.yml:docker-compose.falkordb.yml` and `GRAPH_BACKEND=falkordb` / `FALKORDB_URL=redis://falkordb:6379` in `.env`.

## Test files

| File | Requires FalkorDB | Description |
|---|---|---|
| `test_integration_flow.py` | No | Core pipeline: health, ingest, search, entities, sessions, signals |
| `test_graph.py` | Yes (auto-skips) | Graph pipeline: ingest projection, reconciliation, ego/path API, auth scoping |
| `test_notes.py` | No | Notes graph projection, viz page independence |
| `test_phase3_checkpoint.py` | Yes for Gate 1 | Phase 3 validation: reconciliation completeness, manual sign-off gates |

## Run

```bash
make test-integration           # local venv
make compose-test-integration   # Docker (includes FalkorDB overlay)
```

Slow tests (65s RSS poll wait) are skipped. To run everything:

```bash
make test-integration-full
```

Manual gate tests (Phase 3 checkpoint) are always skipped in CI:

```bash
cd orchestrator && python3 -m pytest ../tests/integration/test_phase3_checkpoint.py -v -m manual
```

Override base URL:

```bash
LUMOGIS_API_URL=http://host.docker.internal:8000 make test-integration
```

CI runs **orchestrator and stack-control unit tests** on each PR (see `.github/workflows/ci.yml`). **Integration** tests need a live stack; **`make compose-test-integration`** uses the FalkorDB overlay. See [`../docs/testing/automated-test-strategy.md`](../docs/testing/automated-test-strategy.md) for the full matrix (web, Playwright, KG image, mock capability, parity).
