# Documentation Index

> Last reviewed: 2026-11-04
> Verified against commit: 738a1965
> Owner: Docs Librarian

## Canonical docs

- [`LUMOGIS_REFERENCE_MANUAL.md`](LUMOGIS_REFERENCE_MANUAL.md) — consolidated operator and contributor reference
- [`public-export/LUMOGIS_AGENT_ORIENTATION.md`](public-export/LUMOGIS_AGENT_ORIENTATION.md) — concise onboarding for AI assistants and contributors (export template; copied to `docs/LUMOGIS_AGENT_ORIENTATION.md` in the AGPL tree)
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
- MCP epic (**ADRs 126–221** on **`dev`**): write surface (**128**–**129**), token scopes (**130**), capability reasons (**126**), Origin guard (**132**), TEMPR **`recall`** read fusion (**133**, **LUM-295**), coding code-structure ingest (**134**, **LUM-301**) + reindex serialization (**141**), Cursor stdio bridge (**135**, **LUM-292**), entitlement issuer (**136**, **LUM-263**), Cursor integration smoke (**137**, **LUM-299**), bank isolation (**138**, **LUM-293**), multi-bank export/purge (**139**, **LUM-544**), admin panel completion (**140**, **LUM-520** / **LUM-545**); household Server loopback auth (**142**, **LUM-473** Chunk A) + default-user remap coverage (**152**, **LUM-473** follow-up); web lint gate (**143**, **LUM-546**); Search Wayland re-summon (**144**, **LUM-455**); operated home-dns hardening + deploy CI (**145**, **LUM-508**); feature flags (**146**, **LUM-126**) + read-only admin panel (**160**, **LUM-573**); cloud LLM privacy mode + biography conflicts + admin Software updates card (**147**, **LUM-194** / **LUM-514** / **LUM-524**); LLM circuit breaker (**148**, **LUM-125**); macOS/Windows Core venv (**149**, **LUM-468**); Graphiti build-vs-wrap verdict (**150**, **LUM-558**); temporal KG validity (**151**, **LUM-104**); tethered egress guard (**153**, **LUM-553**); household invite flow + concurrent write isolation + egress PoC (**154**, **LUM-186** / **LUM-358** / **LUM-570** — three **154** prefixes); household document sharing content projection (**155**, **LUM-157**); member audit log (**156-lum-197**, **LUM-197**); two-user Qdrant isolation test (**156-lum-307**, **LUM-307**); conversation persistence gap-fill (**156-lum-395**, **LUM-395**); post-ship sharing fixes (**157**, **LUM-157**/**LUM-577**); graph-aware entity sharing on document publish (**158**, **LUM-586**); BGE reranker admin UI (**159**, **LUM-159**); capture inbox + archive (**161**, **LUM-606** / **LUM-607**); default-deny KG exposure (**162**, **LUM-559**); connector risk profiling (**163**, **LUM-355**); email send-action trust (**164**, **LUM-229**); paperless KG mapping v0.3 (**165**, **LUM-283**); injection scanners mechanism (**166**, **LUM-361** / **LUM-362**); tombstone re-ingest path guard (**167**, **LUM-500**); safety playground (**168**, **LUM-141**); capability invoke contract v1 (**169**, **LUM-41**); plugin security model programme (**170**, **LUM-507**); capability permission scopes (**171**, **LUM-612**); capability sandbox + egress gate (**172**, **LUM-613**); container-network egress containment (**173**, **LUM-618** / ops **LUM-621**); email IMAP signal source + local mail classification (**174**, **LUM-170**); email arc (**175**–**197**): user-editable taxonomy (**175**, **LUM-629**); IMAP write-back (**176**/**183**); poll cursor safety (**177**); IMAP egress containment (**178**, **LUM-625**); process-wide egress ceiling (**179**, **LUM-571**); proactive surfacing + morning brief (**180**, **LUM-648**); feedback→relevance (**181**, **LUM-649**); email setup v2 (**182**); label write-back (**183**); spam handling (**184**, **LUM-646**); SMTP send (**185**, **LUM-650**); reply threading (**186**, **LUM-651**); send UX + approvals (**187**, **LUM-652**); voice dictation (**188**, **LUM-653**); compose refine (**189**, **LUM-684**); OAuth Outlook connect (**190**, **LUM-687**); unsubscribe + sender suppression (**191**, **LUM-697**); paperless mailbox overlap consent on attachment filing rules (**192**, **LUM-728**); email attachment surface routing + reachability guard (**193**, **LUM-742**); integration-tree membership rule (**194**, **LUM-753**); mailbox poll health at connect phase (**195**, **LUM-633**); OAuth refresh failure kinds (**196**, **LUM-757**); SMTP opt-in error mapping (**197**, **LUM-759**); post-setup send enablement (**198**, **LUM-769**); plaintext mailbox-host enumeration (**199**, **LUM-634**); mailbox IMAP host invariant (**200**, **LUM-773**); action proposal claim semantics (**201**, **LUM-762**); failed-send recovery (**202**, **LUM-793**); Linear evidence-index retirement (**203**, **LUM-300**); pre-main promotion gate (**204**, **LUM-802**); email core-vs-plugin (**205**, **LUM-655**); email intelligence commercial line (**206**, **LUM-809**); email body read view (**207**, **LUM-810**); text-only IMAP fetch (**208**, **LUM-814**); forgot-password reset (**209**, **LUM-65**); Postgres connection pooling (**210**, **LUM-816**); email search stores body text + local search (**211**, **LUM-819** / **LUM-827** numeric + **LUM-826** thread strip); shared Message-ID locate (**212**); unified read vs per-mailbox act (**213**, **LUM-821**); sending address resolved on demand (**214**, **LUM-831**); source edit + poll health (**215**, **LUM-837**); RSS/page outbound policy (**216**, **LUM-838**); per-user source creation (**217**, **LUM-839**); connector test-connection probe (**218**, **LUM-834** chunk A); connector enable toggle + field presence (**219**, **LUM-834** chunk B); autonomy surface — what Lumogis may do (**220**, **LUM-849**); paperless onboarding offer on **`/me/sources`** (**221**, **LUM-282**)

## Implementation plans

- [`decisions/`](decisions/) and the public architecture references above — primary shipped intent for the tree
- [`testing/automated-test-strategy.md`](testing/automated-test-strategy.md) — CI, pytest layers, integration, web, KG, Playwright; **`dev` push CI** (**LUM-788**); **`scripts/release-preflight.sh`**; **`make debug`** / **`make test-list`** and [`scripts/debug/README.md`](../scripts/debug/README.md) (**ADR 078**, LUM-377); opt-in Ollama mutation e2e (**ADR 087**, LUM-450)

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
- [`release/dev-to-main-clean-promotion-workflow.md`](release/dev-to-main-clean-promotion-workflow.md) — promoting integration work onto the release line; **`scripts/release-preflight.sh`** pre-promotion gate (**LUM-788**); **`gate:pre-main`** / **`pre_main_gate.mjs`** (**ADR 204**, **LUM-802**); **`make main-dev-drift-check`** ratchet (**LUM-789**)
- [`RELEASE-MANUAL-CHECKLIST.md`](RELEASE-MANUAL-CHECKLIST.md) — manual sign-off rows referenced by coverage matrices and release workflows

## Testing (reference)

- [`testing/automated-test-strategy.md`](testing/automated-test-strategy.md) — layered commands, CI gates, and **`orchestrator/tests/integration/`** membership rule (**ADR 194**, **LUM-753**)
- [`testing/paperless-live-fixture.md`](testing/paperless-live-fixture.md) — live paperless-ngx fixture lane (**LUM-309**); **`make compose-test-paperless`**
- [`../tests/integration/README.md`](../tests/integration/README.md) — live stack integration tests (repo-root HTTP lane)

## Knowledge graph

- Protocol + public/private boundary narrative: **[`decisions/002-graph-store-falkordb.md`](decisions/002-graph-store-falkordb.md)**
- Compose / capability-service patterns (including optional HTTP KG bridges): **[`extending/extending-the-stack.md`](extending/extending-the-stack.md)**

## Lumogis Web and PWA

- [`../clients/lumogis-web/README.md`](../clients/lumogis-web/README.md) — SPA, codegen, production behind Caddy, Playwright; document library (**LUM-160**, **ADR 101**) with ingest job progress (**LUM-511**, **ADR 110**), unified library over uploads/paperless/email origins (**LUM-724**), household document sharing (**LUM-157**, **ADR 155**) and graph-aware entity cascade (**LUM-586**, **ADR 158**), entity sharing (**LUM-581**), document chat (**LUM-175**, **ADR 101-lum-175**); email triage **`/email`** (**ADRs 175–219**, **LUM-642**–**729** arc + **LUM-728** overlap + **LUM-742** attachment routing + **LUM-633** mailbox health + **LUM-757** OAuth refresh kinds + **LUM-759** SMTP opt-in errors + **LUM-769** post-setup send + **LUM-773** host invariant + **LUM-819** inline search + **LUM-821** mailbox chip + **LUM-830** compose-new + **LUM-831** sending address); **`/me/sources`** for paperless/RSS/page (**LUM-837** / **LUM-839**, **ADRs 215** / **217**); connector test + enable toggle (**LUM-834**, **ADRs 218** / **219**); **What Lumogis may do** autonomy page (**LUM-849**, **ADR 220**); first-run paperless offer on **`/me/sources`** (**LUM-282**, **ADR 221**); action proposals (**ADR 201**, **LUM-762**); recoverable failed sends on Approvals (**LUM-793**, **ADR 202**; Playwright **LUM-797**); capture inbox + archive (**LUM-606** / **LUM-607**, **ADR 161**; live e2e **LUM-608**, matrix **2.3.25**); member audit log (**LUM-197**, **ADR 156-lum-197**); conversation persistence citations (**LUM-395**, **ADR 156-lum-395**); BGE reranker admin (**LUM-159**, **ADR 159**); feature-flags panel (**LUM-573**, **ADR 160**); sparse Qdrant cleanup on re-ingest (**ADR 109**, amendment **ADR 111**); loading/error UX + non-admin health banners (**LUM-212** / **211** / **512**, **ADR 107**); household RBAC (**LUM-334**, **ADR 112**) and admin panel completion (**LUM-520** / **LUM-545**, **ADR 113** + **ADR 140**); invite **`allows_shared`** toggle (**LUM-577**); post-ship sharing hardening (**ADR 157**); launch demo harness **`make web-demo`** (**LUM-181**); MCP token scope selector (**LUM-527**, **ADR 130**)
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
- [`../AGENTS.md`](../AGENTS.md) — coding-agent routing and guardrails (read with [`public-export/LUMOGIS_AGENT_ORIENTATION.md`](public-export/LUMOGIS_AGENT_ORIENTATION.md))

## Archive

- Docs librarian inventory and daily reports: [`_librarian/`](_librarian/) (when present in your checkout)
