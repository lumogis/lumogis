# Lumogis Context Pack

Concise onboarding for AI assistants (**Cursor**, **ChatGPT**, **Claude**). Summarises **repo evidence only** — not Linear live state.

---

## Last refreshed

| Field | Value |
| --- | --- |
| **Date/time** | **2026-05-15** (`/update-context-pack`; **repo + devtools** evidence; **Linear export not re-run** — **`exportedAt`** below is reused from the on-disk snapshot) |
| **lumogis-app** | branch **`dev`**, tip **`55db434`** — post-**0.4.0** integration: e.g. **LUM-224** web Docker/npm-ci + **LUM-250** admin **204** verify (`6f3c568`), reference manual verification SHA header (`5c13137`), export hygiene (**`fix(export): strip parity and public-rc-stack compose overlays from public tree`**, `55db434`). Prior **`dev`** baseline included **LUM-249** RC gate merges (`60edc4c` and ancestors). |
| **lumogis-devtools** | branch **`main`**, tip **`94efec0`** — **LUM-183** Product OS artefacts + **`/merge-workflow`** append (`46c2707`, `94efec0`): archived plan/exploration, topics/follow-up, run logs |
| **agent / run-workflow** | **`agent/lum-183`** at **`37f0005`** — coordinated disclosure **`SECURITY.md`** + **`.github/SECURITY.md`**, **`docs/decisions/044-coordinated-vulnerability-disclosure-policy.md`**, reference manual cross-links (**not** merged into **`dev`** at this **`dev`** tip; merge is a separate gated step) |
| **Public / upstream** | **0.4.0** per **`docs/release/public-release-log.md`**: public **`lumogis/lumogis`** tip **`cdcc574…`** (not private lineage); see that file for private export source SHAs |

**Evidence sources consulted:**

- `AGENTS.md` (lumogis-app) — includes **verify-public-rc environment** (ufw/Docker **`DOCKER-USER`**, skip vs full gate, **`QDRANT_HOST_PORT`**)
- `README.md`, `ARCHITECTURE.md`, `CHANGELOG.md`, `docs/capabilities.md`, `docs/LUMOGIS_REFERENCE_MANUAL.md` (lumogis-app)
- `docs/README.md`, `docs/architecture/*.md`, `docs/release/public-release-log.md` (**0.4.0** maintainer record)
- `docs/decisions/*.md` on **`dev`** (includes **038** RELATES_TO, **039** injection sanitisation, **042** kg export boundary, **043** web Dockerfile/npm CI; **two distinct `034-*.md`** — collision noted in librarian/adrs). **ADR 044** (coordinated disclosure) exists on **`agent/lum-183`**, not on **`dev`** until merged.
- `Makefile` (lumogis-app) — test / web / compose / KG / **RC gates** (**`compose-policy-check`** runs mock overlay then **`docker-compose.ghcr.yml`**)
- **`cursor/reports/linear-issues-export.json`** — **gitignored** locally; **245** issues, **`exportedAt`:** **`2026-05-15T14:47:57.818Z`** (unchanged this pass). Status = **Linear**; refresh with **`node scripts/linear/linear_import.mjs --export-issues`** (devtools) when you need a dated snapshot
- `cursor/reports/linear-roadmap-priority-dashboard-2026-05-03.md` (§5–§6 auto-regenerated on export)
- `cursor/reports/linear-id-map-2026-05-03.csv`
- `cursor/backlog/linear-operating-model.md`
- `cursor/topics.md` (devtools) — **LUM-183** ⚠️ implemented with **P1** operator follow-ups (**PVR** + mailbox); plan archived under **`cursor/plans/archived/`**
- `orchestrator/config.py` — **`LUMOGIS_TOOL_CATALOG_ENABLED`** default **on** when unset (see **Conflict / drift** below vs reference manual)
- `node scripts/linear/drift_check.mjs` (**default scope** — **2** ERRORs: **PLAN_LINKAGE_DRIFT**, **EXPLORATION_LINKAGE_DRIFT**; **57** WARNs in this run)

**Freshness / uncertainty:**

- **`linear-issues-export.json`** is **`.gitignore`d** — teammates run **`--export-issues`** for matching timestamps.
- **Conflict / drift:** **`docs/LUMOGIS_REFERENCE_MANUAL.md`** may still mention tool catalog default **`false`** in places; **`orchestrator/config.get_tool_catalog_enabled()`** and **`CHANGELOG.md` [Unreleased] / [0.4.0]** say default **on** when unset — treat **code + changelog** as authoritative until the manual is fully aligned.
- **LUM-183:** Policy + **ADR 044** are on **`agent/lum-183`**; **`/merge-workflow`** can land them on **`dev`** when Thomas confirms **merge**. Operator evidence (**GitHub Private vulnerability reporting** on public repo + **`lumogis@pm.me`** monitoring) remains **P1** on the issue per **topics**.
- **Roadmap dashboard** §1–§4 / §7–§8 narrative is **curated** — reconcile with §5–§6 + JSON.

---

## What Lumogis is

- **Self-hosted, local-first, privacy-first** household / personal AI: data and indexes stay on hardware you control (Docker Compose, **AGPL-3.0-only** per **README** / **ADR 032**).
- **Core** = **FastAPI orchestrator**; **Lumogis Web** = first-party SPA behind **Caddy** (same-origin); **LibreChat** = optional legacy profile, not the primary surface (**ADR 012**).
- **Ask / Do** safety model for actions/tools; auditability and connector/credential boundaries are first-class (**ADR 006**, credential ADRs **018**, **027**, **029**, etc.).
- **Not** Lumogis-operated SaaS — positioning is **family/household LAN** and operator-controlled deployment.
- **Public vs private**: the **public** tree is an **export snapshot**, not a byte mirror of private history (**`AGENTS.md`**). **GHCR images** (`lumogis-orchestrator`, `lumogis-web`) are published **only** from `lumogis/lumogis` (public repo) `main` — not from private `lumogis-app` (**ADR 037**).

---

## Repository model

| Repo / path | Role |
| --- | --- |
| **lumogis-app** | Product: application, tests, Docker, shipping paths |
| **lumogis-devtools** | Cursor skills, Product OS tooling, Linear scripts, **`.cursor/reports`**, registries |
| **lumogis-app/.cursor** | Symlink → **lumogis-devtools/cursor** (plans, skills, rules resolve here) |
| **lumogis-public** / **upstream/main** | Public **AGPL** export line — **not** the same lineage as private `main`; **0.4.0** snapshot recorded in **`docs/release/public-release-log.md`** |

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
- **`/publish-private-main-to-public`**: generate export via project scripts; **never** raw-push private `main` to public. **Hard gates** before export: `make verify-public-rc` (smoke) or `make verify-public-rc-full` must pass on private `main`; **`CHANGELOG.md`** + **`docs/capabilities.md`** updated; ADRs and **`docs/LUMOGIS_REFERENCE_MANUAL.md`** aligned at implementation time.
- **GHCR publish** is triggered automatically on push to public `main` (and `v*` tags) via `.github/workflows/publish-image.yml` in `lumogis/lumogis` — **not** in `lumogis-app`. No `publish-image.yml` remains in `lumogis-app` (**ADR 037**).
- For live branch topology: **`/cleanup-and-audit-branches`**.

---

## Architecture snapshot *(high level)*

| Area | Notes |
| --- | --- |
| **Core** | FastAPI orchestrator — services, adapters, plugins, signals, actions, routes (see **`ARCHITECTURE.md`** § code navigation + dependency diagram) |
| **Ingest / prompt-injection hygiene** | **ADR 039** — `injection_sanitiser`, pattern YAML, `<retrieved_chunk>` / `<lumogis_injected_context>` scaffolding, ingest **`origin`** metadata, **`TOOL_CHAIN_CAP`**, reference manual §8 |
| **Lumogis Web** | Primary SPA; **LibreChat** optional (compose profile) |
| **Data** | **Postgres**, **Qdrant**, **Ollama** default local embed/LLM; **`QDRANT_HOST_PORT`** (default host publish **6334** in main compose; **`config/test.env.example`** uses **6335** for **`lumogis-test`**) |
| **Graph / KG** | **FalkorDB** optional; **in-process** vs **`lumogis-graph`** (**`GRAPH_MODE`**: `inprocess` / `service` / **`disabled`** default for fresh installs); `query_graph` proxy fail-closed without Core secret/opt-in in `service` mode (**ADR 035**). **ADR 038** — **`RELATES_TO`** projection direction |
| **Capabilities / plugins** | Optional packages; HTTP manifests, bearer trust (**ADR 010**, **011**); **mock-capability** for contract smoke |
| **MCP** | Core `/mcp/` surface; per-user opaque bearer tokens when `AUTH_ENABLED` (**ADR 017**) |
| **Tool catalog** | **`LUMOGIS_TOOL_CATALOG_ENABLED`**: **`config.py`** defaults **on** when unset — set **`false`** to disable merged capability tools (see **Last refreshed** if reference manual disagrees) |
| **Capture / STT** | `/capture`, `/api/v1/captures`, `POST /api/v1/voice/transcribe` when STT enabled (**ADR 031**) |
| **Credentials** | Per-user, household, instance-system tiers; connector Ask/Do per user (**ADR 024**, **027**, …) |
| **Mobile / PWA / Web Push** | Responsive UX, PWA + bounded SW caching, Web Push opt-in (**ADR 030**) |
| **GHCR / Docker images** | `ghcr.io/lumogis/lumogis-orchestrator`, `lumogis-web`; overlay **`docker-compose.ghcr.yml`**. Published from **`lumogis/lumogis`** only (**ADR 036**, **ADR 037**) |

---

## Product OS and Linear workflow

- **Linear** = active **backlog and status** surface; **repo/devtools** files = **durable evidence**.
- **Planned work:** `/create-plan` → `/review-plan` → implement → `/verify-plan` → **`/merge-workflow`** (when **`run-workflow`** deferred Step **12**) → `/navigator drift` → `/linear-update` (when Thomas applies closure).
- **Unplanned shipped work:** `/record-retro` → `/navigator drift` → `/linear-update`.
- **`/linear-update`**: only skill that **intentionally mutates** Linear — **one issue** at a time, explicit Thomas request only.
- **`/navigator sync`** (explicit): refresh export + percentile **priority** sync via **`linear_import.mjs`** (`LINEAR_API_KEY` in env) — not default Navigator.
- **Actionable follow-ups** need **Linear outcomes** (not markdown-only backlog). **P0** blocks closure; **P1** needs explicit acceptance; **P2/P3** deferred only with recorded outcomes (**`AGENTS.md`**, **`/verify-plan`**).
- **Drift check:** `node scripts/linear/drift_check.mjs` — plan/exploration linkage **ERROR**s + markdown WARNs (this refresh: **2** ERRORs as above).

---

## Cursor skills map *(one line each)*

| Skill | Role |
| --- | --- |
| `/navigator` | Read-only routing from local export/reports; **`/navigator sync`** when Thomas refreshes export + syncs priorities |
| `/explore` | Research, comparison, draft ADR, topic index |
| `/evaluate` | Product go/no-go before explore/plan when scope warrants |
| `/create-plan` | Implementation plan + testing plan; Linear-linked by default |
| `/review-plan` | Self-review, critique, arbitrate, status |
| `/implement` | Plan-driven implementation guardrails (`/implement` / `run-workflow` pipeline) |
| `/run-workflow` | Automated per-issue agent pipeline (`scripts/run-workflow.mts`) |
| `/verify-plan` | Verify implementation, ADR finalise, portfolio/topic updates, commit guidance |
| `/record-retro` | As-built record when work shipped without plan loop |
| `/linear-update` | Apply comment/status to **one** issue when Thomas requests |
| `/triage-linear-issues` | Linear issue hygiene / Rough → Product OS shape |
| `/cleanup-and-audit-branches` | Branch topology / release-line audit |
| `/review-cursor-branches` | Review `origin/cursor/*` before merge to `dev` |
| `/prepare-private-release-from-dev` | Scoped **`dev` → private `main`** RC |
| `/publish-private-main-to-public` | Private `main` → **public export** (gates before push) |
| `/merge-workflow` | **Run-workflow closure** — review **`agent/lum-*`**, archive + merge to **`dev`** (gated); **no** Linear API |
| `/update-context-pack` | Maintains **this file** from evidence |

---

## Current roadmap / priority snapshot *(export-based; may be stale)*

**Do not treat as live Linear.** Run `node scripts/linear/linear_import.mjs --export-issues` (**lumogis-devtools**) — **`linear-issues-export.json`** is **gitignored** locally.

**Local snapshot (this refresh):** **245** issues, **`exportedAt`:** **`2026-05-15T14:47:57Z`** — **not** refreshed in this context-pack pass.

**Recently Done / high-signal (export + git — verify in Linear):**
- **LUM-249** — **`verify-public-rc`** environment reliability — **Done** in export
- **LUM-127** — document injection sanitisation + **ADR 039** — **Done** in export
- **LUM-247** — **check-main-hygiene** vs private `docs/private` — **Done** in export
- **LUM-224** (+ **LUM-250** admin **204**) — web Dockerfile/npm-ci + related verify — **landed on `dev`** (`6f3c568` narrative per **topics** / git log — verify **Done** in Linear)
- **0.4.0** — public release logged **`docs/release/public-release-log.md`**

**In flight (repo evidence, not necessarily Linear state):**
- **LUM-183** — **SECURITY.md** / **`.github/SECURITY.md`** + **ADR 044** implemented on **`agent/lum-183`** (**`37f0005`**); **merge to `dev`** pending **`/merge-workflow`** confirmation; **P1** operator items (**PVR**, mailbox evidence) per **topics**

**Active themes** (patterns from export/dashboard — **verify in Linear**):
- **Security / privacy** — LUM-141 class, LUM-125, LUM-29 / LUM-31 / LUM-23 class; **LUM-183** disclosure policy (branch-ready)
- **Pre-launch docs** — LUM-217, LUM-183, LUM-182, LUM-55
- **Web / credentials / KG** — LUM-44 programme, LUM-28, LUM-46 / LUM-37, exploration/mobile threads
- **CI / parity** — e.g. **LUM-248** (**stack-control** tests on host CI / venv — **Backlog** in export)

**Cautions:** `migration:needs-dedupe`; human-review labels; **`drift_check.mjs`** **PLAN** / **EXPLORATION** linkage **ERROR**s (unclassified plan/exploration paths until registry/frontmatter hygiene) — **57** `MARKDOWN_ACTION_WITHOUT_LINEAR_OUTCOME` WARNs in latest default-scope run

---

## Key hard rules

- No **secrets** in repo, chats, or this pack (no `.env` values, tokens, keys).
- No **`cursor/settings.json`**, `__pycache__`, `node_modules`, `.pytest_cache` in commits (unless explicitly intended).
- **No wholesale `dev` → `main`** unless Thomas requests full dev promotion.
- **No** pushing private `main` **directly** to public — export scripts only.
- **No** ad-hoc Linear mutation — **`/linear-update`**, **`/navigator sync`** (priorities only), or approved scripts only.
- **No** markdown-only future work as system of record — **Linear outcomes** for actionable items.
- **GHCR publish only from `lumogis/lumogis`** — do not add a `publish-image.yml` back to `lumogis-app` without a conscious decision and ADR revision.
- Prefer **small, scoped** changes; if unsure, **`/navigator`**.

---

## Verification commands *(typical; match touched paths)*

| Layer | Examples |
| --- | --- |
| **Orchestrator** | `make test` / `cd orchestrator && pytest …`; `make compose-test` (Docker; **`QDRANT_HOST_PORT`** defaults **6335** when unset in Make) |
| **Integration** | `make compose-test-integration`, `make test-integration` (venv) |
| **Lint** | `make lint`, `make compose-lint` |
| **Web** | `make web-test`, `web-lint`, `web-build`, `web-e2e` (needs stack + env per README) |
| **KG** | `make test-kg`, `make compose-test-kg`, `make test-graph-parity` (slow) |
| **Compose policy** | `make compose-policy-check` (mock overlay **+** **`docker-compose.ghcr.yml`**); also **`compose-policy-check-baseline`**, adversarial variants when touching policy |
| **RC gates** | **`make verify-public-rc`** — hygiene → **`compose-policy-check`** → graph merge policy → **`compose-test`** → **`web-codegen-check`** (bootstraps **`.venv`**, runs **`dump_openapi`** — **no live orchestrator**) → web lint/test/build → **`integration-public-rc.sh`** unless **`VERIFY_PUBLIC_RC_SKIP_INTEGRATION=1`** (warn; dev-only) → export tree + **`check-public-export`**. **`make verify-public-rc-full`** — invokes **`verify-public-rc`** with **`VERIFY_PUBLIC_RC_FORCE_INTEGRATION=1`** (always runs integration) + Playwright (+ optional graph parity). **`VERIFY_PUBLIC_RC_SKIP_WEB_CODEGEN_CHECK=1`** still available if justified |
| **Product OS** | `node scripts/linear/drift_check.mjs`; `node scripts/linear/check_linear_evidence_index.mjs` (read-only; **devtools** root) |
| **Release / public** | `scripts/check-main-hygiene.sh`, `scripts/check-public-export.sh`, `scripts/create-upstream-export-tree.sh`; skill **`/publish-private-main-to-public`**; `docs/testing/automated-test-strategy.md` |

---

## New Chat Bootstrap Prompt

Copy-paste:

> I am working on Lumogis. Please use the attached/pasted **Lumogis Context Pack** as the current project context. Treat **Linear** as the active backlog, **repo/devtools files** as evidence, and route workflow advice through the documented **skills**. Do not assume **dev** / **main** / **public** histories are linear. My question is: …

---

## Source-of-truth pointers

- `AGENTS.md` — routing / guardrails + **verify-public-rc** host prerequisites
- `docs/LUMOGIS_CONTEXT_PACK.md` — **this** compact orientation (maintained by **`/update-context-pack`**)
- `docs/README.md` — documentation index (`guides/`, `extending/`, ADRs, release, `private/` pointers)
- `docs/guides/` — self-hoster ops
- `docs/extending/` — stack extension + plugin example
- `docs/LUMOGIS_REFERENCE_MANUAL.md` — operator + contributor narrative *(tool-catalog default may lag code — see **Last refreshed**)*
- `docs/capabilities.md` — shipped-capability overview
- `CHANGELOG.md` — release history ( **[0.4.0]** **2026-05-15** )
- `docs/release/public-release-log.md` — private maintainer record for **public** export SHAs (**0.4.0**)
- `docs/testing/automated-test-strategy.md` — test layers
- `README.md`, `ARCHITECTURE.md` (repo root)
- `docs/architecture/` — public architecture supplements
- `docs/decisions/` — ADRs (**`dev`** includes through **043** web npm-ci; **044** coordinated disclosure on **`agent/lum-183`** until merged)
- **Trust / disclosure (when LUM-183 lands on `dev`):** root **`SECURITY.md`**, **`.github/SECURITY.md`**
- `cursor/backlog/linear-operating-model.md` — Product OS taxonomy (devtools)
- `cursor/reports/linear-issues-export.json` — local Linear snapshot (**gitignored**)
- `cursor/reports/linear-roadmap-priority-dashboard-*.md` — prioritisation dashboard
- `cursor/reports/linear-id-map-*.csv` — ID traceability
- `scripts/linear/README.md` (devtools) — import/drift tooling
- `.cursor/skills/*/SKILL.md` — full workflows

---

## Maintenance rules

- Refresh when **architecture**, **branch/release**, **Product OS**, **skills**, or **roadmap-facing** facts change materially.
- **Shrink** stale bullets; do not accumulate noise.
- **No** second backlog here — **Linear** owns actionable work.
- If an update implies new actionable work, record **Linear outcomes** per `AGENTS.md`.

---

## Update workflow

1. Inspect git state in lumogis-app and lumogis-devtools.
2. Read existing **`docs/LUMOGIS_CONTEXT_PACK.md`**.
3. Refresh evidence: optional **`node scripts/linear/linear_import.mjs --export-issues`**; optional **`drift_check.mjs`** — **do not** invent Linear mutations beyond maintainer-approved script use.
4. Update the context pack.
5. Run **`git diff --check`**; scan draft for secrets.
6. Report sections changed, evidence sources, uncertainty, whether Linear follow-ups are needed.
7. **Do not commit** unless Thomas approves.
