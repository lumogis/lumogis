# AGENTS.md — Lumogis contributor guide (public AGPL tree)

## Purpose

This file orients **coding agents and contributors** working in the **`lumogis/lumogis`** repository: layout, guardrails, verification, and where to read architecture detail.

It applies to this repository checkout.

## Read first

1. **[`CONTRIBUTING-BEGINNERS.md`](CONTRIBUTING-BEGINNERS.md)** — first-time contributors: human setup steps + copy-paste agent prompt.
2. **[`docs/LUMOGIS_AGENT_ORIENTATION.md`](docs/LUMOGIS_AGENT_ORIENTATION.md)** — concise architecture and repo map (key paths, common commands).
3. **[`ARCHITECTURE.md`](ARCHITECTURE.md)** — how Core, services, plugins, and clients fit together.
4. **[`CONTRIBUTING.md`](CONTRIBUTING.md)** — setup, tests, PR expectations, export/CI notes.
5. **[`docs/capabilities.md`](docs/capabilities.md)** — what the platform can do at a feature level.
6. **[`docs/decisions/`](docs/decisions/)** — finalised architecture decisions (ADRs).

Do not duplicate long prose from those files into issues or comments; link paths instead.

## What this repository is

- **Self-hosted, local-first, privacy-first** household knowledge base under **AGPL-3.0-only** ([`LICENSE`](LICENSE)).
- **Core** = FastAPI orchestrator ([`orchestrator/`](orchestrator/)).
- **Lumogis Web** = first-party SPA ([`clients/lumogis-web/`](clients/lumogis-web/)), same-origin behind Caddy — search, document-chat, capture inbox/archive, household sharing, **email triage** (`/email`), unified document library, admin, and **safety playground**.
- **Capability security** — invoke contract v1, permission scopes, community sandbox, optional egress containment — see [`docs/capabilities.md`](docs/capabilities.md) and ADRs **169–173**.
- **Email connector arc** — IMAP signal source, taxonomy, optional write-back, SMTP send/reply/compose, attachments at `/email/attachments`, OAuth mailbox connect, poll-health banners, structured SMTP opt-in errors, post-setup send enablement, recoverable failed sends on Approvals, and unsubscribe; also extracted-text body reading, local email search, unified read across mailboxes, an on-demand sending address, mailbox auth method in source health, OAuth refresh back-off and IDNA host handling — see [`docs/capabilities.md`](docs/capabilities.md) and ADRs **174–202**, **205**, **207–214**, **225**, **228**, **237**.
- **Action proposals** — atomic claim execution with terminal failure on the first attempt — ADR **201** (amends **ADR 040**).
- **Sources and connectors** — per-user paperless / RSS / page sources with edit, pause/resume and poll health; connector test-connection and on/off switch; a switched-off connector reads as off everywhere — ADRs **215–219**, **221**, **226**, **238**.
- **Knowledge graph** — `services/lumogis-graph/` (FalkorDB-backed), AGPL-3.0-only; `GRAPH_MODE` defaults to `disabled`, and **`service` is the only mode that works on the standard Core image** — ADRs **229**, **239**, [`docs/capabilities.md`](docs/capabilities.md) § Knowledge Graph. Every node the writer projects carries `scope`, which every visibility-filtered graph read relies on (**ADR 260**); orphan GC and co-occurrence counts (**ADR 259**); graph pages load only same-origin vendored scripts (**ADR 256**).
- **Install from two release files** — households download `docker-compose.yml` and `example.env` from a GitHub Release, run `docker compose up -d`, and update with `docker compose pull && docker compose up -d`; nothing is built ([`docs/deployment/quickstart.md`](docs/deployment/quickstart.md), [`docs/deployment/upgrading.md`](docs/deployment/upgrading.md), **ADR 262**). The release compose is generated from this repo's `docker-compose.yml` by `scripts/release/build_release_compose.py`; the orchestrator image creates the database schema on a new database.
- **Default install and deployment** — a default install opens Lumogis Web in single-user mode with sign-in off; turn sign-in on before anyone else can reach it ([`docs/deployment/quickstart.md`](docs/deployment/quickstart.md), **ADR 247**). The orchestrator API is published on `127.0.0.1` only, Caddy is the only trusted proxy, and the orchestrator runs as the owner of the checkout, not root (**ADR 246**).
- **Lumogis Search** = Tauri 2 desktop memory-search overlay ([`clients/lumogis-search/`](clients/lumogis-search/)), connecting to your household server over HTTP.
- **Default stack** = Docker Compose ([`docker-compose.yml`](docker-compose.yml)). In a checkout it **builds** the Lumogis images (`pull_policy: build`); optional profiles and overlays documented in [`README.md`](README.md).

## Open-core boundary

This repository is the **public AGPL export** of Lumogis Core, Lumogis Web, Lumogis Search, the knowledge graph, tests, and operator docs. The **knowledge graph** (`services/lumogis-graph/`, `orchestrator/plugins/graph/`) is AGPL-3.0-only and ships here ([`docs/decisions/229-lum-855-kg-agpl-relicence.md`](docs/decisions/229-lum-855-kg-agpl-relicence.md); as built: [`docs/decisions/239-lum-855-kg-publication-as-built.md`](docs/decisions/239-lum-855-kg-publication-as-built.md)). Maintainer-only paths are **not** in this tree. Do not document or depend on paths that are absent from your checkout.

## Boundaries (agents)

- Work only against files **present in this checkout**.
- Do **not** invent references to repositories, issue trackers, or tooling that are not documented here.
- Do **not** commit secrets, API keys, tokens, or real `.env` files (only [`.env.example`](.env.example) patterns).
- Respect **AGPL-3.0-only** licensing on contributed code ([`CONTRIBUTING.md`](CONTRIBUTING.md) CLA section).
- **GHCR images** (`ghcr.io/lumogis/lumogis-orchestrator`, `-web`, `-stack-control`, `-backup`, `-caddy`) are published and attested from **this public repo** — see [`docs/decisions/037-ghcr-publish-public-repo-only.md`](docs/decisions/037-ghcr-publish-public-repo-only.md) and [`docs/decisions/262-lum-980-install-from-published-images.md`](docs/decisions/262-lum-980-install-from-published-images.md). A Release is published by `.github/workflows/release-assets.yml` only after it has pulled those images anonymously by digest.

## Common commands

| Goal | Command |
| --- | --- |
| Unit tests (host venv) | `make test` |
| Same tests with Docker only (offline image, optional throwaway databases) | `scripts/test-env.sh run --tier 1 -- make test` (tier 2 adds Postgres, Qdrant, FalkorDB; see [`CONTRIBUTING.md`](CONTRIBUTING.md)) |
| Integration hermeticity (touching `orchestrator/tests/integration/`) | `make check-integration-hermeticity` |
| Lint orchestrator | `make lint` |
| Full stack integration | `make compose-test` / `make compose-test-integration` (Docker required) |
| Compose policy (touching overlays) | `make compose-policy-check` |
| Web client tests | `make web-test`, `make web-lint`, `make web-build` |
| Web E2E (slim stack) | `make web-e2e-prove` (Docker; see [`CONTRIBUTING.md`](CONTRIBUTING.md) Public CI) |
| OpenAPI contract (CI parity) | `make openapi-check` |
| Coverage matrix format (PRs touching matrices) | `make coverage-matrix-check` |
| ADR numbering (PRs adding an ADR) | `make adr-check` |
| Script exec bits (PRs adding shell scripts) | `make exec-bit-check` |
| Knowledge graph service tests | `make compose-test-kg` (Docker; not part of `make test`); `make test-graph-parity` (slow; its own compose project) |
| Live ingest lane (sign-in on, graph service) | `make e2e-ingest-live` (Docker, 15–40 min; fails on any skip) |
| Fast local debug chain | `make debug`, `make test-list` |
| Local stack (checkout; builds the images) | `docker compose up -d` (after `cp .env.example .env`); update with `git pull && make update` |
| Release install files | `make release-compose` (writes `dist/release/`; `make compose-policy-check` checks the generated file too) |
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
- **Per-user ownership** — chat file tools follow the same visibility rule as search, `POST /ingest` is an admin-only rescan of the configured library folders, and tool handlers take a keyword-only `user_id` — [`docs/decisions/230-lum-884-ingest-and-chat-tool-ownership.md`](docs/decisions/230-lum-884-ingest-and-chat-tool-ownership.md).
- **Ask / Do switching** — which connectors may be switched to DO is one rule, `orchestrator/services/permission_connectors.py` (**ADR 258**): connectors with registered write actions and known capability services; never read-only tool connectors, never a connector with a hard-limited action.
- **Credentials and sessions** — every account-state change ends every credential, and access and refresh tokens are kept apart (**ADR 243**); refresh rotation re-checks the session inside its transaction (**ADR 252**).
- Report vulnerabilities per [`SECURITY.md`](SECURITY.md) (coordinated disclosure); do not open public issues for sensitive reports.

## Issue tracking

Use **GitHub Issues** on **`lumogis/lumogis`** for bugs and contributions. There is no public Linear workspace tied to this tree.
