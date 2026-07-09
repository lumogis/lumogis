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
| `test_document_chat_scoped.py` | No (Qdrant + embedder) | LUM-503: live document-scoped chat round-trip — seeded `document_id` returns scoped citations |

## Document-scoped chat fixture (LUM-503)

`test_document_chat_scoped.py` proves `POST /api/v1/chat/completions` with a
`document_id` returns scoped citations. It needs a known document seeded first.
Seed it (lumogis-test stack only) via the real ingest path:

```bash
COMPOSE_PROJECT_NAME=lumogis-test scripts/seed-document-chat-fixture.sh
# -> {"chunk_count": N, "document_id": <id>, "file_path": "...", "ok": true}
```

Then verify by hand with curl (login first to get `$TOK`):

```bash
curl -sS -X POST "$LUMOGIS_API_URL/api/v1/chat/completions" \
  -H "Authorization: Bearer $TOK" -H 'Content-Type: application/json' \
  -d '{"model":"claude","stream":false,"document_id":<id>,
       "messages":[{"role":"user","content":"What is the secret pangram in this document?"}]}' \
  | jq .lumogis.context_citations
```

The test resolves the seeded id from `GET /api/v1/documents` (matching the
fixture file_path), so you do not hardcode it; it skips cleanly if the fixture
is unseeded or smoke credentials are unset.

## Cursor integration full gate (LUM-540)

Opt-in tier-2 latency gate: seed `tests/fixtures/coding_bank.json` into real
Postgres + Qdrant, then assert MCP `recall` p95 &lt; 200ms on the 50-memory
coding bank.

Prerequisites:

1. Full **lumogis-test** stack (`config/test.env.example`) — orchestrator,
   Postgres (host publish `127.0.0.1:5433` via `docker-compose.test.yml`),
   Qdrant, and Ollama with **`nomic-embed-text`** pulled (384-dim).
2. Warm embedder, low concurrent load during the gate run.

```bash
COMPOSE_PROJECT_NAME=lumogis-test docker compose --env-file config/test.env.example up -d
make seed-cursor-integration-fixture
make prove-cursor-integration-full
# or: set -a && source ai-workspace/mcp/cursor-integration-full.env && make test-cursor-integration-full
```

The seed script writes `ai-workspace/mcp/cursor-integration-full.env` (gitignored).
Default tier-1 harness (`make test-cursor-integration`) stays on fakes and is unchanged.

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
