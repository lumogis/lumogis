# Lumogis Context Pack

Concise onboarding for AI assistants (**Cursor**, **ChatGPT**, **Claude**). Summarises **repo evidence only** — not Linear live state.

---

## Last refreshed

| Field | Value |
| --- | --- |
| **Date/time** | **2026-05-27** (`/update-context-pack`; repo + devtools evidence; Linear export **`2026-05-27T14:28:01.419Z`**) |
| **lumogis-app** | branch **`dev`**, tip **`05995625b`** — **`origin/dev`** (in sync); includes **LUM-329** Tauri desktop overlay (**ADR 069-lum-329**), **LUM-124** memory-as-hint (**ADR 066-lum-124**), **LUM-320** doctor v2 **`--fix`** slice 1 (**ADR 065**), **LUM-322** doctor deferral (**ADR 061** gates), cursor-branch merges (librarian **2026-05-25–27**, e2e spawn-no-shell **ADR 068**, paperless blocked-high poll stall **ADR 067**), prior **LUM-184**/**LUM-165**/**LUM-308**/**LUM-190** chunks, **[Unreleased]** ahead of next tagged release |
| **lumogis-devtools** | branch **`main`**, tip **`4beca33`** — **`origin/main`** (in sync); **LUM-329**/**LUM-124** verify + merge-workflow archives; **record-retro** index for ADRs **067**/**068** |
| **Public / upstream** | **0.4.0** per **`docs/release/public-release-log.md`** (public **`lumogis/lumogis`** tip **`cdcc574…`**); **`dev`** **[Unreleased]** ahead of next tagged release |

**Evidence sources consulted:**

- `AGENTS.md` (lumogis-app) — verify-public-rc environment, Product OS routing
- `README.md`, `ARCHITECTURE.md`, `CHANGELOG.md`, `docs/capabilities.md`, `docs/LUMOGIS_REFERENCE_MANUAL.md`, `TELEMETRY.md`, `SECURITY.md` (lumogis-app)
- `docs/README.md`, `docs/architecture/*.md`, `docs/release/public-release-log.md`
- `docs/decisions/*.md` on **`dev`** through **069** (**LUM-329** desktop — **069-lum-329**; **LUM-124** memory-as-hint — **066-lum-124**; **LUM-320** doctor **`--fix`** — **065**; **LUM-322**/**LUM-319**/**LUM-321** — **063**/**064**; **LUM-281** paperless follow-ups — **062**, **067**; **LUM-60** e2e hardening — **068**; prior **055**–**061**); note **two `034-*.md`**, **two `046-*.md`**, **two `053-*.md`**, **two `059-*.md`**, **two `060-*.md`**, **two `061-*.md`**, **two `063-*.md`**, and **two `064-*.md`** numbering collisions — see `docs/README.md` / librarian inventory
- `Makefile` — **`compose-policy-check`**, **`make changelog-check`**, **`make doctor`**, **`make desktop-dev`** / **`make desktop-build`**, RC gates
- **`cursor/reports/linear-issues-export.json`** — **384** issues, **`exportedAt`:** **`2026-05-27T14:28:01.419Z`** (gitignored locally; refresh with `node scripts/linear/linear_import.mjs --export-issues` in **lumogis-devtools**)
- `cursor/reports/linear-roadmap-priority-dashboard-2026-05-03.md` (§5–§6 regenerated on export)
- `cursor/reports/linear-id-map-2026-05-03.csv`, `cursor/backlog/linear-operating-model.md`, `cursor/topics.md`
- `orchestrator/config.py` — **`LUMOGIS_TOOL_CATALOG_ENABLED`** default **on** when unset; **`GRAPH_MODE`** default **`disabled`** when unset
- `node scripts/linear/drift_check.mjs` (**default scope** — **2** ERRORs: **EXPLORATION_LINKAGE_DRIFT**, **PLAN_LINKAGE_DRIFT**; **57** WARNs: **MARKDOWN_ACTION_WITHOUT_LINEAR_OUTCOME** in skills/plans)

**Freshness / uncertainty:**

- **`linear-issues-export.json`** is **`.gitignore`d** — teammates should use the same **`exportedAt`** after running **`--export-issues`**.
- **LUM-329** / **LUM-124:** Merged to **`dev`** with **`VERIFY_RESULT` incomplete** gaps (see topics index) — verify Linear workflow state in UI, not from this pack alone.
- **LUM-183 / LUM-255:** Export may show **Done**; **P1** operator evidence (GitHub **Private vulnerability reporting** on public repo, mailbox monitoring) may still be open in Linear — verify in UI.
- **v0.1 product gaps:** Core remaining explorations include **LUM-330** (folder watch inbox hardening — plan active) and shipped **LUM-329** (Tauri overlay); launch/docs cluster **LUM-180**/**LUM-181**/**LUM-190**/**LUM-184** — reconcile export for terminal state.
- **Roadmap dashboard** §1–§4 / §7–§8 narrative is **curated** — reconcile with §5–§6 + JSON for ordering.

---

## What Lumogis is

- **Self-hosted, local-first, privacy-first** household / personal AI: data and indexes stay on hardware you control (Docker Compose, **AGPL-3.0-only** per **README** / **ADR 032**).
- **Core** = **FastAPI orchestrator**; **Lumogis Web** = first-party SPA behind **Caddy** (same-origin); **LibreChat** = optional legacy profile, not the primary surface (**ADR 012**).
- **Lumogis Desktop** (**LUM-329**, proprietary tree under **`clients/lumogis-desktop/`**) = Tauri 2 global-hotkey memory search overlay — excluded from public AGPL export.
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
- **GHCR publish** from **`lumogis/lumogis`** `publish-image.yml` on public `main` / tags — **not** in **`lumogis-app`** (**ADR 037**).
- For live branch topology: **`/cleanup-and-audit-branches`**. For **`origin/cursor/*`**: **`/review-cursor-branches`**.

---

## Architecture snapshot *(high level)*

| Area | Notes |
| --- | --- |
| **Core** | FastAPI orchestrator — services, adapters, plugins, signals, actions, routes (**`ARCHITECTURE.md`**) |
| **Ingest / prompt-injection hygiene** | **ADR 039** — sanitiser, scaffolding, **`TOOL_CHAIN_CAP`** |
| **Context building** | **ADR 051** / **LUM-210** — hybrid entity selection; **LUM-124** — memory-as-hint entity taxonomy + chat hedging (**ADR 066-lum-124**); **LUM-308** — optional chat **auto-RAG** (`LUMOGIS_AUTO_RAG_*`, default **off**) |
| **Lumogis Web** | Primary SPA; **LibreChat** optional (compose profile) |
| **Lumogis Desktop** | Tauri 2 overlay — **`GET /api/v1/memory/search`**, OS keychain JWT (**LUM-329**, **ADR 069-lum-329**); proprietary; **`make desktop-dev`** / CI **`desktop-build.yml`** |
| **Data** | **Postgres**, **Qdrant**, **Ollama**; **`QDRANT_HOST_PORT`** default host **6334** ( **`lumogis-test`** example uses **6335** ) |
| **Graph / KG** | **FalkorDB** optional; **`GRAPH_MODE`**: `inprocess` / `service` / **`disabled`** default; fail-closed in `service` mode (**ADR 035**). **ADR 038** — **`RELATES_TO`** direction |
| **Capabilities / plugins** | HTTP manifests, bearer trust (**ADR 010**, **011**); **mock-capability** for contract smoke |
| **MCP** | Core **`/mcp/`** streamable HTTP; per-user opaque bearer tokens when `AUTH_ENABLED` (**ADR 017**) |
| **Tool catalog** | **`LUMOGIS_TOOL_CATALOG_ENABLED`**: **`config.py`** defaults **on** when unset |
| **Sessions / auth** | Multi-device refresh + **`tv`** invalidation (**ADR 041**); optional per-request **`sid`** revocation lookup (**ADR 050** / **LUM-243**) |
| **Capture / STT** | `/capture`, `/api/v1/captures`, voice transcribe when STT enabled (**ADR 031**) |
| **External ingest** | **paperless-ngx** read-only REST polling (**LUM-281**, **ADR 054**); pagination watermark (**ADR 062**); blocked-high poll stall (**ADR 067**); per-user credentials |
| **Operator health** | **`make doctor`** read-only CLI (**LUM-199**, **ADR 061**); **`--fix`** slice 1 (**LUM-320**, **ADR 065**); in-process doctor probes deferred (**LUM-322**) |
| **Credentials** | Per-user, household, instance-system tiers; connector Ask/Do per user (**ADR 024**, **027**, …) |
| **Mobile / PWA / Web Push** | Phase 2–5 MVP slices shipped per ADR 030 / reference manual §17 |
| **GHCR / supply chain** | Images from public repo only; **SLSA** attestations + regression guard workflows (**ADR 049**, **LUM-276**) |
| **OpenAPI / web client contract** | Offline **`dump_openapi.py`** + snapshot drift; **`make openapi-check`** (**ADR 053**, **LUM-94**); **LUM-302** breaking-change classifier (**ADR 060-lum-302**) |
| **Web E2E CI** | Path-gated **`.github/workflows/web-e2e.yml`** (**LUM-60**, **ADR 064**); host Postgres helper uses **`spawnSync` argv** — no shell (**ADR 068**) |
| **Pre-launch security audit** | Hybrid manual + CI SCA/SAST (**ADR 060-lum-190**, **LUM-190**) |
| **Telemetry proof** | **`TELEMETRY.md`** + Makefile guard (**ADR 046-telemetry** file; separate from **046-lum-35** backup ADR) |

---

## Product OS and Linear workflow

- **Linear** = active **backlog and status** surface; **repo/devtools** files = **durable evidence**.
- **Planned work:** `/create-plan` → `/review-plan` → implement → `/verify-plan` → **`/merge-workflow`** (when **`run-workflow`** defers Step 12) → `/navigator drift` → `/linear-update` (when Thomas applies closure).
- **Unplanned shipped work:** `/record-retro` → `/navigator drift` → `/linear-update`.
- **`/linear-update`**: only skill that **intentionally mutates** Linear issue fields beyond priorities — **one issue** at a time, explicit Thomas request.
- **`/navigator sync`** (explicit): refresh export + percentile **priority** sync via **`linear_import.mjs`** — requires **`LINEAR_API_KEY`** in env; **not** default Navigator.
- **Actionable follow-ups** need **Linear outcomes** (not markdown-only backlog). **P0** blocks closure; **P1** needs explicit acceptance; **P2/P3** deferred only with recorded outcomes.
- **Drift check:** `node scripts/linear/drift_check.mjs` — this refresh: **2** ERRORs (**exploration linkage**, **plan linkage**), **57** WARNs (markdown deferred-action prose in skills/plans).

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

**Do not treat as live Linear.** Snapshot: **384** issues, **`exportedAt`:** **`2026-05-27T14:28:01.419Z`**.

**Recently shipped on `dev` (merge + topics evidence — verify workflow state in Linear UI):** **LUM-329** (Tauri desktop overlay, **ADR 069-lum-329**); **LUM-124** (memory-as-hint, **ADR 066-lum-124**); **LUM-320** (doctor v2 **`--fix`** slice 1, **ADR 065**); **LUM-322** (doctor in-process deferral, **ADR 061** gates); **LUM-321** (pytest preflight, **ADR 064-lum-321**); **LUM-319** (doctor CI integration, **ADR 063-lum-319**); cursor-branch merges (**ADR 067** paperless blocked-high stall, **ADR 068** e2e spawn-no-shell, docs librarian **2026-05-25–27**); prior **LUM-190**/**LUM-165**/**LUM-308**/**LUM-184**/**LUM-281**/**LUM-60** — see export filter, not exhaustive.

**Active repo plans (devtools, non-archived):** **LUM-330** (folder watch inbox hardening), **LUM-44**, **LUM-76**, **LUM-77**, **LUM-56**, **LUM-57**, **LUM-277**, **LUM-78**, **LUM-53**. **LUM-329**, **LUM-124**, **LUM-320**, **LUM-322**, **LUM-321**, **LUM-319**, **LUM-190**, **LUM-165**, **LUM-308**, **LUM-184**, **LUM-281**, **LUM-60**, **LUM-94**, **LUM-302** plans → **`cursor/plans/archived/`**.

**Themes (patterns in export — not a backlog):** **Desktop overlay + folder watch (v0.1)**, **memory-as-hint / KG quality**, **agentic core**, **cross-device web**, **ingest/search + paperless**, **doctor/operator health**, **pre-launch security/docs**, **public export / attestations / OpenAPI CI parity**.

**Cautions:** **`type:story`** deprecated; **`drift_check`** **2** linkage ERRORs; **LUM-329**/**LUM-124** verify gaps may remain open in Linear; export refresh artefacts may be **local-only** until committed in **devtools**; **066** prefix collision resolved (**LUM-124** retains **066**; **LUM-329** → **069**).

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
| **Desktop** | `make desktop-dev`, `make desktop-build`; Rust tests in **`clients/lumogis-desktop/`** |
| **Doctor** | `make doctor`, `make compose-test-doctor` |
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
- `docs/decisions/` — ADRs through **069** on **`dev`** (note **064**/**061**/**059**/**060** filename collision clusters)
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
