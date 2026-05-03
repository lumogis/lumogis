# Documentation index

Last reviewed: 2026-05-02  
Verified against commit: 98f02b1

## Product and operators

- [`LUMOGIS_REFERENCE_MANUAL.md`](LUMOGIS_REFERENCE_MANUAL.md) — consolidated operator and contributor reference
- [Repository root `README.md`](../README.md) — product overview and quickstart (Lumogis Web + Caddy + Core)
- [`connect-and-verify.md`](connect-and-verify.md) — end-to-end stack runbook
- [`dev-cheatsheet.md`](dev-cheatsheet.md) — Docker, Compose, day-to-day commands
- [`troubleshooting.md`](troubleshooting.md)
- [`gpu-setup.md`](gpu-setup.md)
- [`connector-credentials.md`](connector-credentials.md)
- [`per-user-export-format.md`](per-user-export-format.md)
- [`architecture/lumogis-speech-to-text-foundation-plan.md`](architecture/lumogis-speech-to-text-foundation-plan.md) — optional Speaches/STT Compose overlay (`docker-compose.stt.yml`), operator verification notes

## Architecture

- [`../ARCHITECTURE.md`](../ARCHITECTURE.md) — Core structure, Caddy routing, MCP/capability registry
- [`architecture/`](architecture/) — plans, closeout reviews, plugin imports, tool vocabulary
- [`decisions/`](decisions/) — ADRs (`001`–`032`, `DEBT.md`)

## Testing

- [`testing/automated-test-strategy.md`](testing/automated-test-strategy.md) — CI, pytest layers, integration, web, KG, Playwright
- [`../tests/integration/README.md`](../tests/integration/README.md) — live stack integration tests

## Knowledge graph

- [`kg_reference.md`](kg_reference.md) — technical KG reference (in-process vs `lumogis-graph` service mode)
- [`kg_operations_guide.md`](kg_operations_guide.md) — operator-facing KG concepts and runbook

## Lumogis Web and PWA

- [`../clients/lumogis-web/README.md`](../clients/lumogis-web/README.md) — SPA, codegen, production behind Caddy, Playwright
- [`../clients/lumogis-web/src/pwa/README.md`](../clients/lumogis-web/src/pwa/README.md) — service worker, Web Push, offline UX boundaries

## Extending and contributing

- [`extending-the-stack.md`](extending-the-stack.md) — compose overlays, capability services, adapters/plugins
- [`../CONTRIBUTING.md`](../CONTRIBUTING.md) — contributor setup and expectations
- [`examples/example_plugin/`](examples/example_plugin/) — minimal plugin template
- [`maintainers.md`](maintainers.md) — maintainer-facing publishing notes (hygiene / public tree)

## Maintainer-only material (private repository)

The following exist on the **private** monorepo only and are **omitted** from the public AGPL export (`scripts/check-public-export.sh`): maintainer release workflow under `docs/release/`, internal inventories under `docs/_librarian/`, and `docs/private/`. Do not rely on those paths in contributions meant for the upstream public tree.
