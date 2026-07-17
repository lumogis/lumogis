# Lumogis agent orientation

Concise onboarding for AI assistants and contributors working in the **`lumogis/lumogis`** public tree. Summarises **committed evidence** in the checkout — not a live external backlog.

**Maintainer note:** This file is authored under **`docs/public-export/`** in the private product repo and substituted into the AGPL export by **`scripts/create-upstream-export-tree.sh`**. It is **not** the private maintainer context pack.

---

## Last refreshed

| Field | Value |
| --- | --- |
| **Date** | **2026-07-12** |
| **Branch / commit** | **`main`** @ **`797c81470`** (maintainer product repo at refresh) |
| **Evidence consulted** | `README.md`, `ARCHITECTURE.md`, `CONTRIBUTING.md`, `CONTRIBUTING-BEGINNERS.md` (export template), `Makefile`, `docs/capabilities.md`, `docs/testing/automated-test-strategy.md`, `docs/testing/README.md`, `CHANGELOG.md` **[0.10.0]**, `scripts/debug/README.md` |

---

## What Lumogis is

- **Self-hosted household knowledge base** — data and indexes stay on hardware you control (Docker Compose, **AGPL-3.0-only**).
- **Core** = FastAPI orchestrator; **Lumogis Web** = first-party SPA behind **Caddy** (same-origin) — search, document-chat, capture, sharing, and admin.
- **Lumogis Search** = Tauri 2 desktop overlay for memory search against your household server (`clients/lumogis-search/`).
- **Household sharing** — per-user personal/shared scopes; documents and entities can be published to the household with scoped visibility.
- **Optional LibreChat** profile exists for OpenAI-compatible chat; Lumogis Web is the primary surface.
- **Not** a Lumogis-operated SaaS — operators run their own stack.

Published container images (`ghcr.io/lumogis/lumogis-orchestrator`, `ghcr.io/lumogis/lumogis-web`) are built from **this public repo** — see **ADR 037** (`docs/decisions/037-ghcr-publish-public-repo-only.md`).

---

## Repository map

| Path | Role |
| --- | --- |
| `CONTRIBUTING-BEGINNERS.md` | First-time contributor steps + copy-paste agent prompt |
| `orchestrator/` | Core — APIs, services, plugins, actions, signals |
| `clients/lumogis-web/` | Lumogis Web SPA |
| `clients/lumogis-search/` | Lumogis Search desktop overlay (Tauri 2) |
| `stack-control/` | Compose/stack helper service |
| `postgres/` | DB init and migrations |
| `docker/`, `docker-compose*.yml` | Runtime and optional profiles |
| `docs/decisions/` | ADRs |
| `docs/architecture/` | Architecture notes |
| `docs/capabilities.md` | Feature catalogue |
| `docs/LUMOGIS_REFERENCE_MANUAL.md` | Operator narrative |
| `docs/testing/` | Test strategy + **TEST-COVERAGE-MATRIX-*** (core/web) |
| `scripts/` | Doctor, debug runners, fixtures |
| `.github/workflows/` | CI — `ci.yml`, `openapi-check`, `search-overlay-build.yml` (Lumogis Search installers), `changelog.yml` |

---

## Architecture anchors

- **Five concepts:** Services, Adapters, Plugins, Signals, Actions — see [`ARCHITECTURE.md`](../ARCHITECTURE.md).
- **Ask / Do** for tool and action safety — **ADR 006**.
- **Household scopes** — personal/shared/system memory and document visibility — **ADR 015**, **ADR 155**.
- **Capture** — Quick Capture inbox (edit, commit to memory) and read-only archive for indexed captures — **ADR 161**.
- **Tool catalog** read-only observation vs execution — `orchestrator/services/unified_tools.py`, **ADR 034**.
- **MCP** = external interoperability layer — `orchestrator/mcp_server.py`, memory read/write tools — **ADR 017**, **ADR 128–135**.
- **Context building** — hybrid entity selection and budgets — **ADR 051**; optional chat auto-RAG — **ADR 059**, **ADR 106**.
- **Conversation history** — browse/continue/delete in Lumogis Web; server-side transcript persistence — **ADR 074**, **ADR 085**, **ADR 156**.
- **Admin** — system status, search/retrieval settings (BGE reranker toggle), read-only feature-flags panel — **ADR 074**, **ADR 159**, **ADR 160**.
- **Lumogis Search export** — public AGPL overlay at `clients/lumogis-search/`; installer CI ships in this tree — **ADR 080**, **ADR 082**; open-core boundary — **ADR 081**, **ADR 042**.

For operator deployment: [`docs/deployment/quickstart.md`](deployment/quickstart.md), [`README.md`](../README.md).

---

## Verification commands (contributors)

| Layer | Typical command |
| --- | --- |
| Orchestrator unit | `make test` |
| Lint | `make lint` |
| Compose integration | `make compose-test`, `make compose-test-integration` |
| Web | `make web-test`, `make web-lint`, `make web-build`, `make web-e2e-prove` |
| Search overlay (Vitest + Rust) | `cd clients/lumogis-search && npm test`; `make search-dev`, `make search-build` |
| OpenAPI CI contract | `make openapi-check` |
| Coverage matrix format | `make coverage-matrix-check` (core/web matrices) |
| Fast local debug chain | `make debug`, `make test-list` — see `scripts/debug/README.md` |
| Operator health | `make doctor` |

Full strategy: [`docs/testing/automated-test-strategy.md`](testing/automated-test-strategy.md). Feature→test map: [`docs/testing/README.md`](testing/README.md).

---

## How to work as an agent here

1. **First-time contributors:** start with **`CONTRIBUTING-BEGINNERS.md`** (human steps + fenced paste prompt).
2. Read **`AGENTS.md`** (guardrails) and this file (orientation).
3. Read **`ARCHITECTURE.md`** before structural code changes.
4. Prefer **small, scoped** diffs; match existing style in touched modules.
5. Run tests that match touched paths; paste summaries in PRs.
6. For security-sensitive behaviour, read **`SECURITY.md`** and relevant ADRs before proposing changes.
7. Use **GitHub Issues** for follow-ups.

---

## Related docs (depth)

- [`docs/README.md`](README.md) — documentation index
- [`docs/extending/extending-the-stack.md`](extending/extending-the-stack.md) — plugins and extension
- [`CHANGELOG.md`](../CHANGELOG.md) — shipped product changes (latest: **0.10.0**)
- [`TELEMETRY.md`](../TELEMETRY.md) — telemetry posture
