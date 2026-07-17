# Documentation Index

> Status: Active
> Last reviewed: 2026-07-17
> Verified against commit: 0688d8749
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
- MCP epic (**ADRs 126–173** on **`dev`**): write surface (**128**–**129**), token scopes (**130**), capability reasons (**126**), Origin guard (**132**), TEMPR **`recall`** read fusion (**133**, **LUM-295**), coding code-structure ingest (**134**, **LUM-301**) + reindex serialization (**141**), Cursor stdio bridge (**135**, **LUM-292**), entitlement issuer (**136**, **LUM-263**), Cursor integration smoke (**137**, **LUM-299**), bank isolation (**138**, **LUM-293**), multi-bank export/purge (**139**, **LUM-544**), admin panel completion (**140**, **LUM-520** / **LUM-545**); household Server loopback auth (**142**, **LUM-473** Chunk A) + default-user remap coverage (**152**, **LUM-473** follow-up); web lint gate (**143**, **LUM-546**); Search Wayland re-summon (**144**, **LUM-455**); operated home-dns hardening + deploy CI (**145**, **LUM-508**); feature flags (**146**, **LUM-126**) + read-only admin panel (**160**, **LUM-573**); cloud LLM privacy mode + biography conflicts + admin Software updates card (**147**, **LUM-194** / **LUM-514** / **LUM-524**); LLM circuit breaker (**148**, **LUM-125**); macOS/Windows Core venv (**149**, **LUM-468**); Graphiti build-vs-wrap verdict (**150**, **LUM-558**); temporal KG validity (**151**, **LUM-104**); tethered egress guard (**153**, **LUM-553**); household invite flow + concurrent write isolation + egress PoC (**154**, **LUM-186** / **LUM-358** / **LUM-570** — three **154** prefixes); household document sharing content projection (**155**, **LUM-157**); member audit log (**156-lum-197**, **LUM-197**); two-user Qdrant isolation test (**156-lum-307**, **LUM-307**); conversation persistence gap-fill (**156-lum-395**, **LUM-395**); post-ship sharing fixes (**157**, **LUM-157**/**LUM-577**); graph-aware entity sharing on document publish (**158**, **LUM-586**); BGE reranker admin UI (**159**, **LUM-159**); capture inbox + archive (**161**, **LUM-606** / **LUM-607**); default-deny KG exposure (**162**, **LUM-559**); connector risk profiling (**163**, **LUM-355**); email send-action trust (**164**, **LUM-229**); paperless KG mapping v0.3 (**165**, **LUM-283**); injection scanners mechanism (**166**, **LUM-361** / **LUM-362**); tombstone re-ingest path guard (**167**, **LUM-500**); safety playground (**168**, **LUM-141**); capability invoke contract v1 (**169**, **LUM-41**); plugin security model programme (**170**, **LUM-507**); capability permission scopes (**171**, **LUM-612**); capability sandbox + egress gate (**172**, **LUM-613**); container-network egress containment (**173**, **LUM-618** / ops **LUM-621**)

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
- [`public-export/README.md`](public-export/README.md) — sanitized **`AGENTS.md`** / orientation templates for **`/update-public-export`**; operator self-evaluation guide **`EVALUATION.md`** (**LUM-363**)
- [`release/dev-to-main-clean-promotion-workflow.md`](release/dev-to-main-clean-promotion-workflow.md) — promoting integration work onto the release line
- [`RELEASE-MANUAL-CHECKLIST.md`](RELEASE-MANUAL-CHECKLIST.md) — manual sign-off rows referenced by coverage matrices and release workflows

## Testing (reference)

- [`../tests/integration/README.md`](../tests/integration/README.md) — live stack integration tests

## Knowledge graph

- Protocol + public/private boundary narrative: **[`decisions/002-graph-store-falkordb.md`](decisions/002-graph-store-falkordb.md)**
- Compose / capability-service patterns (including optional HTTP KG bridges): **[`extending/extending-the-stack.md`](extending/extending-the-stack.md)**

## Lumogis Web and PWA

- [`../clients/lumogis-web/README.md`](../clients/lumogis-web/README.md) — SPA, codegen, production behind Caddy, Playwright; document library (**LUM-160**, **ADR 101**) with ingest job progress (**LUM-511**, **ADR 110**), household document sharing (**LUM-157**, **ADR 155**) and graph-aware entity cascade (**LUM-586**, **ADR 158**), entity sharing (**LUM-581**), document chat (**LUM-175**, **ADR 101-lum-175**); capture inbox + archive (**LUM-606** / **LUM-607**, **ADR 161**; live e2e **LUM-608**, matrix **2.3.25**); member audit log (**LUM-197**, **ADR 156-lum-197**); conversation persistence citations (**LUM-395**, **ADR 156-lum-395**); BGE reranker admin (**LUM-159**, **ADR 159**); feature-flags panel (**LUM-573**, **ADR 160**); sparse Qdrant cleanup on re-ingest (**ADR 109**, amendment **ADR 111**); loading/error UX + non-admin health banners (**LUM-212** / **211** / **512**, **ADR 107**); household RBAC (**LUM-334**, **ADR 112**) and admin panel completion (**LUM-520** / **LUM-545**, **ADR 113** + **ADR 140**); invite **`allows_shared`** toggle (**LUM-577**); post-ship sharing hardening (**ADR 157**); launch demo harness **`make web-demo`** (**LUM-181**); MCP token scope selector (**LUM-527**, **ADR 130**)
- [`../clients/lumogis-mcp/README.md`](../clients/lumogis-mcp/README.md) — AGPL stdio bridge for Cursor (**LUM-292**, **ADR 135**); **`make lumogis-cursor-install`**
- [`../clients/lumogis-web/src/pwa/README.md`](../clients/lumogis-web/src/pwa/README.md) — service worker, Web Push, offline UX boundaries
- Optional Speaches STT Compose overlay: **`docker-compose.stt.yml`** and the composition table in the root **`README.md`**

## Lumogis Search (desktop overlay)

- [`../clients/lumogis-search/README.md`](../clients/lumogis-search/README.md) — AGPL Tauri 2 overlay for memory search against your household server (**ADR 080**–**083**: export, boundary, public CI, Persona A distribution; **ADR 089** Hub handoff; **ADR 090** system tray; **ADR 091** persona-aware Settings; **ADR 096** cold-start library resync on bundled Hub/Server; **ADR 114** overlay GUI e2e harness; **ADR 144** Wayland re-summon via CLI `--toggle` + recovery hint — **LUM-455**, manual sign-off open)

## Extending and contributing

- [`extending/extending-the-stack.md`](extending/extending-the-stack.md) — compose overlays, capability services, adapters/plugins
- [`extending/capability-contract-v1.md`](extending/capability-contract-v1.md) — normative HTTP capability author guide (**LUM-241**; implements **ADR 169** / **LUM-41**)
- [`../CONTRIBUTING.md`](../CONTRIBUTING.md) — contributor setup and expectations; beginners onboarding via **`CONTRIBUTING-BEGINNERS.md`** export (**ADR 084**)
- [`extending/examples/example_plugin/`](extending/examples/example_plugin/) — minimal plugin template
- [`../AGENTS.md`](../AGENTS.md) — coding-agent routing and guardrails (read with [`LUMOGIS_AGENT_ORIENTATION.md`](LUMOGIS_AGENT_ORIENTATION.md))

## Archive

- Docs librarian inventory and daily reports: [`_librarian/`](_librarian/) (when present in your checkout)
