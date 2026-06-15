# Contributing to lumogis

Thank you for your interest in contributing. This document covers everything you need to get started.

For architecture internals, read [ARCHITECTURE.md](ARCHITECTURE.md) first.

All participants must follow the [Code of Conduct](CODE_OF_CONDUCT.md).

**First-time contributors:** start with [CONTRIBUTING-BEGINNERS.md](CONTRIBUTING-BEGINNERS.md) (human steps + copy-paste agent prompt).

## AI assistants and IDE agents

For work in **this public repository**, start with **[CONTRIBUTING-BEGINNERS.md](CONTRIBUTING-BEGINNERS.md)** if you are new, then read [AGENTS.md](AGENTS.md) and [docs/LUMOGIS_AGENT_ORIENTATION.md](docs/LUMOGIS_AGENT_ORIENTATION.md). Those files describe layout, open-core boundaries, and verification for the AGPL tree only.

For ChatGPT or Claude outside the repository, add **`CONTRIBUTING-BEGINNERS.md`**, **`docs/LUMOGIS_AGENT_ORIENTATION.md`**, and **`ARCHITECTURE.md`** to project knowledge — not private maintainer context packs or internal backlog exports.

## Public CI parity (OpenAPI)

The public AGPL tree is produced by export (`scripts/create-upstream-export-tree.sh`); it receives **sanitized** [`docs/public-export/AGENTS.md`](docs/public-export/AGENTS.md) and [`docs/LUMOGIS_AGENT_ORIENTATION.md`](docs/public-export/LUMOGIS_AGENT_ORIENTATION.md) (not the private context pack). It must keep the same **`.github/workflows/ci.yml`** surface as private development, including the **`openapi-check`** job and the offline scripts, Makefile target, web client snapshot/codegen inputs, and breaking-check fixtures that job relies on.

**Do not** add any of the paths asserted by **`scripts/check-public-export.sh`** (search for `Required presence (LUM-303)` and the numbered path comment block) to **`scripts/public-export-strip-list.txt`** without updating that assertion, **`orchestrator/tests/test_check_public_export_script.py`**, and this section in the **same** change — otherwise **`make verify-public-rc`** / **`scripts/check-public-export.sh`** will fail on purpose.

**Search overlay CI (LUM-433):** the public export must include **`.github/workflows/search-overlay-build.yml`** (four-target Tauri matrix for **`clients/lumogis-search/`**, unsigned v1 installers, **`search-v*`** tag releases on **`lumogis/lumogis`** only). **Do not** add that workflow path to **`scripts/public-export-strip-list.txt`** without updating **`scripts/check-public-export.sh`** (search for `Required presence (LUM-433)`), **`orchestrator/tests/test_check_public_export_script.py`**, and this section in the **same** change.

**Beginners onboarding (LUM-378):** the public export must include **`CONTRIBUTING-BEGINNERS.md`** at the repository root (copied from **`docs/public-export/CONTRIBUTING-BEGINNERS.md`**). **Do not** add that path to **`scripts/public-export-strip-list.txt`** without updating **`scripts/check-public-export.sh`** (search for `Required presence (LUM-378)`), **`orchestrator/tests/test_check_public_export_script.py`**, and this section in the **same** change.

Architecture context: **`docs/decisions/037-ghcr-publish-public-repo-only.md`** (export and public CI); **`docs/decisions/053-lum-94-ci-openapi-codegen-check-without-live-orchestrator.md`** (OpenAPI gate); **[ADR 061 — LUM-303](docs/decisions/061-lum-303-public-ci-parity-openapi-check-via-export.md)** (export presence contract).

## Optional CI — web Playwright (LUM-60)

The workflow **`.github/workflows/web-e2e.yml`** starts a **slim** Compose project (PostgreSQL, Qdrant, orchestrator, Lumogis Web, Caddy, stack-control — **no Ollama**) and runs **`make web-e2e-prove`** with Playwright on the runner host. The **`docker-compose.web-e2e-ci.yml`** overlay sets **`LUMOGIS_RC_SESSION_END_STUB=true`** on the orchestrator so **`POST /session/end`** → batch **`session_end`** writes Postgres **`sessions`** rows without Ollama (required for **LUM-414** conversation-history e2e on the slim stack).

| Topic | Detail |
| --- | --- |
| **Triggers** | **`pull_request`** to **`main`** / **`master`** with types **`opened`**, **`synchronize`**, **`reopened`**, **`labeled`**, **`unlabeled`** (toggling the label re-runs the job), and **`workflow_dispatch`**. The nightly **`schedule`** trigger was removed from **`.github/workflows/web-e2e.yml`** (2026-05-30). |
| **Path gate** | On pull requests, **`.github/scripts/web-e2e-paths.sh`** must see a diff hit on the web, Caddy, compose, Makefile, or workflow surfaces listed in that script — otherwise the job logs **`SKIP_WEB_E2E_PATHS`** and exits successfully without starting Docker. |
| **Label (pull requests only)** | Add the repository label **`ci:run-web-e2e`** so cred-gated steps run on same-repo PRs. Path-matched PRs **without** the label **skip** Playwright (they do not fail solely for missing secrets). |
| **Fork PRs** | When **`github.event.pull_request.head.repo.full_name`** differs from the base repository, the workflow logs **`SKIP_FORK_PR`** and skips cred-gated steps. |
| **Secrets** | **`LUMOGIS_WEB_SMOKE_EMAIL`** and **`LUMOGIS_WEB_SMOKE_PASSWORD`** (password at least **12** characters). They must match the bootstrap admin injected for the disposable stack (**`LUMOGIS_BOOTSTRAP_ADMIN_EMAIL`** / **`LUMOGIS_BOOTSTRAP_ADMIN_PASSWORD`** — the workflow sets both pairs from the same secrets). Configure them as **repository secrets** under the same trust model as your other CI secrets. |

### Optional — Ollama mutation Playwright (LUM-450)

The slim **`web-e2e.yml`** job does **not** start Ollama (ADR-064). To exercise **real** Ollama pull/delete on **`/admin/system-status`** through Caddy, use the opt-in target **`make web-e2e-ollama-prove`** on a **full** stack (`docker compose up -d` **including** the **`ollama`** service).

| Variable | Required | Purpose |
| --- | --- | --- |
| **`LUMOGIS_WEB_SMOKE_EMAIL`** / **`LUMOGIS_WEB_SMOKE_PASSWORD`** | yes | Admin smoke user (≥12-char password) |
| **`LUMOGIS_E2E_EXPECT_ADMIN`** | yes (`1`) | Same gate as LUM-413 admin e2e |
| **`LUMOGIS_E2E_EXPECT_OLLAMA`** | yes (`1`) | Enables **`admin_ollama_mutations.spec.ts`** (skipped when unset) |
| **`LUMOGIS_E2E_OLLAMA_PULL_MODEL`** | no | Ephemeral pull model (default **`tinyllama:1.1b`**) |
| **`PLAYWRIGHT_BASE_URL`** | no | Caddy front door (default **`http://127.0.0.1`**) |

The spec pulls then deletes **only** the ephemeral model — never household defaults. It is **not** part of **`make web-e2e-prove`**, **`verify-public-rc`**, or **`verify-public-rc-full`** (Phase 1). Phase 2 optional CI and **`verify-public-rc-full`** auto-wire (after cold-pull timing baseline) are tracked in **LUM-453**.

This job does **not** replace **`make verify-public-rc`** or **`make verify-public-rc-full`**; do not use it as a reason to set **`VERIFY_PUBLIC_RC_SKIP_WEB_E2E`**.

---

## Contributor Licence Agreement (CLA)

**All contributors must sign the CLA before a PR can be merged.**

Contributions are submitted under the project licence, **AGPL-3.0-only** (GNU Affero General Public License v3.0 only). The CLA grants Lumogis maintainers a perpetual, worldwide, non-exclusive, royalty-free, irrevocable right to use, reproduce, modify, distribute, sublicense and relicense your contributions, including under commercial or alternative licence terms, while you retain full copyright.

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
docker compose up -d               # first boot; Ollama pulls default models (see README)
# Optional — hot-reload orchestrator while editing Python:
make dev
```

`make dev` uses `docker-compose.dev.yml`, which mounts the orchestrator source and reloads on file changes. You do not need to rebuild the Docker image during development.

### Python dependencies for tests and lint

Production images install only each service’s `requirements.txt`. For **local** pytest and ruff, install the dev extras (not baked into runtime images):

| Component | Runtime | Dev/test (local + CI) |
|-----------|---------|------------------------|
| **Orchestrator** | `python -m pip install -r orchestrator/requirements.txt` | `python -m pip install -r orchestrator/requirements-dev.txt` |
| **stack-control** | `python -m pip install -r stack-control/requirements.txt` | `python -m pip install -r stack-control/requirements-dev.txt` |

#### Which install?

| Goal | Command | Notes |
| --- | --- | --- |
| **Orchestrator unit tests only** | `python -m pip install -r orchestrator/requirements-test.txt` | Lighter deps — heavy runtime adapters are mocked in tests (see comments in that file). Does **not** match CI; does **not** cover stack-control or `make lint`. |
| **Full local dev** (`make test`, `make lint`, feature work) | Chained install below (same as the one-liner in § *Running tests (local venv)*) | **CI-equivalent** — `.github/workflows/ci.yml` installs `orchestrator/requirements.txt`, `orchestrator/requirements-dev.txt`, and `stack-control/requirements-dev.txt`. |

If in doubt, use the **full dev** install.

For a full local dev venv that runs `make test` and `make lint`:

```bash
python -m pip install -r orchestrator/requirements.txt
python -m pip install -r orchestrator/requirements-dev.txt
python -m pip install -r stack-control/requirements-dev.txt
```

The [Makefile](Makefile) sets `PYTHON ?= python3` for local test targets so `make test` works on hosts with no `python` shim. With an activated venv, `python` is usually available; you can run `make test PYTHON=python` if needed.

### Running tests (local venv)

If `make test` reports that pytest is missing, install CI-equivalent dev deps into your activated venv:

```bash
python -m pip install -r orchestrator/requirements.txt && python -m pip install -r orchestrator/requirements-dev.txt && python -m pip install -r stack-control/requirements-dev.txt
```

Then:

```bash
make test       # orchestrator + stack-control unit tests — no Docker needed
make test-list  # canonical test inventory (all suites + release stages)
make debug      # fast local chain with summary stdout + tee logs (see scripts/debug/README.md)
make lint       # ruff check + format check
```

Unit tests use mock adapters (`orchestrator/tests/conftest.py`) — no running services required.

```bash
make test-integration   # full-stack tests — requires docker compose up -d
```

Integration tests run against the live stack. See [Integration tests](#integration-tests) below.

### Running tests (Docker only, no local venv)

The orchestrator **runtime** image does not include pytest. Use **`make compose-test`**: it runs `pip install -q -r orchestrator/requirements-dev.txt` inside the container, then `python -m pytest` against the mounted repo (see `Makefile`). Do **not** use `docker compose run orchestrator pytest` — pytest may be missing.

```bash
make compose-test-stack-control   # stack-control unit tests via Compose — Docker only
```

Stack-control unit tests are included in **`make test`** when you have a host venv. Use **`make compose-test-stack-control`** when you lack local pytest or after changes to **`stack-control/`** or its Compose volume mount (no running stack required).

For ad hoc single files in the container, use the same pattern the Makefile uses:

```bash
docker compose run --rm -w /project/orchestrator orchestrator sh -c \
  "pip install -q -r requirements-dev.txt && python -m pytest tests/path/to/test_foo.py -q"
```

Other compose targets: `make compose-lint`, `make compose-test-stack-control`, `make compose-test-integration` (see `Makefile`).

### Coverage matrices

Feature→test evidence lives in [docs/testing/README.md](docs/testing/README.md):

- **Public clone:** update [docs/testing/TEST-COVERAGE-MATRIX-core.md](docs/testing/TEST-COVERAGE-MATRIX-core.md) and/or [docs/testing/TEST-COVERAGE-MATRIX-web.md](docs/testing/TEST-COVERAGE-MATRIX-web.md) when **`/verify-plan`** closes a planned feature chunk — not on every unrelated PR.
- **Full private tree:** KG and desktop matrices under [docs/private/testing/](docs/private/testing/README.md) follow the same **verify-plan** rule (**Step 7c** in `.cursor/skills/verify-plan/SKILL.md`).

The v1 baseline was seeded in **LUM-384** (code audit). **LUM-428** tightened ✅ rows and cross-checks **active + archived** `.cursor/plans/*.plan.md` for test citations. Re-run `python3 scripts/testing/_lum428_audit_matrix_citations.py` after matrix edits. **LUM-429:** CI runs `make coverage-matrix-check` — row ID format, legend, duplicate IDs, catalog sync (`scripts/feature-ids.json`). After adding or renaming IDs, run `node scripts/check-coverage-matrix.mjs --write-catalog` in the same PR. Do not assign ✅ from `docs/capabilities.md` or CHANGELOG alone; cite `` `test_name` in `file` `` in the matrix **Notes** column.

### Release manual checklist

Before a maintainer release sign-off (after automated RC gates on the release line), complete [docs/RELEASE-MANUAL-CHECKLIST.md](docs/RELEASE-MANUAL-CHECKLIST.md) — human verification items cited as **`MS-###`** in coverage matrix 🚫 rows. See [docs/testing/automated-test-strategy.md](docs/testing/automated-test-strategy.md) for automated vs manual boundaries.

### OpenAPI snapshot / Lumogis Web typed client

When you add or rename **`/api/v1/*`** routes, refresh the committed snapshot in the **same PR** as the route change (CI and `make openapi-check` fail on drift):

```bash
cd orchestrator && python -m scripts.dump_openapi --pretty --sort-keys \
  --out ../clients/lumogis-web/openapi.snapshot.json
```

(`orchestrator/scripts/dump_openapi.py` loads the FastAPI app with test-only env hatches — **no** running orchestrator.)

- `npm run codegen` (or `make web-codegen`) regenerates `clients/lumogis-web/src/api/generated/openapi.d.ts` from the **committed** `openapi.snapshot.json`.
- **`npm run codegen:check`**, **`make web-codegen-check`**, and **`make openapi-check`** (alias) compare that snapshot to a fresh `dump_openapi` run — **offline**; they do **not** use `LUMOGIS_OPENAPI_URL` or a live stack. For an optional **live** `/openapi.json` pull, use **`npm run codegen -- --live`** (see `clients/lumogis-web/README.md`). Rare: override which Python runs `dump_openapi` via **`LUMOGIS_OPENAPI_PYTHON`** (see `clients/lumogis-web/scripts/codegen.mjs`).

#### OpenAPI breaking-change contract (LUM-302)

CI runs **[oasdiff](https://github.com/oasdiff/oasdiff)** (`oasdiff breaking`) on the **committed** `clients/lumogis-web/openapi.snapshot.json` after `make openapi-check` succeeds: **base** = snapshot at the PR **merge-base** (or `HEAD~1` on `push`), **revision** = working tree at `HEAD`. This is **semantic** classification on top of the LUM-94 binary snapshot/codegen gate — see [ADR 053](docs/decisions/053-lum-94-ci-openapi-codegen-check-without-live-orchestrator.md) and [ADR 060 — LUM-302](docs/decisions/060-lum-302-openapi-breaking-change-classifier.md).

**`OPENAPI_BREAKING_FAIL_ON`** (passed to oasdiff `--fail-on`): **`ERR`** (default in CI) exits **1** only on definite breaking changes; **`WARN`** is **stricter** (fails on ERR **or** WARN); **`INFO`** is strictest. **`off`** skips oasdiff entirely and prints a `::warning::OpenAPI breaking gate bypassed (OPENAPI_BREAKING_FAIL_ON=off)` audit line — use only with maintainer intent.

**Local run:** use **Go 1.26+** (the oasdiff v1.15.2 module requires it — CI uses `setup-go` **1.26.x**), then install the pinned CLI (`go install github.com/oasdiff/oasdiff@v1.15.2` — same pin as `.github/workflows/ci.yml`), then `make openapi-breaking-check`. Optional **`OPENAPI_BREAKING_BASE_REF`** (e.g. `origin/main`) selects the base revision explicitly; otherwise the script uses **`HEAD~1`** locally (merge-base is used automatically on `pull_request` in Actions).

**Ignore files** (`--warn-ignore` / `--err-ignore`): do **not** commit an ignore rules file without **explicit reviewer approval** in the PR (link the Linear issue or an ADR note). If OpenAPI 3.x / oasdiff behaviour becomes a recurring problem, revisit tool choice per the LUM-302 ADR “revisit conditions”.

---

## Changelog

We follow [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) in [CHANGELOG.md](CHANGELOG.md).

### When you must update the changelog

If your pull request changes **product-facing paths** tracked in [`.github/workflows/changelog.yml`](.github/workflows/changelog.yml), **`CHANGELOG.md` must appear in the PR diff** (typically under **`[Unreleased]`** with **Added** / **Changed** / **Fixed** / **Removed** as appropriate). The same path list lives in [scripts/changelog-gate-paths.txt](scripts/changelog-gate-paths.txt) for local checks—**keep these in sync** when globs change.

PRs that touch only paths outside that filter (for example **`docs/**`** alone or **`.github/`** alone) **do not** run this workflow and have **no** changelog obligation from that gate.

### Bypasses (maintainers)

- GitHub label **`Skip-Changelog`** (see `skipLabels` in the workflow).
- The literal **`[skip changelog]`** anywhere in the **PR description/body** (case-insensitive), matching CI.

Third-party outages or misconfiguration may block the check until fixed; the same bypasses are the supported escape hatches—document in the PR when you use them.

### Branch protection / required checks

If this workflow is marked **required** in branch protection while it uses **workflow-level `paths:`** filters, **docs-only** (or otherwise filtered) PRs may show **no status** from this job and appear stuck (“waiting for status”). **Do not** mark the changelog check **required** until you add a job-level path filter with an always-reporting success job, or your process explicitly handles that case.

### Fork pull requests

Workflows on forks may show **Expected — Waiting for status** until a maintainer approves the first run on that PR. That is normal GitHub behaviour, not a bug in this gate.

### CI on `main` / `master`

[`.github/workflows/changelog.yml`](.github/workflows/changelog.yml) runs on pull requests targeting **`main`** or **`master`** (and on pushes that change that workflow file). For gated product paths, CI requires **`CHANGELOG.md`** updates under **`[Unreleased]`** before merge; merged PRs carry those entries onto the default branch. See bypasses above if you need an exception.

### Local check (optional)

Before pushing:

```bash
make changelog-check
```

Uses [scripts/check-changelog-touched.sh](scripts/check-changelog-touched.sh) (diff vs `origin/dev`, then `origin/main`, then `HEAD~1`). To mimic the **PR-body** skip locally, set **`CHANGELOG_GATE_PR_BODY`** to a string containing **`[skip changelog]`**.

---

## How to write a new extractor

Ingest extractors live under `orchestrator/adapters/`. Register file extensions with the **`@extractor(".ext")`** decorator from `config` (`orchestrator/config.py` — `extractor()` and `get_extractors()` auto-import adapter modules). No factory branches, no Protocol, and no config wiring changes.

```python
# orchestrator/adapters/epub_extractor.py

from config import extractor


@extractor(".epub")
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

That is the entire change for a new file type. The ingest pipeline picks up decorated extractors automatically.

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

**Reference plugin:** `docs/extending/examples/example_plugin/` — a minimal working plugin with routes, hooks, and a README.

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

**CI vs broader automation:** `.github/workflows/ci.yml` runs **orchestrator** and **stack-control unit tests** plus Ruff on every PR. Path-gated Docker jobs include **doctor integration** (`make compose-test-doctor`) and **backup integration** (`make compose-test-backup`, LUM-486) when backup-related paths change. Integration, web, Playwright, KG-image, and parity suites require Docker and/or Node; they are part of the permanent strategy documented in [`docs/testing/automated-test-strategy.md`](docs/testing/automated-test-strategy.md). Run the targets that match your change (e.g. `make compose-test-integration` after HTTP/API work, `make compose-test-backup` after backup sidecar changes, `make web-test` after web changes).

**New behaviour:** add tests at the right layer (unit for pure logic, integration when the HTTP stack matters, web tests for client regressions). Do not commit secret values or paths that **`scripts/check-public-export.sh`** rejects — see **`docs/release/public-agpl-release-workflow.md`** and **`CONTRIBUTING.md`** for **export hygiene** (paths omitted from the upstream tree must never leak into patches meant for the published repo).

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

**Pushing to the public GitHub repo:** follow **`docs/release/public-agpl-release-workflow.md`** so only the export-shaped tree is published.

**PRs must:**
- Pass `make lint` (ruff check + format)
- Pass `make test` (unit tests)
- Include tests for new functionality at the appropriate layer — see [`docs/testing/automated-test-strategy.md`](docs/testing/automated-test-strategy.md)
- Sign the CLA

Describe in the PR body what you ran (`make test`, `make compose-test`, integration/web targets as applicable). Heavy suites may be additionally verified on merge.

**For design decisions:** open a Discussion first — not every idea needs a PR. If you are proposing a new port, changing a Protocol signature, or adding a dependency, start a Discussion so the approach can be agreed before you write code. This saves everyone time.

**For bug reports:** open an Issue with reproduction steps, your OS, and Docker version.

**For security issues:** do not open a public Issue. Email [lumogis@pm.me](mailto:lumogis@pm.me). You may also use GitHub **Private vulnerability reporting** on [lumogis/lumogis](https://github.com/lumogis/lumogis) when it is enabled there.

---

## Code of conduct

This project follows the [Contributor Covenant v2.1](CODE_OF_CONDUCT.md). Be kind.
