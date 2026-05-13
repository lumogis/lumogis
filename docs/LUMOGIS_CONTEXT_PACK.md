# Lumogis Context Pack

Concise onboarding for AI assistants (**Cursor**, **ChatGPT**, **Claude**). Summarises **repo evidence only** — not Linear live state.

---

## Last refreshed

| Field | Value |
| --- | --- |
| **Date/time** | 2026-05-11 (context-pack refresh; no UTC clock in body) |
| **lumogis-app** | branch **`main`**; **2026-05-11** refresh (**`guides/`** + **`extending/`** docs layout, **ARCHITECTURE.md** code-navigation section, context pack aligned with **`dev`**). Verify tip with **`git log -1`**. **`origin/dev`** may run ahead — see **`AGENTS.md`**. |
| **lumogis-devtools** | branch **`main`**, commit **`2543696`** (short; skills, reports, Product OS tooling) |
| **Public / lumogis-public** | Not verified in this pass (optional checkout / `upstream/main`; see **`AGENTS.md`**) |

**Evidence sources consulted:**

- `AGENTS.md` (lumogis-app)
- `README.md`, `ARCHITECTURE.md` (**includes § *Finding your way around the code* — diagram-aligned directory map**), `CHANGELOG.md`, `docs/capabilities.md`, `docs/LUMOGIS_REFERENCE_MANUAL.md` (lumogis-app)
- `docs/README.md` (**documentation index**: **`guides/`** for self-hosted ops; **`extending/`** for contributors · plugin **`examples/`**)
- `docs/architecture/*.md` (public supplements — e.g. **`plugin-imports.md`**, **`tool-vocabulary.md`**); maintainer planning often under **`docs/private/architecture/`** (may be export-stripped from public trees)
- `docs/decisions/*.md` (**~37** markdown files: numbered **001–035**, **`DEBT.md`**, two distinct **`034-*.md`** filenames — not individually re-read in full)
- `Makefile` (lumogis-app) — test / web / compose / KG targets (**`dev`** vs **`main`** may differ; **`main`** may carry fuller release gates — see **`docs/testing/automated-test-strategy.md`**)
- `cursor/reports/linear-issues-export.json` — **`exportedAt`: `2026-05-08T21:33:35.887Z`**, team **LUM**, **110** issues (local snapshot)
- `cursor/reports/linear-roadmap-priority-dashboard-2026-05-03.md` (curated narrative + scoring tables; header ties to export era — may lag fresher JSON **§5–§6**)
- `cursor/reports/linear-id-map-2026-05-03.csv` (traceability snapshot)
- `cursor/backlog/linear-operating-model.md` (Product OS taxonomy; devtools path via **`.cursor`** symlink)
- `.cursor/skills/*/SKILL.md` (skill set; **`/publish-private-main-to-public`** includes **CHANGELOG** + **`docs/capabilities.md`** gates before public export)

**Freshness / uncertainty:**

- **Linear export** is a **local snapshot** (**2026-05-08**); **authoritative issue state** is **Linear** + a fresh export. Refresh (human-run, from **lumogis-devtools**): `node scripts/linear/linear_import.mjs --export-issues`. Optional: `node scripts/linear/drift_check.mjs`.
- **Roadmap dashboard** file date (**2026-05-03**) may lag the JSON export above — reconcile scores vs **`linear-issues-export.json`** after refresh.
- **`docs/release/public-release-log.md`** was **not present** in this **lumogis-app** tree at refresh time; use **`docs/release/*`** when present and release skills.

---

## What Lumogis is

- **Self-hosted, local-first, privacy-first** household / personal AI: data and indexes stay on hardware you control (Docker Compose, **AGPL-3.0-only** product line per **README** / **ADR 032**).
- **Core** = **FastAPI orchestrator**; **Lumogis Web** = first-party SPA behind **Caddy** (same-origin); **LibreChat** = optional legacy OpenAI-style UI (compose profile), not the primary multi-user surface (**reference manual** / **ADR 012**).
- **Ask / Do** safety model for actions/tools; auditability and connector/credential boundaries are first-class (**ADR 006**, credential ADRs **018**, **027**, **029**, etc.).
- **Not** a Lumogis-operated SaaS; positioning is **family/household LAN** and operator-controlled deployment.
- **Public vs private**: the **public** tree is an **export snapshot**, not a byte mirror of private history (**`AGENTS.md`**).

---

## Repository model

| Repo / path | Role |
| --- | --- |
| **lumogis-app** | Product: application, tests, Docker, shipping paths |
| **lumogis-devtools** | Cursor skills, Product OS tooling, Linear scripts, **`.cursor/reports`**, registries |
| **lumogis-app/.cursor** | Symlink → **lumogis-devtools/cursor** (plans, skills, rules resolve here) |
| **lumogis-public** / **upstream/main** | Public **AGPL** export line when present (**not** the same lineage as private `main` — **`AGENTS.md`**) |

---

## Branch and release model

| Line | Typical role |
| --- | --- |
| **lumogis-app `dev`** | Active integration |
| **lumogis-app `origin/main`** | Clean **private** release line |
| **Public `main` / `upstream/main`** | **Exported** AGPL snapshot |
| **lumogis-devtools `main`** | Internal tooling |

- **`dev` and private `main` may diverge.** **Public** and **private** `main` **may differ by commit hash**.
- **Scoped promotion** (`README`/docs/assets) is the default posture for **`dev` → private `main`** — not wholesale merge (**`/prepare-private-release-from-dev`**).
- **`/publish-private-main-to-public`**: generate export via project scripts; **never** raw-push private `main` to public. **Hard gates** before export (private **`main`**): update **`CHANGELOG.md`** for the release and sync **`docs/capabilities.md`** with shipped behaviour; ADRs and **`docs/LUMOGIS_REFERENCE_MANUAL.md`** stay aligned at implementation time — see skill body.
- For live topology: **`/cleanup-and-audit-branches`** (do not embed branch tables here).

---

## Architecture snapshot *(high level)*

| Area | Notes |
| --- | --- |
| **Core** | FastAPI orchestrator — services, adapters, plugins, signals, actions, routes (**see **`ARCHITECTURE.md`** § code navigation + dependency diagram**) |
| **Lumogis Web** | Primary SPA; **LibreChat** optional |
| **Data** | **Postgres** (metadata, audit, …), **Qdrant** (vectors); **Ollama** default local embed/LLM |
| **Graph / KG** | **FalkorDB** optional; **in-process** plugin vs **`lumogis-graph`** (**`GRAPH_MODE`**: **`inprocess`** / **`service`** / **`disabled`**); **`query_graph`** proxy fail-closed without explicit Core secret/opt-in in **`service`** mode (**ADR 035**, **CHANGELOG**) |
| **Capabilities / plugins** | Optional packages; HTTP manifests, bearer trust (**ADR 010**, **011**); **mock-capability** for contract smoke |
| **MCP** | Core **`/mcp/`** surface; per-user opaque bearer tokens when **`AUTH_ENABLED`** (**ADR 017**) |
| **Tool catalog** | Read-only unified catalog; **`LUMOGIS_TOOL_CATALOG_ENABLED`** defaults **off**; when **on**, healthy OOP capability tools may merge into the LLM tool list (**reference manual §9**) |
| **Capture / STT** | **Shipped MVP**: **`/capture`** QuickCapture, **`/api/v1/captures`** ledger (attachments, transcribe hook, index); **`POST /api/v1/voice/transcribe`** when STT enabled (**ADR 031**). Semantic search across captures vs ordinary **documents** remains uneven (**reference manual §13 / §17**) |
| **Credentials** | Per-user, household, and instance-system tiers; encrypted stores; connector Ask/Do **per user** (**ADR 024**, **027**, …) |
| **Mobile / PWA / Web Push** | **Partial MVP shipped**: responsive mobile UX, **PWA** manifest + service worker (bounded caching — **not** full offline product), **Web Push** opt-in + subscription plumbing; generic push for **every** connector/action outcome category **not** claimed shipped (**reference manual §13**, **ADR 030**) |

---

## Product OS and Linear workflow

- **Linear** = active **backlog and status** surface; **repo/devtools** files = **durable evidence**.
- **Planned work:** `/create-plan` → `/review-plan` → implement → `/verify-plan` → `/navigator drift` → `/linear-update` (when Thomas applies closure).
- **Unplanned shipped work:** `/record-retro` → `/navigator drift` → `/linear-update`.
- **`/linear-update`**: only skill that **intentionally mutates** Linear — and **only** when Thomas explicitly asks, **one issue** at a time.
- **Actionable follow-ups** need **Linear outcomes** (not markdown-only backlog). **P0** blocks closure; **P1** needs explicit acceptance; **P2/P3** deferred only with recorded outcomes (**`AGENTS.md`**, **`/verify-plan`**).

---

## Cursor skills map *(one line each)*

| Skill | Role |
| --- | --- |
| `/navigator` | Read-only routing: local export/reports + skills (no Linear API) |
| `/explore` | Research, comparison, draft ADR, topic index |
| `/create-plan` | Implementation plan + testing plan; Linear-linked by default |
| `/review-plan` | Self-review, critique, arbitrate, status |
| `/verify-plan` | Verify implementation, ADR finalise, portfolio/topic updates, commit guidance |
| `/record-retro` | As-built record when work shipped without plan loop |
| `/linear-update` | Apply comment/status to **one** issue when Thomas requests |
| `/cleanup-and-audit-branches` | Branch topology / release-line audit |
| `/review-cursor-branches` | Review `origin/cursor/*` before merge to `dev` |
| `/prepare-private-release-from-dev` | Scoped **`dev` → private `main`** RC |
| `/publish-private-main-to-public` | Private `main` → **public export** (**CHANGELOG** + **`capabilities.md`** gates before push) |
| `/update-context-pack` | Maintains **this file** from evidence |

---

## Current roadmap / priority snapshot *(export-based; may be stale)*

**Do not treat as live Linear.** Prefer a fresh **`linear_import.mjs --export-issues`** run, then **`linear-issues-export.json`** + dashboard **§5–§6**.

**Local snapshot:** **`linear-issues-export.json`** — **110** issues, exported **2026-05-08**. **`linear-roadmap-priority-dashboard-2026-05-03.md`** — curated **§2 “Now”** and automated **§5** scores from an **older** export era; treat narrative and table tops as **indicative** until the dashboard is regenerated against the latest JSON.

**Illustrative themes** (same dashboard file; verify against fresh export): plan ↔ Linear linkage hygiene (**LUM-9** class), human-review / dedupe gates (**LUM-56**, **LUM-80** / **LUM-81** class), web umbrella (**LUM-44**), capability / compose / security hardening (**LUM-42**, **LUM-43** class), graph / MCP gateway items, graph-stats privacy debt (**LUM-23**).

**Cautions:** **`migration:needs-dedupe`** and **human-review** rows called out in dashboard **§7**; reconcile automated scores with maintainer judgement.

---

## Key hard rules

- No **secrets** in repo, chats, or this pack (no `.env` values, tokens, keys).
- No **`cursor/settings.json`**, `__pycache__`, `node_modules`, `.pytest_cache` in commits (unless explicitly intended).
- **No wholesale `dev` → `main`** unless Thomas requests **full dev promotion**.
- **No** pushing private `main` **directly** to public — **export scripts** only.
- **No** ad-hoc Linear mutation — **`/linear-update`** or approved scripts only.
- **No** markdown-only future work as system of record — **Linear outcomes** for actionable items.
- Prefer **small, scoped** changes; if unsure, **`/navigator`**.

---

## Verification commands *(typical; match touched paths)*

| Layer | Examples |
| --- | --- |
| **Orchestrator** | `make test` / `cd orchestrator && pytest …`; `make compose-test` (Docker) |
| **Integration** | `make compose-test-integration`, `make test-integration` (venv) |
| **Lint** | `make lint`, `make compose-lint` |
| **Web** | `make web-test`, `web-lint`, `web-build`, `web-e2e` (needs stack + env per README) |
| **KG** | `make test-kg`, `make compose-test-kg`, `make test-graph-parity` (slow) |
| **Product OS** | `node scripts/linear/drift_check.mjs` (devtools, when applicable); plan/exploration linkage per **`/verify-plan`** maintainer gates |
| **Release / public** | **`docs/testing/automated-test-strategy.md`** — targeted layers on **`dev`**; comprehensive **`make verify-public-rc-full`** (and targets it chains) on **private `main`** (targets exist as of LUM-225 / `ca83054`). **`docs/release/public-agpl-release-workflow.md`**, skills **`/prepare-private-release-from-dev`**, **`/publish-private-main-to-public`**. **`/verify-plan`** may require extra gates when touching Docker Compose policy paths — see skill Step 3. **GHCR images** (`lumogis-orchestrator`, `lumogis-web`) must be published **only** from `lumogis/lumogis` (public repo) `main`; `make verify-public-rc` must pass on private `main` before `/publish-private-main-to-public` runs. `publish-image.yml` in `lumogis-app` is a known temporary state pending LUM-225 Phase 3–5 completion. |

---

## New Chat Bootstrap Prompt

Copy-paste:

> I am working on Lumogis. Please use the attached/pasted **Lumogis Context Pack** as the current project context. Treat **Linear** as the active backlog, **repo/devtools files** as evidence, and route workflow advice through the documented **skills**. Do not assume **dev** / **main** / **public** histories are linear. My question is: …

---

## Source-of-truth pointers

- `AGENTS.md` — routing / guardrails
- `docs/LUMOGIS_CONTEXT_PACK.md` — **this** compact orientation (maintained by **`/update-context-pack`**)
- `docs/README.md` — documentation index (**`guides/`**, **`extending/`**, ADRs, release, **`private/`** pointers)
- `docs/guides/` — self-hoster ops (GPU, troubleshooting, connector credentials, export format, structured logging)
- `docs/extending/` — **`extending-the-stack.md`** + **`examples/example_plugin/`** template
- `docs/LUMOGIS_REFERENCE_MANUAL.md` — operator + contributor narrative
- `docs/capabilities.md` — short shipped-capability overview (public-facing)
- `CHANGELOG.md` — release history (public-facing; gated before public publish)
- `docs/testing/automated-test-strategy.md` — test layers, **dev** vs **`main`** / RC expectations
- `README.md`, `ARCHITECTURE.md` (repo root)
- `docs/architecture/` — public architecture supplements
- `docs/decisions/` — ADRs
- `docs/release/` — release notes / logs **when present**
- `cursor/backlog/linear-operating-model.md` — Product OS taxonomy (devtools)
- `cursor/reports/linear-issues-export.json` — local Linear snapshot
- `cursor/reports/linear-roadmap-priority-dashboard-*.md` — prioritisation dashboard
- `cursor/reports/linear-id-map-*.csv` — ID traceability
- `scripts/linear/README.md` (devtools) — import/drift tooling
- `.cursor/skills/*/SKILL.md` — full workflows

---

## Maintenance rules

- Refresh when **architecture**, **branch/release**, **Product OS**, **skills**, or **roadmap-facing** facts change materially.
- **Shrink** stale bullets; do not accumulate noise.
- **No** second backlog here — **Linear** owns actionable work.
- If an update implies new actionable work, record **Linear outcomes** per **`AGENTS.md`**.
