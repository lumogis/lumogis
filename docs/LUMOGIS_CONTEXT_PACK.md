# Lumogis Context Pack

Concise onboarding for AI assistants (**Cursor**, **ChatGPT**, **Claude**). Summarises **repo evidence only** — not Linear live state.

---

## Last refreshed

| Field | Value |
| --- | --- |
| **Date/time** | 2026-05-08 (authoring pass; no UTC clock in body) |
| **lumogis-app** | **`origin/main`** commit **`3bf31c5`** (short); tip includes private-only release-log entry after export source **`9ec4e3f`** |
| **lumogis-app `origin/dev`** (reference) | **`3ac85ba`** (short) — may diverge from `main`; not re-audited line-by-line this pass |
| **lumogis-devtools** | branch **`main`**, commit **`62a7276`** (short) |
| **Public `lumogis/lumogis`** | checkout **`ccb803f`** (short) — AGPL snapshot line (**`upstream/main`** remote in private app repo) |

**Evidence sources consulted:**

- `AGENTS.md` (lumogis-app)
- `README.md`, `docs/LUMOGIS_REFERENCE_MANUAL.md` (lumogis-app)
- `docs/release/public-release-log.md` (lumogis-app) — latest entry **2026-05-08** (private `9ec4e3f` → public `ccb803f`)
- `docs/architecture/*.md` (paths present; see **Architecture snapshot**)
- `docs/decisions/*.md` — **35** markdown files on disk (34 numbered ADRs **`001`–`034`** plus **`DEBT.md`**)
- `Makefile` (lumogis-app) — `verify-public-rc`, `verify-public-rc-full`, test / web / compose / KG targets
- `cursor/reports/linear-issues-export.json` — **`exportedAt`: `2026-05-07T18:55:48.956Z`**, team **LUM**, **96** issues
- `cursor/reports/linear-roadmap-priority-dashboard-2026-05-03.md` (header mirrors JSON export metadata; **§1–§2, §7–§8** curated)
- `cursor/reports/linear-id-map-2026-05-03.csv` (traceability snapshot)
- `cursor/backlog/linear-operating-model.md` (path per maintainer layout; not re-read in full)
- `.cursor/skills/*/SKILL.md` (skill set; details live in each file)

**Freshness / uncertainty:**

- **Linear export** is **~1 day old** relative to pack date; **authoritative issue state** is **Linear** + a fresh export. Refresh (human-run, from **lumogis-devtools**):
  `node scripts/linear/linear_import.mjs --export-issues`
  Optional drift script: `node scripts/linear/drift_check.mjs`
- Roadmap dashboard **§1–§2, §7–§8** are **curated** and may lag **§5–§6** + JSON — reconcile per dashboard header.
- For live branch topology: **`/cleanup-and-audit-branches`** (do not treat this pack as a branch map).

---

## What Lumogis is

- **Self-hosted, local-first, privacy-first** household / personal AI: data and indexes stay on hardware you control (Docker Compose, **AGPL-3.0-only** product line per **README** / **ADR 032**).
- **Core** = **FastAPI orchestrator**; **Lumogis Web** = first-party SPA behind **Caddy** (same-origin); **LibreChat** = optional legacy OpenAI-style UI (compose profile), not the primary multi-user surface (**reference manual** / **ADR 012**).
- **Ask / Do** safety model for actions/tools; auditability and connector/credential boundaries are first-class (**`docs/decisions/006-ask-do-safety-model.md`**, credential ADRs **018**, **027**, **029**, etc.).
- **Not** a Lumogis-operated SaaS; positioning is **family/household LAN** and operator-controlled deployment.
- **Public vs private**: **public** tree is an **export snapshot**, not a byte mirror of private history (**`AGENTS.md`**). Long-form **operator stack runbooks** that used to live under `docs/` are **no longer tracked** in-tree (gitignored on private clones; same omission in public export) — see **`docs/release/public-release-log.md`** (2026-05-08).

---

## Repository model

| Repo / path | Role |
| --- | --- |
| **lumogis-app** | Product: application, tests, Docker, shipping paths |
| **lumogis-devtools** | Cursor skills, Product OS tooling, Linear scripts, **`.cursor/reports`**, registries |
| **lumogis-app/.cursor** | Symlink → **lumogis-devtools/cursor** (plans, skills, rules resolve here) |
| **lumogis-public** / **`upstream`** on `lumogis` | Public **AGPL** export line (**not** the same lineage as private `main` — **`AGENTS.md`**) |

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
- **`/publish-private-main-to-public`**: **`scripts/create-upstream-export-tree.sh`** + **`scripts/check-public-export.sh`**; sync into a **separate public working copy**; **never** raw-push private `main` to public.
- For live topology: **`/cleanup-and-audit-branches`** (do not embed branch tables here).

---

## Architecture snapshot *(high level)*

| Area | Notes |
| --- | --- |
| **Core** | FastAPI orchestrator — services, adapters, plugins, signals, actions, routes |
| **Lumogis Web** | Primary SPA; LibreChat optional |
| **Data** | **Postgres** (metadata, audit, …), **Qdrant** (vectors); **Ollama** default local embed/LLM |
| **Graph / KG** | **FalkorDB** optional (in-process / **lumogis-graph** out-of-process); **`GRAPH_MODE`**, overlays in **README** |
| **Capabilities / plugins** | Optional packages; manifests, bearer trust (**ADR 010**, **011**); **mock-capability** for contract smoke |
| **MCP** | Core exposes MCP tools (**`/mcp/`** routing per **README**/manual) |
| **Capture / STT** | Planned/shipped facets documented under **`docs/decisions/`** (e.g. **031**) and **`docs/architecture/*speech*`** |
| **Credentials** | Per-user connectors, scopes, UX ADRs (**018**, **027**, **026**, …) |
| **Mobile / PWA / offline** | Programme items exist in Linear (`LUM-77`, etc.) and **architecture** docs — treat as **roadmap**, not assumed shipped |

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
| `/publish-private-main-to-public` | Private `main` → **public export** via scripts + separate checkout |
| `/update-context-pack` | Maintains **this file** from evidence |

---

## Current roadmap / priority snapshot *(export-based; may be stale)*

**Do not treat as live Linear.** Reconcile **`linear-issues-export.json`** (exported **2026-05-07**, **96** issues) with **`linear-roadmap-priority-dashboard-2026-05-03.md`** **§5–§6** after refresh.

From **dashboard §2 “Now”** (curated narrative; reconcile with JSON): emphasis on **LUM-9** / **LUM-10** (plan/exploration ↔ Linear linkage), **LUM-80** / **LUM-81** (human review), **LUM-44** (web umbrella), **LUM-23** (graph stats privacy debt).

**§5 automated scores** highlight **LUM-42**, **LUM-43**, **LUM-64**, **LUM-80**, **LUM-41**, **LUM-56** (dedupe gate), **LUM-27**, **LUM-44**, **LUM-68**, **LUM-81**, **LUM-23**, **LUM-26**, etc., per snapshot — **confirm in JSON**.

**Cautions:** **LUM-56** — **`migration:needs-dedupe`**; **LUM-76** called **Canceled** in dashboard prose (replacement issue if Agentic programme continues); **human-review** and **out-of-scope** rows in dashboard **§7**.

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
| **Orchestrator / unit** | `make test`, `make test-unit`; `cd orchestrator && pytest …` |
| **Integration** | `make compose-test-integration`, `make test-integration` (venv); **`make verify-public-rc`** (hygiene + unit/web + gated integration + UI smoke + export checks per **Makefile**) |
| **Release-scale private gate** | **`make verify-public-rc-full`** (`verify-public-rc` + migrations + full UI gate + optional compose/KG parity lines per **Makefile**) |
| **Lint** | `make lint`, `make compose-lint` |
| **Web** | `make web-test`, `web-lint`, `web-build`, Playwright targets (needs stack + env per **`clients/lumogis-web/README.md`**) |
| **KG** | `make test-kg`, `make compose-test-kg`, `make test-graph-parity` (slow; host env must have pytest for parity target) |
| **Export hygiene** | `scripts/check-main-hygiene.sh`, `scripts/check-protected-release-files.sh`, `scripts/create-upstream-export-tree.sh`, `scripts/check-public-export.sh` |
| **Product OS** | `node scripts/linear/drift_check.mjs` (when applicable); plan/exploration linkage per skills |

---

## New Chat Bootstrap Prompt

Copy-paste:

> I am working on Lumogis. Please use the attached/pasted **Lumogis Context Pack** as the current project context. Treat **Linear** as the active backlog, **repo/devtools files** as evidence, and route workflow advice through the documented **skills**. Do not assume **dev** / **main** / **public** histories are linear. My question is: …

---

## Source-of-truth pointers

- `AGENTS.md` — routing / guardrails
- `docs/LUMOGIS_CONTEXT_PACK.md` — **this** compact orientation (maintained by **`/update-context-pack`**)
- `docs/LUMOGIS_REFERENCE_MANUAL.md` — operator + contributor narrative
- `README.md`, `ARCHITECTURE.md` (repo root)
- `docs/architecture/` — roadmaps, reconciliation, plans
- `docs/decisions/` — ADRs
- `docs/release/public-release-log.md` — correlated private/public snapshot SHAs (private-only tree)
- `docs/release/` (other) — promotion / AGPL workflow (**private repo**)
- `cursor/backlog/linear-operating-model.md` — Product OS taxonomy (devtools)
- `cursor/reports/linear-issues-export.json` — local Linear snapshot
- `cursor/reports/linear-roadmap-priority-dashboard-*.md` — prioritisation dashboard
- `cursor/reports/linear-id-map-*.csv` — ID traceability
- `scripts/linear/README.md` (devtools) — import/drift tooling
- `.cursor/skills/*/SKILL.md` — full workflows

---

## Maintenance rules

- Refresh when **architecture**, **branch/release**, **Product OS**, **skills**, **public/private posture**, or **roadmap-facing** facts change materially.
- **Shrink** stale bullets; do not accumulate noise.
- **No** second backlog here — **Linear** owns actionable work.
- If an update implies new actionable work, record **Linear outcomes** per **`AGENTS.md`**.
- Prefer refreshing **`linear-issues-export.json`** before treating roadmap tables as authoritative.
