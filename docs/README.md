# Documentation Index

> Status: Active
> Last reviewed: 2026-05-27
> Verified against commit: 110e8cc
> Owner: Docs Librarian

## Canonical docs

- [`LUMOGIS_CONTEXT_PACK.md`](LUMOGIS_CONTEXT_PACK.md) — canonical repo-evidence onboarding for Cursor, ChatGPT, Claude, and other assistants (maintained by **`/update-context-pack`**; do not duplicate elsewhere)
- [`LUMOGIS_REFERENCE_MANUAL.md`](LUMOGIS_REFERENCE_MANUAL.md) — consolidated operator and contributor reference
- [`capabilities.md`](capabilities.md) — shipped capability narrative (kept in sync with releases; see public export / release skills)
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
| `private/` | Private maintainer material (not in public export) |

## Architecture

- [`../ARCHITECTURE.md`](../ARCHITECTURE.md) — Core structure, Caddy routing, MCP/capability registry
- [`architecture/plugin-imports.md`](architecture/plugin-imports.md) — plugin import conventions
- [`architecture/tool-vocabulary.md`](architecture/tool-vocabulary.md) — LLM vs MCP tool naming
- Maintainer-only plans and closeouts: [`private/architecture/`](private/architecture/) and [`private/archive/`](private/archive/) (may be omitted from public export trees—see release scripts)

## Decisions / ADRs

- [`decisions/`](decisions/) — numbered ADRs and [`decisions/DEBT.md`](decisions/DEBT.md)

## Implementation plans

- [`decisions/`](decisions/) and the public architecture references above — primary shipped intent for the tree
- [`testing/automated-test-strategy.md`](testing/automated-test-strategy.md) — CI, pytest layers, integration, web, KG, Playwright

## Operations

- [`deployment/quickstart.md`](deployment/quickstart.md) — first run from published **GHCR** images
- [`deployment/remote-access.md`](deployment/remote-access.md) — Tailscale Serve / Cloudflare Tunnel for secure off-LAN access
- [`guides/troubleshooting.md`](guides/troubleshooting.md)
- [`guides/gpu-setup.md`](guides/gpu-setup.md)
- [`guides/connector-credentials.md`](guides/connector-credentials.md)
- [`guides/per-user-export-format.md`](guides/per-user-export-format.md)
- [`guides/structured-logging.md`](guides/structured-logging.md)
- [`release/public-agpl-release-workflow.md`](release/public-agpl-release-workflow.md) — building a publishable source tree
- [`release/dev-to-main-clean-promotion-workflow.md`](release/dev-to-main-clean-promotion-workflow.md) — promoting integration work onto the release line

## Testing (reference)

- [`../tests/integration/README.md`](../tests/integration/README.md) — live stack integration tests

## Knowledge graph

- Protocol + public/private boundary narrative: **[`decisions/002-graph-store-falkordb.md`](decisions/002-graph-store-falkordb.md)**
- Compose / capability-service patterns (including optional HTTP KG bridges): **[`extending/extending-the-stack.md`](extending/extending-the-stack.md)**

## Lumogis Web and PWA

- [`../clients/lumogis-web/README.md`](../clients/lumogis-web/README.md) — SPA, codegen, production behind Caddy, Playwright
- [`../clients/lumogis-web/src/pwa/README.md`](../clients/lumogis-web/src/pwa/README.md) — service worker, Web Push, offline UX boundaries
- Optional Speaches STT Compose overlay: **`docker-compose.stt.yml`** and the composition table in the root **`README.md`**

## Extending and contributing

- [`extending/extending-the-stack.md`](extending/extending-the-stack.md) — compose overlays, capability services, adapters/plugins
- [`../CONTRIBUTING.md`](../CONTRIBUTING.md) — contributor setup and expectations
- [`extending/examples/example_plugin/`](extending/examples/example_plugin/) — minimal plugin template
- [`../AGENTS.md`](../AGENTS.md) — coding-agent routing and guardrails (read with [`LUMOGIS_CONTEXT_PACK.md`](LUMOGIS_CONTEXT_PACK.md))

## Archive

- Historical maintainer extractions and closeouts: [`private/archive/`](private/archive/)
- Docs librarian inventory and daily reports: [`_librarian/`](_librarian/)
