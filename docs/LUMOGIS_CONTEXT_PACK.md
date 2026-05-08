# Lumogis Context Pack

Concise onboarding for AI assistants (**Cursor**, **ChatGPT**, **Claude**). Summarises **repo evidence only** — not Linear live state.

---

## Last refreshed

| Field | Value |
| --- | --- |
| **Date/time** | 2026-05-06 (authoring pass; no UTC clock in body) |
| **lumogis-app** | branch `dev`, commit **`d7c42be`** (short) |
| **lumogis-devtools** | branch **`main`**, commit **`3eebbb6`** (short; skills including `/update-context-pack`) |
| **Public / lumogis-public** | Not verified in this pass (optional checkout / `upstream/main`; see **`AGENTS.md`**) |

**Evidence sources consulted:**

- `AGENTS.md` (lumogis-app)
- `README.md`, `docs/LUMOGIS_REFERENCE_MANUAL.md` (lumogis-app)
- `docs/architecture/*.md` (paths present; see **Architecture snapshot**)
- `docs/decisions/*.md` (34 ADR files on disk; not individually re-read in full)
- `Makefile` (lumogis-app) — test / web / compose / KG targets
- `cursor/reports/linear-issues-export.json` — **`exportedAt`: `2026-05-04T18:39:28.373Z`**, team **LUM**, **84** issues
- `cursor/reports/linear-roadmap-priority-dashboard-2026-05-03.md` (mirrors that export in header)
- `cursor/reports/linear-id-map-2026-05-03.csv` (traceability snapshot)
- `cursor/backlog/linear-operating-model.md` (path per maintainer layout; not re-opened line-by-line)
- `.cursor/skills/*/SKILL.md` (skill set; details live in each file)

**Freshness / uncertainty:**

- **Linear export is ~2 days old** relative to pack date; **authoritative issue state** is **Linear** + a fresh export. Refresh (human-run, from **lumogis-devtools**):
  `node scripts/linear/linear_import.mjs --export-issues`
  Optional drift script: `node scripts/linear/drift_check.mjs`
- **`docs/release/public-release-log.md`** was **not found** in this **lumogis-app** tree at refresh time; use **`docs/release/*`** and release skills when present.
- Roadmap dashboard **§1–§2, §7–§8** are **curated** and may lag **§5–§6** + JSON — reconcile per dashboard header (**`linear-roadmap-priority-dashboard-2026-05-03.md`**).

---

## What Lumogis is

- **Self-hosted, local-first, privacy-first** household / personal AI: data and indexes stay on hardware you control (Docker Compose, **AGPL-3.0-only** product line per **README** / **ADR 032**).
- **Core** = **FastAPI orchestrator**; **Lumogis Web** = first-party SPA behind **Caddy** (same-origin); **LibreChat** = optional legacy OpenAI-style UI (compose profile), not the primary multi-user surface (**reference manual** / **ADR 012**).
- **Ask / Do** safety model for actions/tools; auditability and connector/credential boundaries are first-class (**`docs/decisions/006-ask-do-safety-model.md`**, credential ADRs **018**, **027**, **029**, etc.).
- **Not** a Lumogis-operated SaaS; positioning is **family/household LAN** and operator-controlled deployment.
- **Public vs private**: **public** tree is an **export snapshot**, not a byte mirror of private history (**`AGENTS.md`**).

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
- **`/publish-private-main-to-public`**: generate export via project scripts; **never** raw-push private `main` to public.
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
| `/publish-private-main-to-public` | Private `main` → **public export** |
| `/update-context-pack` | Maintains **this file** from evidence |

---

## Current roadmap / priority snapshot *(export-based; may be stale)*

**Do not treat as live Linear.** Use **`linear-issues-export.json`** + **`linear-roadmap-priority-dashboard-2026-05-03.md` §5–§6 after refresh.

From **dashboard §2 “Now”** (curated narrative, same export era): emphasis on **LUM-9** / **LUM-10** (plan/exploration ↔ Linear linkage), **LUM-80** / **LUM-81** (human review), **LUM-44** (web umbrella), **LUM-23** (graph stats privacy debt).

**§5 top scores** (automated table, same snapshot) highlight **LUM-42**, **LUM-43** (capability security / compose guard), **LUM-64**, **LUM-80**, **LUM-41**, **LUM-56** (dedupe gate), **LUM-27**, **LUM-44**, **LUM-68**, **LUM-81**, **LUM-23**, **LUM-26** (MCP/KG gateway), etc.

**Cautions:** **LUM-56** — **`migration:needs-dedupe`**; **human-review** and **out-of-scope** rows called out in dashboard **§7**.

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
| **Product OS** | `node scripts/linear/drift_check.mjs` (when applicable); plan/exploration linkage per skills |
| **Release / public** | Gates named in **`/prepare-private-release-from-dev`** and **`/publish-private-main-to-public`** (e.g. `verify-public-rc-full` **when defined** in your Makefile — **not** present in the scanned **Makefile** on this tree; follow skill + script docs) |

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
