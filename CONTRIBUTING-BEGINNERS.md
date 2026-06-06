# Contributing to Lumogis — beginners

Welcome! This guide is a gentler on-ramp than [CONTRIBUTING.md](CONTRIBUTING.md). Read that file for the full contributor reference (CLA, code boundaries, integration tests, and export hygiene).

## Your first contribution (step by step)

1. **Fork and clone** [lumogis/lumogis](https://github.com/lumogis/lumogis) on GitHub, then clone your fork locally.
2. **Prerequisites:** Docker and Docker Compose, **Python 3.12**, and `make` (see [CONTRIBUTING.md](CONTRIBUTING.md) — Development setup).
3. **Create a virtual environment** and activate it:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate    # Windows: .venv\Scripts\activate
   ```
4. **Install CI-equivalent dev dependencies** (copy verbatim):
   ```bash
   python -m pip install -r orchestrator/requirements.txt && python -m pip install -r orchestrator/requirements-dev.txt && python -m pip install -r stack-control/requirements-dev.txt
   ```
5. **Run unit tests** (no Docker required — tests use mock adapters):
   ```bash
   make test
   make lint
   ```
6. **Optional — run the full stack** (only if you need a live deployment or integration tests later):
   ```bash
   cp .env.example .env
   docker compose up -d
   ```
   For integration tests after the stack is up, see `make test-integration` and `make compose-test` in [CONTRIBUTING.md](CONTRIBUTING.md).
7. **Pick a good first issue** from GitHub (label query only — do not rely on hardcoded issue numbers):
   [good first issue — open issues](https://github.com/lumogis/lumogis/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22)
8. **Create a branch**, make the smallest safe change, run `make lint` and `make test` again, then open a pull request. Link the issue and sign the CLA when the bot prompts you (see [CONTRIBUTING.md](CONTRIBUTING.md) — Contributor Licence Agreement).

## Safest first code change

If you are unsure where to start, add a small **ingest extractor** under `orchestrator/adapters/`. The canonical pattern is [`orchestrator/adapters/pdf_extractor.py`](orchestrator/adapters/pdf_extractor.py): import `extractor` from `config`, decorate your function with `@extractor(".ext")`, and implement `def extract_<name>(path: str) -> str`. `get_extractors()` auto-imports adapter modules — no Protocol, factory branch, or config wiring.

More detail: [CONTRIBUTING.md — How to write a new extractor](CONTRIBUTING.md#how-to-write-a-new-extractor).

## Architecture pointers

- [ARCHITECTURE.md](ARCHITECTURE.md) — how Core, services, plugins, and clients fit together
- [AGENTS.md](AGENTS.md) — layout, guardrails, and verification for coding agents
- [docs/LUMOGIS_AGENT_ORIENTATION.md](docs/LUMOGIS_AGENT_ORIENTATION.md) — concise repo map and common commands

## Optional — Let an AI coding agent guide you

Copy everything inside the fence below into a **new** Cursor or Codex chat after cloning this repository:

```text
I want to make my first contribution to Lumogis (lumogis/lumogis). Read these files first, in order:
CONTRIBUTING-BEGINNERS.md, CONTRIBUTING.md, AGENTS.md, docs/LUMOGIS_AGENT_ORIENTATION.md, ARCHITECTURE.md.

Then guide me step by step: verify Python 3.12 and Docker prerequisites, create a venv, run the CI-equivalent pip install from CONTRIBUTING.md (orchestrator/requirements.txt, orchestrator/requirements-dev.txt, stack-control/requirements-dev.txt), run make test (unit tests — no Docker), then optionally cp .env.example .env and docker compose up -d only if I need the live stack. Help me pick a good first issue from https://github.com/lumogis/lumogis/issues?q=is%3Aissue+is%3Aopen+label%3A%22good+first+issue%22, create a branch, make the smallest safe change (prefer a new ingest extractor in orchestrator/adapters/ using @extractor per orchestrator/adapters/pdf_extractor.py if unsure), run make lint and make test, and prepare a PR that mentions the CLA and links the issue. Quote exact commands and errors; do not skip failed checks. Do not reference private maintainer docs or issue trackers not in this repository.
```

If your agent cannot open local files, clone `lumogis/lumogis` first or paste the contents of the files listed above.

## Troubleshooting

- **`make test` says pytest is missing** — activate your venv and run the pip install one-liner in step 4 above.
- **`make lint` fails** — fix ruff issues in `orchestrator/` before opening a PR.
- **Unit vs integration tests** — `make test` does not need Docker; `make compose-test` and `make test-integration` need a running stack (`docker compose up -d`).
- **Docker errors** — ensure Docker is running; see [CONTRIBUTING.md](CONTRIBUTING.md) and the root [README.md](README.md) quickstart.
