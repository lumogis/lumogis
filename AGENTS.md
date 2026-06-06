# AGENTS.md — Lumogis contributor guide (public AGPL tree)

## Purpose

This file orients **coding agents and contributors** working in the **`lumogis/lumogis`** repository: layout, guardrails, verification, and where to read architecture detail.

It applies to this repository checkout.

## Read first

1. **[`docs/LUMOGIS_AGENT_ORIENTATION.md`](docs/LUMOGIS_AGENT_ORIENTATION.md)** — concise architecture and repo map (key paths, common commands).
2. **[`ARCHITECTURE.md`](ARCHITECTURE.md)** — how Core, services, plugins, and clients fit together.
3. **[`CONTRIBUTING.md`](CONTRIBUTING.md)** — setup, tests, PR expectations, export/CI notes.
4. **[`docs/capabilities.md`](docs/capabilities.md)** — what the platform can do at a feature level.
5. **[`docs/decisions/`](docs/decisions/)** — finalised architecture decisions (ADRs).

Do not duplicate long prose from those files into issues or comments; link paths instead.

## What this repository is

- **Self-hosted, local-first, privacy-first** household AI under **AGPL-3.0-only** ([`LICENSE`](LICENSE)).
- **Core** = FastAPI orchestrator ([`orchestrator/`](orchestrator/)).
- **Lumogis Web** = first-party SPA ([`clients/lumogis-web/`](clients/lumogis-web/)), same-origin behind Caddy.
- **Lumogis Search** = Tauri 2 desktop memory-search overlay ([`clients/lumogis-search/`](clients/lumogis-search/)), connecting to your household server over HTTP.
- **Default stack** = Docker Compose ([`docker-compose.yml`](docker-compose.yml)); optional profiles and overlays documented in [`README.md`](README.md).

## Boundaries (agents)

- Work only against files **present in this checkout**.
- Do **not** invent references to repositories, issue trackers, or tooling that are not documented here.
- Do **not** commit secrets, API keys, tokens, or real `.env` files (only [`.env.example`](.env.example) patterns).
- Respect **AGPL-3.0-only** licensing on contributed code ([`CONTRIBUTING.md`](CONTRIBUTING.md) CLA section).
- **GHCR images** (`ghcr.io/lumogis/lumogis-orchestrator`, `ghcr.io/lumogis/lumogis-web`) are published from **this public repo** — see [`docs/decisions/037-ghcr-publish-public-repo-only.md`](docs/decisions/037-ghcr-publish-public-repo-only.md).

## Common commands

| Goal | Command |
| --- | --- |
| Unit tests (host venv) | `make test` |
| Lint orchestrator | `make lint` |
| Full stack integration | `make compose-test` / `make compose-test-integration` (Docker required) |
| Web client tests | `make web-test` |
| OpenAPI contract (CI parity) | `make openapi-check` |
| Coverage matrix format (PRs touching matrices) | `make coverage-matrix-check` |
| Fast local debug chain | `make debug`, `make test-list` |
| Local stack | `docker compose up -d` (after `cp .env.example .env`) |
| Operator health CLI | `make doctor` |
| Lumogis Search overlay | `make search-dev`, `make search-build` (`clients/lumogis-search/`) |

See [`Makefile`](Makefile) and [`docs/testing/automated-test-strategy.md`](docs/testing/automated-test-strategy.md) for the full matrix.

## Verification expectations

- Run **meaningful** tests for touched paths; do not claim green without command output.
- Backend changes: **pytest** / compose targets as appropriate.
- Web changes: **npm** lint/test/build targets as appropriate.
- Docs-only changes: diff hygiene and link checks against files that exist **in this tree**.
- Release-hygiene changes: run `scripts/create-upstream-export-tree.sh` and `scripts/check-public-export.sh` when you touch export scripts ([`CONTRIBUTING.md`](CONTRIBUTING.md)).

## Security and behaviour

- **Ask / Do** safety model for actions and tools — [`docs/decisions/006-ask-do-safety-model.md`](docs/decisions/006-ask-do-safety-model.md), [`SECURITY.md`](SECURITY.md).
- Report vulnerabilities per [`SECURITY.md`](SECURITY.md) (coordinated disclosure); do not open public issues for sensitive reports.

## Issue tracking

Use **GitHub Issues** on **`lumogis/lumogis`** for bugs and contributions. There is no public Linear workspace tied to this tree.
