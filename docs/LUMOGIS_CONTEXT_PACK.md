# Lumogis Context Pack

Concise onboarding for AI assistants (**Cursor**, **ChatGPT**, **Claude**). Summarises **repo evidence only** — not Linear live state.

---

## Last refreshed

| Field | Value |
| --- | --- |
| **Date/time** | 2026-05-14 (`/update-context-pack`; repo evidence + refreshed **local-only** Linear export) |
| **lumogis-app** | branch **`dev`**, tip **`5cb8260`** (`merge: document injection sanitisation (LUM-127) via run-workflow` — **ADR 039** / `feat(orchestrator): …` **`ce7791c`**); includes prior **`dev`** merges **LUM-208** (**RELATES_TO** / **ADR 038**, `33d7e45`) and docs-librarian maintenance (`8b036cc` chain). Private **`origin/main`** tip **`8cd69a3`** (`docs: refresh context pack — LUM-225 / 0.3.0 / ADR 037`) — **behind** **`dev`** on product features merged after that point. |
| **lumogis-devtools** | branch **`main`**, tip **`dc2d132`** (LUM-127 verify-plan artefacts + merge log); this session optionally re-ran **`linear_import.mjs --export-issues`** — see **Freshness**. |
| **Public / upstream** | **`547f44e`** — `release: Lumogis 0.3.0` on **`upstream/main`** (`lumogis/lumogis`; export line, not private lineage) |

**Evidence sources consulted:**

- `AGENTS.md` (lumogis-app)
- `README.md`, `ARCHITECTURE.md` (**§ *Finding your way around the code*** — diagram-aligned directory map), `CHANGELOG.md`, `docs/capabilities.md`, `docs/LUMOGIS_REFERENCE_MANUAL.md` (lumogis-app)
- `docs/README.md` (**documentation index**: **`guides/`** for self-hosted ops; **`extending/`** for contributors · plugin **`examples/`**)
- `docs/architecture/*.md` (public supplements); maintainer planning often under `docs/private/architecture/` (export-stripped)
- `docs/decisions/*.md` (numbered **001–039**; **ADR 038** RELATES_TO directionality; **ADR 039** document injection sanitisation; **two distinct `034-*.md`** files — ADR number collision — not individually re-read in full)
- `docs/_librarian/` — inventory + daily reports when present (e.g. **`2026-05-14-docs-librarian-report.md`**); librarian notice on duplicate **ADR 034** in **`docs/decisions/034-linear-evidence-index.md`**
- `Makefile` (lumogis-app) — test / web / compose / KG / **RC gate** targets
- **`cursor/reports/linear-issues-export.json`** — **gitignored** locally; refreshed snapshot **233** issues, **`exportedAt`:** **`2026-05-14T19:19:39.830Z`** via `node scripts/linear/linear_import.mjs --export-issues` (**lumogis-devtools**). Authoritative status remains **Linear**; file is machine-local unless your team commits a non-ignored mirror.
- `cursor/reports/linear-roadmap-priority-dashboard-2026-05-03.md` (header **§5–§6** regenerated when export runs; filename still **2026-05-03**)
- `cursor/reports/linear-id-map-2026-05-03.csv` (traceability snapshot)
- `cursor/backlog/linear-operating-model.md` (Product OS taxonomy; devtools path via `.cursor` symlink)
- `.cursor/skills/*/SKILL.md` (skill set)
- `node scripts/linear/drift_check.mjs` (this session — operational default)

**Freshness / uncertainty:**

- **`linear-issues-export.json`** is **`.gitignore`d** — teammates need their own **`--export-issues`** run for the same timestamps/counts.
- **Linear vs `dev` mismatch (evidence):** export read during this refresh still lists **LUM-127** as **Backlog** while **`dev`** carries the merged implementation (**`5cb8260`**) — reconcile in **Linear** via **`/linear-update`** when ready.
- **Roadmap dashboard** narrative sections (§1–§4, §7–§8) are **static / curated** — reconcile with **§5–§6** and the JSON.
- **Devtools working tree:** re-export may dirty tracked **`cursor/reports/linear-roadmap-priority-dashboard-2026-05-03.md`** and **`cursor/reports/backlog-dashboard.html`** — commit or discard in **lumogis-devtools** as you prefer.

---

## What Lumogis is

- **Self-hosted, local-first, privacy-first** household / personal AI: data and indexes stay on hardware you control (Docker Compose, **AGPL-3.0-only** per **README** / **ADR 032**).
- **Core** = **FastAPI orchestrator**; **Lumogis Web** = first-party SPA behind **Caddy** (same-origin); **LibreChat** = optional legacy profile, not the primary surface (**ADR 012**).
- **Ask / Do** safety model for actions/tools; auditability and connector/credential boundaries are first-class (**ADR 006**, credential ADRs **018**, **027**, **029**, etc.).
- **Not** Lumogis-operated SaaS — positioning is **family/household LAN** and operator-controlled deployment.
- **Public vs private**: the **public** tree is an **export snapshot**, not a byte mirror of private history (**`AGENTS.md`**). **GHCR images** (`lumogis-orchestrator`, `lumogis-web`) are published **only** from `lumogis/lumogis` (public repo) `main` — not from private `lumogis-app` (**ADR 037**, LUM-225, as of 0.3.0).

---

## Repository model

| Repo / path | Role |
| --- | --- |
| **lumogis-app** | Product: application, tests, Docker, shipping paths |
| **lumogis-devtools** | Cursor skills, Product OS tooling, Linear scripts, **`.cursor/reports`**, registries |
| **lumogis-app/.cursor** | Symlink → **lumogis-devtools/cursor** (plans, skills, rules resolve here) |
| **lumogis-public** / **upstream/main** | Public **AGPL** export line — **not** the same lineage as private `main`; currently at **0.3.0** (`547f44e`) |

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
| **Ingest / prompt-injection hygiene** | **ADR 039** — `injection_sanitiser`, pattern YAML, `<retrieved_chunk>` / `<lumogis_injected_context>` scaffolding on assembled context + tool payloads, ingest **`origin`** metadata on vectors, compaction-trust prefix hooks (**`memory.py`**), per-invocation **`TOOL_CHAIN_CAP`** (**`loop.py`**); operator narrative **`docs/LUMOGIS_REFERENCE_MANUAL.md`** §8 (**env toggles**, **`NullInjectionScanner`** default). |
| **Lumogis Web** | Primary SPA; **LibreChat** optional (compose profile) |
| **Data** | **Postgres** (metadata, audit, …), **Qdrant** (vectors); **Ollama** default local embed/LLM |
| **Graph / KG** | **FalkorDB** optional; **in-process** plugin vs **`lumogis-graph`** (**`GRAPH_MODE`**: `inprocess` / `service` / `disabled`); `query_graph` proxy fail-closed without explicit Core secret/opt-in in `service` mode (**ADR 035**). **ADR 038** — canonical **`RELATES_TO`** projection direction + undirected **`MATCH`** retrieval semantics (`docs/private/kg/kg_reference.md` §2.3 when present). |
| **Capabilities / plugins** | Optional packages; HTTP manifests, bearer trust (**ADR 010**, **011**); **mock-capability** for contract smoke |
| **MCP** | Core `/mcp/` surface; per-user opaque bearer tokens when `AUTH_ENABLED` (**ADR 017**) |
| **Tool catalog** | Read-only unified catalog; `LUMOGIS_TOOL_CATALOG_ENABLED` defaults **off** |
| **Capture / STT** | **Shipped MVP**: `/capture` QuickCapture, `/api/v1/captures` ledger, `POST /api/v1/voice/transcribe` when STT enabled (**ADR 031**) |
| **Credentials** | Per-user, household, and instance-system tiers; encrypted stores; connector Ask/Do per user (**ADR 024**, **027**, …) |
| **Mobile / PWA / Web Push** | **Partial MVP shipped**: responsive mobile UX, PWA manifest + SW (bounded caching — not full offline product), Web Push opt-in + subscription plumbing (**ADR 030**) |
| **GHCR / Docker images** | Multi-platform (`linux/amd64`, `linux/arm64`) images at `ghcr.io/lumogis/lumogis-orchestrator` and `ghcr.io/lumogis/lumogis-web`. Overlay: `docker-compose.ghcr.yml` (uses `build: !reset null`). Published from **`lumogis/lumogis` public repo** only (**ADR 036**, **ADR 037**). |

---

## Product OS and Linear workflow

- **Linear** = active **backlog and status** surface; **repo/devtools** files = **durable evidence**.
- **Planned work:** `/create-plan` → `/review-plan` → implement → `/verify-plan` → **`/merge-workflow`** (when **`run-workflow` / headless verify** deferred Step **12** — devtools commit + plan/archive + **`agent/lum-*` → `dev`**) → `/navigator drift` → `/linear-update` (when Thomas applies closure).
- **Unplanned shipped work:** `/record-retro` → `/navigator drift` → `/linear-update`.
- **`/linear-update`**: only skill that **intentionally mutates** Linear — **one issue** at a time, explicit Thomas request only.
- **Actionable follow-ups** need **Linear outcomes** (not markdown-only backlog). **P0** blocks closure; **P1** needs explicit acceptance; **P2/P3** deferred only with recorded outcomes (**`AGENTS.md`**, **`/verify-plan`**).
- **Drift check:** `node scripts/linear/drift_check.mjs` (from lumogis-devtools) — checks plan/exploration ↔ Linear linkage and open-row hygiene (this refresh: **2** linkage **ERROR**s — **`PLAN_LINKAGE_DRIFT`** / **`EXPLORATION_LINKAGE_DRIFT`** via **`check_*_linear_linkage.mjs`** — plus many markdown WARNs).

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
| `/publish-private-main-to-public` | Private `main` → **public export** (`CHANGELOG` + `capabilities.md` + `make verify-public-rc` gates before push) |
| `/merge-workflow` | **Run-workflow closure** — review + test **`agent/lum-*`**, devtools **`git mv` archive** + commit, merge to **`dev`** (gated), optional push, worktree teardown; **never** calls Linear API |
| `/update-context-pack` | Maintains **this file** from evidence |

---

## Current roadmap / priority snapshot *(export-based; may be stale)*

**Do not treat as live Linear.** Run `node scripts/linear/linear_import.mjs --export-issues` (**lumogis-devtools**) — note **`linear-issues-export.json`** is **gitignored** locally.

**Local snapshot (this refresh):** **233** issues, exported **`2026-05-14T19:19Z`** (`exportedAt` in local JSON).

**Recently merged to `lumogis-app` `dev` (Git evidence — reconcile Linear status separately):**
- **LUM-127** — document injection sanitisation stack + **ADR 039** (`5cb8260` / `ce7791c`); export still showed **Backlog** for **LUM-127** at refresh time → likely needs **`/linear-update`** closure.
- **LUM-208** — **`RELATES_TO`** directionality / projection (**ADR 038**) (`33d7e45` / `fbcee11`).

**Recently closed / high-signal (historical export examples — verify in Linear):**
- **LUM-225** — GHCR publish from public repo only (**ADR 037**)
- **LUM-223** — lumogis-web TypeScript / Docker build fix
- **LUM-87** / **LUM-86** / harness cluster — Product OS / drift / evidence index tooling

**Active high-priority themes** (patterns from dashboard + export; **verify in Linear**):
- **Security / privacy** — LUM-141 (safety playground; **blocked_by** chain with **LUM-127** in export), LUM-125 (circuit breakers), LUM-29 / LUM-31 / LUM-23 class
- **Pre-launch docs / compliance** — LUM-217, LUM-183, LUM-182, LUM-55
- **Web / credentials / KG** — LUM-44 programme, LUM-28, LUM-46 / LUM-37, LUM-26 / LUM-57 / LUM-58

**Cautions:** `migration:needs-dedupe` (**LUM-56** class); human-review rows (**LUM-80**, **LUM-81**); **`drift_check.mjs`** linkage **ERROR**s (**plan** / **exploration** registry — investigate checker output).

---

## Key hard rules

- No **secrets** in repo, chats, or this pack (no `.env` values, tokens, keys).
- No **`cursor/settings.json`**, `__pycache__`, `node_modules`, `.pytest_cache` in commits (unless explicitly intended).
- **No wholesale `dev` → `main`** unless Thomas requests full dev promotion.
- **No** pushing private `main` **directly** to public — export scripts only.
- **No** ad-hoc Linear mutation — **`/linear-update`** or approved scripts only.
- **No** markdown-only future work as system of record — **Linear outcomes** for actionable items.
- **GHCR publish only from `lumogis/lumogis`** — do not add a `publish-image.yml` back to `lumogis-app` without a conscious decision and ADR revision.
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
| **Compose policy** | `make compose-policy-check` (also: `compose-policy-check-baseline`, `…-adversarial`, `…-adversarial-envfile` when checker/fixtures touched) |
| **RC gates** | `make verify-public-rc` — smoke gate (chain: hygiene → compose-policy → compose-test → web lint/test/build → integration → export check); `make verify-public-rc-full` — adds Playwright e2e + optional graph parity. **Run on private `main` before `/publish-private-main-to-public`**. `VERIFY_PUBLIC_RC_SKIP_WEB_CODEGEN_CHECK=1` escape if OpenAPI URL unavailable. |
| **Product OS** | `node scripts/linear/drift_check.mjs`; `node scripts/linear/check_linear_evidence_index.mjs` (from lumogis-devtools; read-only) |
| **Release / public** | `scripts/check-main-hygiene.sh`, `scripts/check-public-export.sh <export-dir>`, `scripts/create-upstream-export-tree.sh`; skill `/publish-private-main-to-public`; `docs/testing/automated-test-strategy.md` for layer matrix |

---

## New Chat Bootstrap Prompt

Copy-paste:

> I am working on Lumogis. Please use the attached/pasted **Lumogis Context Pack** as the current project context. Treat **Linear** as the active backlog, **repo/devtools files** as evidence, and route workflow advice through the documented **skills**. Do not assume **dev** / **main** / **public** histories are linear. My question is: …

---

## Source-of-truth pointers

- `AGENTS.md` — routing / guardrails
- `docs/LUMOGIS_CONTEXT_PACK.md` — **this** compact orientation (maintained by **`/update-context-pack`**)
- `docs/README.md` — documentation index (`guides/`, `extending/`, ADRs, release, `private/` pointers)
- `docs/guides/` — self-hoster ops (GPU, troubleshooting, connector credentials, export format, structured logging)
- `docs/extending/` — `extending-the-stack.md` + `examples/example_plugin/` template
- `docs/LUMOGIS_REFERENCE_MANUAL.md` — operator + contributor narrative
- `docs/capabilities.md` — short shipped-capability overview (public-facing)
- `CHANGELOG.md` — release history (public-facing; gated before public publish)
- `docs/testing/automated-test-strategy.md` — test layers, dev vs `main` / RC expectations
- `README.md`, `ARCHITECTURE.md` (repo root)
- `docs/architecture/` — public architecture supplements
- `docs/decisions/` — ADRs (001–039; **038** RELATES_TO; **039** injection sanitisation; two **034-*** collision)
- `docs/release/` — release notes / logs when present
- `cursor/backlog/linear-operating-model.md` — Product OS taxonomy (devtools)
- `cursor/reports/linear-issues-export.json` — local Linear snapshot (**gitignored**; regenerate via **`linear_import.mjs --export-issues`**)
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
