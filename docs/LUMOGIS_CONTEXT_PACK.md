# Lumogis Context Pack

Concise onboarding for AI assistants (**Cursor**, **ChatGPT**, **Claude**). Summarises **repo evidence only** — not Linear live state.

---

## Last refreshed

| Field | Value |
| --- | --- |
| **Date/time** | **2026-05-22** (`/update-context-pack`; repo + devtools evidence; Linear export **`2026-05-22T22:13:05.679Z`** via **`/navigator sync`** same session) |
| **lumogis-app** | branch **`dev`**, tip **`a22b18f49`** — **`origin/dev`** (**ahead 7**, not pushed at refresh); includes **LUM-190** pre-launch hybrid audit CI + artefacts (**ADR 060-lum-190**), **LUM-165** first-run onboarding (**ADR 059-lum-165**), **LUM-308** auto-RAG (**ADR 059-lum-308**), **LUM-184** quickstart (**ADR 059-lum-184**), prior **LUM-305**/**LUM-306**/**LUM-281**/**LUM-94**/**LUM-209** chunks, **[Unreleased]** ahead of next tagged release |
| **lumogis-devtools** | branch **`main`**, tip **`ca56527`** — **`origin/main`** (**ahead 6**, not pushed at refresh); **LUM-190** / **LUM-165** / **LUM-308** / **LUM-184** verify-plan + merge-workflow artefacts archived; export/dashboard refresh local (**gitignored** / uncommitted HTML+JSON from sync) |
| **Public / upstream** | **0.4.0** per **`docs/release/public-release-log.md`** (public **`lumogis/lumogis`** tip **`cdcc574…`**); **`dev`** **[Unreleased]** ahead of next tagged release |

**Evidence sources consulted:**

- `AGENTS.md` (lumogis-app) — verify-public-rc environment, Product OS routing
- `README.md`, `ARCHITECTURE.md`, `CHANGELOG.md`, `docs/capabilities.md`, `docs/LUMOGIS_REFERENCE_MANUAL.md`, `TELEMETRY.md`, `SECURITY.md` (lumogis-app)
- `docs/README.md`, `docs/architecture/*.md`, `docs/release/public-release-log.md`
- `docs/decisions/*.md` on **`dev`** through **060** (**LUM-190** pre-launch audit — **ADR 060-lum-190**; **LUM-302** OpenAPI classifier — **ADR 060-lum-302**; **LUM-165** onboarding — **ADR 059-lum-165**; **LUM-308** auto-RAG — **ADR 059-lum-308**; **LUM-184** quickstart — **ADR 059-lum-184**; **LUM-209** sessions index — **ADR 055**; **LUM-281**/**LUM-237**/**LUM-94** — **054**/**053-lum-237**/**053-lum-94**; **LUM-305**/**LUM-306** — **057**/**058**); note **two `034-*.md`**, **two `046-*.md`**, **two `053-*.md`**, **two `059-*.md`**, and **two `060-*.md`** numbering collisions — see `docs/README.md` / librarian inventory
- `Makefile` — **`compose-policy-check`**, **`make changelog-check`**, RC gates
- **`cursor/reports/linear-issues-export.json`** — **314** issues, **`exportedAt`:** **`2026-05-22T22:13:05.679Z`** (gitignored locally; refresh with `node scripts/linear/linear_import.mjs --export-issues` in **lumogis-devtools**)
- `cursor/reports/linear-roadmap-priority-dashboard-2026-05-03.md` (§5–§6 regenerated on export)
- `cursor/reports/linear-id-map-2026-05-03.csv`, `cursor/backlog/linear-operating-model.md`, `cursor/topics.md`
- `orchestrator/config.py` — **`LUMOGIS_TOOL_CATALOG_ENABLED`** default **on** when unset; **`GRAPH_MODE`** default **`disabled`** when unset
- `node scripts/linear/drift_check.mjs` (**default scope** — **1** ERROR: **EXPLORATION_LINKAGE_DRIFT**; **55** WARNs)

**Freshness / uncertainty:**

- **`linear-issues-export.json`** is **`.gitignore`d** — teammates should use the same **`exportedAt`** after running **`--export-issues`**.
- **LUM-183 / LUM-255:** Export shows **Done**; **P1** operator evidence (GitHub **Private vulnerability reporting** on public repo, mailbox monitoring) may still be open in Linear — verify in UI, not from this pack.
- **Roadmap dashboard** §1–§4 / §7–§8 narrative is **curated** — reconcile with §5–§6 + JSON for ordering.

---

## What Lumogis is

- **Self-hosted, local-first, privacy-first** household / personal AI: data and indexes stay on hardware you control (Docker Compose, **AGPL-3.0-only** per **README** / **ADR 032**).
- **Core** = **FastAPI orchestrator**; **Lumogis Web** = first-party SPA behind **Caddy** (same-origin); **LibreChat** = optional legacy profile, not the primary surface (**ADR 012**).
- **Ask / Do** safety model for actions/tools; auditability and connector/credential boundaries are first-class (**ADR 006**, credential ADRs **018**, **027**, **029**, etc.).
- **Not** Lumogis-operated SaaS — positioning is **family/household LAN** and operator-controlled deployment.
- **Public vs private**: the **public** tree is an **export snapshot**, not a byte mirror of private history (**`AGENTS.md`**). **GHCR images** (`lumogis-orchestrator`, `lumogis-web`) are published **only** from `lumogis/lumogis` (public repo) `main` — not from private `lumogis-app` (**ADR 037**). **SLSA L2 attestations** on published images — verify with **`gh attestation verify`** (**ADR 049**, **CHANGELOG** [Unreleased]).

---

## Repository model

| Repo / path | Role |
| --- | --- |
| **lumogis-app** | Product: application, tests, Docker, shipping paths |
| **lumogis-devtools** | Cursor skills, Product OS tooling, Linear scripts, **`.cursor/reports`**, registries |
| **lumogis-app/.cursor** | Symlink → **lumogis-devtools/cursor** (plans, skills, rules resolve here) |
| **lumogis-public** / **upstream/main** | Public **AGPL** export line — **not** the same lineage as private `main`; **0.4.0** snapshot in **`docs/release/public-release-log.md`** |

---

## Branch and release model

| Line | Typical role |
| --- | --- |
| **lumogis-app `dev`** | Active integration |
| **lumogis-app `origin/main`** | Clean **private** release line |
| **Public `main` / `upstream/main`** | **Exported** AGPL snapshot |
| **lumogis-devtools `main`** | Internal tooling |

- **`dev` and private `main` may diverge.** **Public** and **private** `main` **may differ by commit hash**.
- **Scoped promotion** (`README`/docs/assets) is the default for **`dev` → private `main`** — not wholesale merge (**`/prepare-private-release-from-dev`**).
- **`/publish-private-main-to-public`**: export via scripts; **never** raw-push private `main` to public. **Hard gates** before export: `make verify-public-rc` or `make verify-public-rc-full` on private `main`; **`CHANGELOG.md`** + **`docs/capabilities.md`** updated; ADRs and **`docs/LUMOGIS_REFERENCE_MANUAL.md`** aligned at implementation time.
- **GHCR publish** from **`lumogis/lumogis`** `publish-image.yml` on public `main` / tags — **not** in **`lumogis-app`** (**ADR 037**). **LUM-276** added public **`attestation-guard.yml`** / **`attestation-verify.yml`** workflows (evidence on **`dev`** + topics).
- For live branch topology: **`/cleanup-and-audit-branches`**.

---

## Architecture snapshot *(high level)*

| Area | Notes |
| --- | --- |
| **Core** | FastAPI orchestrator — services, adapters, plugins, signals, actions, routes (**`ARCHITECTURE.md`**) |
| **Ingest / prompt-injection hygiene** | **ADR 039** — sanitiser, scaffolding, **`TOOL_CHAIN_CAP`** |
| **Context building** | **ADR 051** / **LUM-210** — hybrid entity selection (explicit + optional semantic), entity budget env vars; **LUM-308** — optional chat **auto-RAG** from **`documents`** (`LUMOGIS_AUTO_RAG_*`, default **off**) alongside session summaries |
| **Lumogis Web** | Primary SPA; **LibreChat** optional (compose profile) |
| **Data** | **Postgres**, **Qdrant**, **Ollama**; **`QDRANT_HOST_PORT`** default host **6334** ( **`lumogis-test`** example uses **6335** ) |
| **Graph / KG** | **FalkorDB** optional; **`GRAPH_MODE`**: `inprocess` / `service` / **`disabled`** default; `query_graph` fail-closed in `service` mode (**ADR 035**). **ADR 038** — **`RELATES_TO`** direction |
| **Capabilities / plugins** | HTTP manifests, bearer trust (**ADR 010**, **011**); **mock-capability** for contract smoke |
| **MCP** | Core **`/mcp/`** streamable HTTP; per-user opaque bearer tokens when `AUTH_ENABLED` (**ADR 017**). Active **evaluation/exploration** cluster around **Lumogis-as-MCP-memory** for coding tools (**LUM-284+** family in export — verify in Linear) |
| **Tool catalog** | **`LUMOGIS_TOOL_CATALOG_ENABLED`**: **`config.py`** defaults **on** when unset — set **`false`** to disable merged capability tools |
| **Sessions / auth** | Multi-device refresh + **`tv`** invalidation (**ADR 041**); optional per-request **`sid`** revocation lookup (**ADR 050** / **LUM-243**) |
| **Capture / STT** | `/capture`, `/api/v1/captures`, voice transcribe when STT enabled (**ADR 031**) |
| **External ingest** | **paperless-ngx** read-only REST polling (**LUM-281**, **ADR 054**); per-user credentials; `POST /api/v1/sources` with `source_type: "paperless"` |
| **Credentials** | Per-user, household, instance-system tiers; connector Ask/Do per user (**ADR 024**, **027**, …) |
| **Mobile / PWA / Web Push** | Phase 2–5 MVP slices shipped per ADR 030 / reference manual §17 |
| **GHCR / supply chain** | Images from public repo only; **SLSA** attestations + regression guard workflows (**ADR 049**, **LUM-276**) |
| **OpenAPI / web client contract** | Offline **`dump_openapi.py`** + snapshot drift; **`make openapi-check`** alias + path-gated **`openapi-check`** CI job (**ADR 053**, **LUM-94**); **LUM-302** breaking-change classifier follow-up (**ADR 060-lum-302**, **Done** in export at refresh) |
| **Pre-launch security audit** | Hybrid manual + CI SCA/SAST: path-gated **`security-audit`** job, **`make audit-local`** (blocking), advisory **Bandit**, findings under **`docs/security-audit/`** (**ADR 060-lum-190**, **LUM-190** — **In Review** in Linear at refresh; P1: RC ZAP + evidence-index row) |
| **Telemetry proof** | **`TELEMETRY.md`** + Makefile guard (**ADR 046-telemetry** file; separate from **046-lum-35** backup ADR) |

---

## Product OS and Linear workflow

- **Linear** = active **backlog and status** surface; **repo/devtools** files = **durable evidence**.
- **Planned work:** `/create-plan` → `/review-plan` → implement → `/verify-plan` → **`/merge-workflow`** (when **`run-workflow`** defers Step 12) → `/navigator drift` → `/linear-update` (when Thomas applies closure).
- **Unplanned shipped work:** `/record-retro` → `/navigator drift` → `/linear-update`.
- **`/linear-update`**: only skill that **intentionally mutates** Linear issue fields beyond priorities — **one issue** at a time, explicit Thomas request.
- **`/navigator sync`** (explicit): refresh export + percentile **priority** sync via **`linear_import.mjs`** — requires **`LINEAR_API_KEY`** in env; **not** default Navigator.
- **Actionable follow-ups** need **Linear outcomes** (not markdown-only backlog). **P0** blocks closure; **P1** needs explicit acceptance; **P2/P3** deferred only with recorded outcomes.
- **Drift check:** `node scripts/linear/drift_check.mjs` — this refresh: **1** ERROR (**exploration linkage**), **55** WARNs (markdown deferred-action prose in skills/plans); **`cursor/evidence/linear-evidence-index.md`** may still have **PATH_MISSING** rows (pre-existing).

---

## Cursor skills map *(one line each)*

| Skill | Role |
| --- | --- |
| `/navigator` | Read-only routing from export/reports; **`/navigator sync`** refreshes export + syncs priorities |
| `/explore` | Research, comparison, draft ADR, topic index |
| `/evaluate` | Product go/no-go before explore/plan when scope warrants |
| `/create-plan` | Implementation plan + testing plan; Linear-linked by default |
| `/review-plan` | Self-review, critique, arbitrate, status |
| `/implement` | Plan-driven implementation guardrails (`run-workflow` pipeline) |
| `/run-workflow` | Automated per-issue agent pipeline (`scripts/run-workflow.mts`) |
| `/verify-plan` | Verify implementation, ADR finalise, portfolio/topic updates, commit guidance |
| `/record-retro` | As-built record when work shipped without plan loop |
| `/linear-update` | Apply comment/status to **one** issue when Thomas requests |
| `/triage-linear-issues` | Linear issue hygiene / Rough → Product OS shape |
| `/cleanup-and-audit-branches` | Branch topology / release-line audit |
| `/review-cursor-branches` | Review `origin/cursor/*` before merge to `dev` |
| `/prepare-private-release-from-dev` | Scoped **`dev` → private `main`** RC |
| `/publish-private-main-to-public` | Private `main` → **public export** (gates before push) |
| `/merge-workflow` | **Run-workflow closure** — review **`agent/lum-*`**, archive + merge to **`dev`** (gated) |
| `/update-context-pack` | Maintains **this file** from evidence |

---

## Current roadmap / priority snapshot *(export-based; verify in Linear)*

**Do not treat as live Linear.** Snapshot: **314** issues, **`exportedAt`:** **`2026-05-22T22:13:05.679Z`**. Priority bands refreshed via **`/navigator sync`** same session (**7** Linear priority mutations: **LUM-318**, **LUM-312**, **LUM-227**, **LUM-105**, **LUM-92**, **LUM-52**, **LUM-47**).

**Per-type top band (one line each — use `/navigator next --[type]` to drill down; do not merge types into one global rank):**

| Type group | Top non-terminal band (export) | Example identifier |
| --- | --- | --- |
| **bug** | *(none active)* | — |
| **security** | High | **LUM-190** |
| **feature** | Urgent | **LUM-194** |
| **improvement** | Urgent | **LUM-33** |
| **techdebt** | Urgent | **LUM-125** |
| **evaluation** | Urgent | **LUM-170** |
| **exploration** | Urgent | **LUM-24** |
| **docs** | Urgent | **LUM-213** |

**Recently shipped on `dev` (export + merge evidence — verify workflow state in Linear UI):** **LUM-190** (hybrid pre-launch audit CI + **`docs/security-audit/`** artefacts, **ADR 060-lum-190** — merged **`a22b18f49`**, **In Review** in Linear); **LUM-165** (first-run onboarding modal + empty states, **ADR 059-lum-165** — **Done** in export); **LUM-308** (**Done** — chat auto-RAG, **ADR 059-lum-308**); **LUM-184** (first-run **`docs/deployment/quickstart.md`**, **ADR 059-lum-184** — merged to **`dev`**, **In Review** in export); **LUM-302** (OpenAPI breaking-change classifier, **ADR 060-lum-302** — **Done** in export); **LUM-281**, **LUM-237**, **LUM-304**, **LUM-94**, **LUM-209**, **LUM-305**/**LUM-306**, and others — see export filter, not exhaustive.

**LUM-94 follow-ups:** **LUM-302** shows **Done** in export at refresh; **LUM-303** (P3 public **`lumogis/lumogis`** CI parity for **`openapi-check`**) remains open. **LUM-273** shows **Done** in export.

**Active repo plans (devtools, non-archived):** **LUM-44**, **LUM-76**, **LUM-77**, **LUM-56**, **LUM-57**, **LUM-277**, **LUM-78**, **LUM-53** (FP-032 closure plan file). **LUM-190**, **LUM-165**, **LUM-308**, **LUM-184**, **LUM-281**, **LUM-237**, **LUM-94**, **LUM-302** plans → **`cursor/plans/archived/`**.

**Themes (patterns in export — not a backlog):** **MCP / coding-agent memory** (**LUM-284+**), **agentic core**, **cross-device web**, **KG quality / living graph**, **ingest/search + auto-RAG follow-ups**, **pre-launch security/docs**, **public export / attestations / changelog / OpenAPI CI parity**.

**Cautions:** **`type:story`** deprecated (**LUM-260** flagged in sync — route **`/triage-linear-issues`**); **`drift_check`** **exploration linkage** ERROR; evidence index **PATH_MISSING** rows; export refresh artefacts may be **local-only** until committed in **devtools**; **0** active **`bug`**-typed non-terminal issues in export (verify taxonomy if that looks wrong).

---

## Key hard rules

- No **secrets** in repo, chats, or this pack (no `.env` values, tokens, keys).
- No **`cursor/settings.json`**, `__pycache__`, `node_modules`, `.pytest_cache` in commits (unless explicitly intended).
- **No wholesale `dev` → `main`** unless Thomas requests full dev promotion.
- **No** pushing private `main` **directly** to public — export scripts only.
- **No** ad-hoc Linear mutation — **`/linear-update`**, **`/navigator sync`** (priorities only), or approved scripts only.
- **No** markdown-only future work as system of record — **Linear outcomes** for actionable items.
- **GHCR publish only from `lumogis/lumogis`** — do not add `publish-image.yml` back to **`lumogis-app`** without ADR revision.
- Prefer **small, scoped** changes; if unsure, **`/navigator`**.

---

## Verification commands *(typical; match touched paths)*

| Layer | Examples |
| --- | --- |
| **Orchestrator** | `make test` / `pytest`; `make compose-test` (Docker; **`QDRANT_HOST_PORT`** defaults **6335** in Make when unset) |
| **Integration** | `make compose-test-integration`, `make test-integration` |
| **Lint** | `make lint`, `make compose-lint` |
| **Web** | `make web-test`, `web-lint`, `web-build`, `web-e2e` |
| **KG** | `make test-kg`, `make compose-test-kg`, `make test-graph-parity` (slow) |
| **Compose policy** | `make compose-policy-check` (+ baseline / adversarial variants when touching policy) |
| **Changelog gate** | `make changelog-check` (PRs touching product paths per **CONTRIBUTING.md**) |
| **OpenAPI / codegen** | `make openapi-check` (alias **`web-codegen-check`**); offline snapshot vs **`dump_openapi`** (**ADR 053**) |
| **Security audit (local/CI)** | `make audit-local`, `make bandit-check`; path-gated **`security-audit`** CI job (**LUM-190**) |
| **RC gates** | **`make verify-public-rc`** / **`make verify-public-rc-full`** — see **`AGENTS.md`** for skip vs full integration |
| **Product OS** | `node scripts/linear/drift_check.mjs`; `node scripts/linear/check_linear_evidence_index.mjs` (**devtools** root) |
| **Release / public** | `scripts/check-main-hygiene.sh`, `scripts/check-public-export.sh`, `scripts/create-upstream-export-tree.sh`; **`/publish-private-main-to-public`** |

---

## New Chat Bootstrap Prompt

Copy-paste:

> I am working on Lumogis. Please use the attached/pasted **Lumogis Context Pack** as the current project context. Treat **Linear** as the active backlog, **repo/devtools files** as evidence, and route workflow advice through the documented **skills**. Do not assume **dev** / **main** / **public** histories are linear. My question is: …

---

## Source-of-truth pointers

- `AGENTS.md` — routing / guardrails + verify-public-rc host notes
- `docs/LUMOGIS_CONTEXT_PACK.md` — **this** file (**`/update-context-pack`**)
- `docs/README.md` — documentation index
- `docs/LUMOGIS_REFERENCE_MANUAL.md` — operator + contributor narrative
- `docs/capabilities.md` — shipped capabilities
- `CHANGELOG.md` — **[0.4.0]** **2026-05-15** + **[Unreleased]** on **`dev`**
- `TELEMETRY.md`, `SECURITY.md` — trust / disclosure
- `docs/release/public-release-log.md` — **0.4.0** public export record
- `docs/decisions/` — ADRs through **060** on **`dev`** (note **060-lum-190** / **060-lum-302** and **059-lum-165** / **059-lum-308** / **059-lum-184** collisions)
- `docs/architecture/`, `docs/guides/`, `docs/extending/`
- `cursor/backlog/linear-operating-model.md` — Product OS taxonomy (**devtools**)
- `cursor/reports/linear-issues-export.json` — Linear snapshot (**gitignored**)
- `cursor/reports/linear-roadmap-priority-dashboard-*.md`
- `scripts/linear/README.md` (**devtools**)
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
3. Refresh evidence: optional **`node scripts/linear/linear_import.mjs --export-issues`**; optional **`drift_check.mjs`** — do not invent Linear mutations beyond maintainer-approved script use.
4. Update the context pack.
5. Run **`git diff --check`**; scan draft for secrets.
6. Report sections changed, evidence sources, uncertainty, whether Linear follow-ups are needed.
7. **Do not commit** unless Thomas approves.
