# Documentation Index

> Status: Active
> Last reviewed: 2026-05-06
> Doc index from librarian pass at **5dfeb7c**; merged into **dev** after ADR/harness and admin-diagnostics work. Use **`git rev-parse HEAD`** for the repository tip.
> Owner: Docs Librarian

## Canonical docs

- [`LUMOGIS_CONTEXT_PACK.md`](LUMOGIS_CONTEXT_PACK.md) — canonical repo-evidence onboarding for Cursor, ChatGPT, Claude, and other assistants (maintained by **`/update-context-pack`**; do not duplicate elsewhere)
- [`LUMOGIS_REFERENCE_MANUAL.md`](LUMOGIS_REFERENCE_MANUAL.md) — consolidated operator and contributor reference
- [Repository root `README.md`](../README.md) — product overview and quickstart (Lumogis Web + Caddy + Core)
- *(End-to-end stack runbook and Docker/Compose command reference are not tracked in-tree — keep local copies if you use them.)*

## Architecture

- [`../ARCHITECTURE.md`](../ARCHITECTURE.md) — Core structure, Caddy routing, MCP/capability registry
- [`architecture/`](architecture/) — plans, closeout reviews, plugin imports, tool vocabulary
- [`architecture/product-roadmap-reconciliation-audit-2026-05-02.md`](architecture/product-roadmap-reconciliation-audit-2026-05-02.md) — read-only reconciliation snapshot (2026-05-02)

## Decisions / ADRs

- [`decisions/`](decisions/) — ADRs `001`–`033` and [`decisions/DEBT.md`](decisions/DEBT.md)

## Implementation plans

- [`architecture/`](architecture/) — phased web, self-hosted remediation, STT, capability contract plans
- [`testing/automated-test-strategy.md`](testing/automated-test-strategy.md) — CI, pytest layers, integration, web, KG, Playwright
- Skill-managed IDE plans (if present) are out of tree; shipped intent is under `docs/architecture/` and `docs/decisions/`

## Operations

- [`troubleshooting.md`](troubleshooting.md)
- [`gpu-setup.md`](gpu-setup.md)
- [`connector-credentials.md`](connector-credentials.md)
- [`per-user-export-format.md`](per-user-export-format.md)
- [`release/public-agpl-release-workflow.md`](release/public-agpl-release-workflow.md) — public snapshot process (private monorepo)
- [`release/dev-to-main-clean-promotion-workflow.md`](release/dev-to-main-clean-promotion-workflow.md) — private promotion (private monorepo)

## Testing (reference)

- [`../tests/integration/README.md`](../tests/integration/README.md) — live stack integration tests

## Knowledge graph

- [`kg_reference.md`](kg_reference.md) — technical KG reference (in-process vs `lumogis-graph` service mode)
- [`kg_operations_guide.md`](kg_operations_guide.md) — operator-facing KG concepts and runbook

## Lumogis Web and PWA

- [`../clients/lumogis-web/README.md`](../clients/lumogis-web/README.md) — SPA, codegen, production behind Caddy, Playwright
- [`../clients/lumogis-web/src/pwa/README.md`](../clients/lumogis-web/src/pwa/README.md) — service worker, Web Push, offline UX boundaries
- [`architecture/lumogis-speech-to-text-foundation-plan.md`](architecture/lumogis-speech-to-text-foundation-plan.md) — optional Speaches/STT Compose overlay (`docker-compose.stt.yml`)

## Extending and contributing

- [`extending-the-stack.md`](extending-the-stack.md) — compose overlays, capability services, adapters/plugins
- [`../CONTRIBUTING.md`](../CONTRIBUTING.md) — contributor setup and expectations
- [`examples/example_plugin/`](examples/example_plugin/) — minimal plugin template
- [`maintainers.md`](maintainers.md) — maintainer-facing publishing notes (hygiene / public tree)
- [`../AGENTS.md`](../AGENTS.md) — coding-agent routing and guardrails (read with [`LUMOGIS_CONTEXT_PACK.md`](LUMOGIS_CONTEXT_PACK.md))

## Archive

- [`archive/open-core-repository-workflow.md`](archive/open-core-repository-workflow.md) — superseded dual-repo flow (see [`release/public-agpl-release-workflow.md`](release/public-agpl-release-workflow.md))

## Maintainer-only material (private repository)

The following exist on the **private** monorepo only and are **omitted** from the public AGPL export (`scripts/check-public-export.sh`): maintainer release workflow under `docs/release/`, internal inventories under `docs/_librarian/`, and `docs/private/`. Do not rely on those paths in contributions meant for the upstream public tree.
