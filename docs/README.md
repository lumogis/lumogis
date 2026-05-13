# Documentation Index

## Documentation structure

| Directory | Contents |
|-----------|----------|
| `guides/` | Operational guides for self-hosters |
| `extending/` | Guides for contributors and plugin authors |
| `architecture/` | Implementation architecture notes |
| `decisions/` | Architecture Decision Records (ADRs) |
| `development/` | Local development setup |
| `testing/` | Test strategy |
| `release/` | Release and export workflows (maintainers) |
| `private/` | Private maintainer material (not in public export) |

> Status: Active
> Last reviewed: 2026-05-08
> Verified against commit: **e23f9d0**
> Owner: Docs Librarian

## Canonical docs

- [`LUMOGIS_CONTEXT_PACK.md`](LUMOGIS_CONTEXT_PACK.md) — canonical repo-evidence onboarding for Cursor, ChatGPT, Claude, and other assistants (maintained by **`/update-context-pack`**; do not duplicate elsewhere)
- [`LUMOGIS_REFERENCE_MANUAL.md`](LUMOGIS_REFERENCE_MANUAL.md) — consolidated operator and contributor reference
- [Repository root `README.md`](../README.md) — product overview and quickstart (Lumogis Web + Caddy + Core)

## Architecture

- [`../ARCHITECTURE.md`](../ARCHITECTURE.md) — Core structure, Caddy routing, MCP/capability registry
- [`architecture/plugin-imports.md`](architecture/plugin-imports.md) — plugin import conventions
- [`architecture/tool-vocabulary.md`](architecture/tool-vocabulary.md) — LLM vs MCP tool naming

## Decisions / ADRs

- [`decisions/`](decisions/) — numbered ADRs and [`decisions/DEBT.md`](decisions/DEBT.md)

## Implementation plans

- [`decisions/`](decisions/) and the public architecture references above — primary shipped intent for the tree
- [`testing/automated-test-strategy.md`](testing/automated-test-strategy.md) — CI, pytest layers, integration, web, KG, Playwright

## Operations

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

- Design and boundaries: [`decisions/011-lumogis-graph-service-extraction.md`](decisions/011-lumogis-graph-service-extraction.md) and related graph ADRs
- Service operator reference: [`../services/lumogis-graph/README.md`](../services/lumogis-graph/README.md)

## Lumogis Web and PWA

- [`../clients/lumogis-web/README.md`](../clients/lumogis-web/README.md) — SPA, codegen, production behind Caddy, Playwright
- [`../clients/lumogis-web/src/pwa/README.md`](../clients/lumogis-web/src/pwa/README.md) — service worker, Web Push, offline UX boundaries
- Optional Speaches STT Compose overlay: **`docker-compose.stt.yml`** and the composition table in the root **`README.md`**

## Extending and contributing

- [`extending/extending-the-stack.md`](extending/extending-the-stack.md) — compose overlays, capability services, adapters/plugins
- [`../CONTRIBUTING.md`](../CONTRIBUTING.md) — contributor setup and expectations
- [`extending/examples/example_plugin/`](extending/examples/example_plugin/) — minimal plugin template
- [`../AGENTS.md`](../AGENTS.md) — coding-agent routing and guardrails (read with [`LUMOGIS_CONTEXT_PACK.md`](LUMOGIS_CONTEXT_PACK.md))

## Documentation inventory

Periodic audits and the machine-readable inventory live under [`_librarian/`](_librarian/).
