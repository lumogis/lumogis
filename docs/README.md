# Documentation Index

> Status: Active
> Last reviewed: 2026-06-14
> Verified against commit: a36f022
> Owner: Docs Librarian

## Canonical docs

- [`LUMOGIS_REFERENCE_MANUAL.md`](LUMOGIS_REFERENCE_MANUAL.md) — consolidated operator and contributor reference
- [`LUMOGIS_AGENT_ORIENTATION.md`](LUMOGIS_AGENT_ORIENTATION.md) — concise onboarding for AI assistants and contributors
- [`capabilities.md`](capabilities.md) — shipped capability narrative
- [Repository root `README.md`](../README.md) — product overview and quickstart (Lumogis Web + Caddy + Core)

## Documentation structure

| Directory | Contents |
|-----------|----------|
| `deployment/` | First-run quickstart (published GHCR images): [`deployment/quickstart.md`](deployment/quickstart.md); remote access (Tailscale / Cloudflare Tunnel): [`deployment/remote-access.md`](deployment/remote-access.md) |
| `guides/` | Operational guides for self-hosters |
| `extending/` | Guides for contributors and plugin authors |
| `architecture/` | Implementation architecture notes |
| `decisions/` | Architecture Decision Records (ADRs) |
| `development/` | Local development setup |
| `testing/` | Test strategy |
| `release/` | Release and export workflows (maintainers) |
| `public-export/` | Maintainer templates copied into the AGPL export tree (**LUM-376**) — see [`public-export/README.md`](public-export/README.md) |
| `private/` | Private maintainer material (not in public export) |

## Architecture

- [`../ARCHITECTURE.md`](../ARCHITECTURE.md) — Core structure, Caddy routing, MCP/capability registry
- [`architecture/plugin-imports.md`](architecture/plugin-imports.md) — plugin import conventions
- [`architecture/tool-vocabulary.md`](architecture/tool-vocabulary.md) — LLM vs MCP tool naming

## Decisions / ADRs

- [`decisions/`](decisions/) — numbered ADRs and [`decisions/DEBT.md`](decisions/DEBT.md)

## Implementation plans

- [`decisions/`](decisions/) and the public architecture references above — primary shipped intent for the tree
- [`testing/automated-test-strategy.md`](testing/automated-test-strategy.md) — CI, pytest layers, integration, web, KG, Playwright; **`make debug`** / **`make test-list`** and [`scripts/debug/README.md`](../scripts/debug/README.md) (**ADR 078**, LUM-377); opt-in Ollama mutation e2e (**ADR 087**, LUM-450)

## Operations

- [`deployment/quickstart.md`](deployment/quickstart.md) — first run from published **GHCR** images
- [`deployment/remote-access.md`](deployment/remote-access.md) — Tailscale Serve / Cloudflare Tunnel for secure off-LAN access
- [`guides/troubleshooting.md`](guides/troubleshooting.md)
- [`guides/gpu-setup.md`](guides/gpu-setup.md)
- [`guides/connector-credentials.md`](guides/connector-credentials.md)
- [`guides/per-user-export-format.md`](guides/per-user-export-format.md)
- [`guides/structured-logging.md`](guides/structured-logging.md)
- [`release/public-agpl-release-workflow.md`](release/public-agpl-release-workflow.md) — building a publishable source tree
- [`public-export/README.md`](public-export/README.md) — sanitized **`AGENTS.md`** / orientation templates for **`/update-public-export`**
- [`release/dev-to-main-clean-promotion-workflow.md`](release/dev-to-main-clean-promotion-workflow.md) — promoting integration work onto the release line
- [`RELEASE-MANUAL-CHECKLIST.md`](RELEASE-MANUAL-CHECKLIST.md) — manual sign-off rows referenced by coverage matrices and release workflows

## Testing (reference)

- [`../tests/integration/README.md`](../tests/integration/README.md) — live stack integration tests

## Knowledge graph

- Protocol + public/private boundary narrative: **[`decisions/002-graph-store-falkordb.md`](decisions/002-graph-store-falkordb.md)**
- Compose / capability-service patterns (including optional HTTP KG bridges): **[`extending/extending-the-stack.md`](extending/extending-the-stack.md)**

## Lumogis Web and PWA

- [`../clients/lumogis-web/README.md`](../clients/lumogis-web/README.md) — SPA, codegen, production behind Caddy, Playwright
- [`../clients/lumogis-web/src/pwa/README.md`](../clients/lumogis-web/src/pwa/README.md) — service worker, Web Push, offline UX boundaries
- Optional Speaches STT Compose overlay: **`docker-compose.stt.yml`** and the composition table in the root **`README.md`**

## Lumogis Search (desktop overlay)

- [`../clients/lumogis-search/README.md`](../clients/lumogis-search/README.md) — AGPL Tauri 2 overlay for memory search against your household server (**ADR 080**–**083**: export, boundary, public CI, Persona A distribution; **ADR 089** Hub handoff; **ADR 090** system tray; **ADR 091** persona-aware Settings; **ADR 096** cold-start library resync on bundled Hub/Server)

## Extending and contributing

- [`extending/extending-the-stack.md`](extending/extending-the-stack.md) — compose overlays, capability services, adapters/plugins
- [`../CONTRIBUTING.md`](../CONTRIBUTING.md) — contributor setup and expectations; beginners onboarding via **`CONTRIBUTING-BEGINNERS.md`** export (**ADR 084**)
- [`extending/examples/example_plugin/`](extending/examples/example_plugin/) — minimal plugin template
- [`../AGENTS.md`](../AGENTS.md) — coding-agent routing and guardrails (read with [`LUMOGIS_AGENT_ORIENTATION.md`](LUMOGIS_AGENT_ORIENTATION.md))

## Archive

- Docs librarian inventory and daily reports: [`_librarian/`](_librarian/) (when present in your checkout)
