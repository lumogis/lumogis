# Lumogis Context Pack

Concise onboarding for AI assistants (**Cursor**, **ChatGPT**, **Claude**). Summarises **repo evidence only** — not Linear live state.

---

## Last refreshed

| Field | Value |
| --- | --- |
| **Date/time** | 2026-05-13 (`/update-context-pack` after LUM-225 / LUM-192 verify + 0.3.0 public release) |
| **lumogis-app** | branch **`dev`**, tip **`e704af5`** (`verify-plan: LUM-225 public-repo GHCR gates (ADR 037)`); private **`origin/main`** tip **`a3b8825`** (same content). |
| **lumogis-devtools** | branch **`main`**, commit **`3bd1800`** (LUM-225 plan/exploration archived, topics updated) |
| **Public / lumogis-public** | **`547f44e`** — `release: Lumogis 0.3.0` (2026-05-13; 52 files, 931 insertions) |

**Evidence sources consulted:**

- `AGENTS.md` (lumogis-app)
- `README.md`, `ARCHITECTURE.md` (**§ *Finding your way around the code*** — diagram-aligned directory map), `CHANGELOG.md`, `docs/capabilities.md`, `docs/LUMOGIS_REFERENCE_MANUAL.md` (lumogis-app)
- `docs/README.md` (**documentation index**: **`guides/`** for self-hosted ops; **`extending/`** for contributors · plugin **`examples/`**)
- `docs/architecture/*.md` (public supplements); maintainer planning often under `docs/private/architecture/` (export-stripped)
- `docs/decisions/*.md` (numbered **001–037**; two distinct `034-*.md` files — ADR number collision — not individually re-read in full)
- `Makefile` (lumogis-app) — test / web / compose / KG / **RC gate** targets
- `cursor/reports/linear-issues-export.json` — **`exportedAt`: `2026-05-13T21:40:41Z`**, team **LUM**, **222** issues (local snapshot; LUM-225 shows Backlog — moved to Done immediately after export, not yet reflected)
- `cursor/reports/linear-roadmap-priority-dashboard-2026-05-03.md` (curated narrative + scoring tables regenerated from 2026-05-13 export in **§5–§6**)
- `cursor/reports/linear-id-map-2026-05-03.csv` (traceability snapshot)
- `cursor/backlog/linear-operating-model.md` (Product OS taxonomy; devtools path via `.cursor` symlink)
- `.cursor/skills/*/SKILL.md` (skill set)

**Freshness / uncertainty:**

- **Linear export** is a **local snapshot** (**2026-05-13**); LUM-225 was moved to **Done** after the export — authoritative state is **Linear**. Refresh: `node scripts/linear/linear_import.mjs --export-issues` (from lumogis-devtools).
- **Roadmap dashboard** narrative sections (§1–§4, §7–§8) are **static / curated** and may reference resolved issues — reconcile with **§5–§6** (regenerated) and fresh JSON.

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
| **Lumogis Web** | Primary SPA; **LibreChat** optional (compose profile) |
| **Data** | **Postgres** (metadata, audit, …), **Qdrant** (vectors); **Ollama** default local embed/LLM |
| **Graph / KG** | **FalkorDB** optional; **in-process** plugin vs **`lumogis-graph`** (**`GRAPH_MODE`**: `inprocess` / `service` / `disabled`); `query_graph` proxy fail-closed without explicit Core secret/opt-in in `service` mode (**ADR 035**) |
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
- **Planned work:** `/create-plan` → `/review-plan` → implement → `/verify-plan` → `/navigator drift` → `/linear-update` (when Thomas applies closure).
- **Unplanned shipped work:** `/record-retro` → `/navigator drift` → `/linear-update`.
- **`/linear-update`**: only skill that **intentionally mutates** Linear — **one issue** at a time, explicit Thomas request only.
- **Actionable follow-ups** need **Linear outcomes** (not markdown-only backlog). **P0** blocks closure; **P1** needs explicit acceptance; **P2/P3** deferred only with recorded outcomes (**`AGENTS.md`**, **`/verify-plan`**).
- **Drift check:** `node scripts/linear/drift_check.mjs` (from lumogis-devtools) — checks plan/exploration ↔ Linear linkage and open-row hygiene.

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
| `/update-context-pack` | Maintains **this file** from evidence |

---

## Current roadmap / priority snapshot *(export-based; may be stale)*

**Do not treat as live Linear.** Prefer a fresh `linear_import.mjs --export-issues` run.

**Local snapshot:** `linear-issues-export.json` — **222** issues, exported **2026-05-13T21:40Z**.

**Recently closed (from export + this session):**
- **LUM-225** ✅ Done (2026-05-13) — GHCR publish moved to public repo, `verify-public-rc` gates, ADR 037
- **LUM-192** ✅ Done — GHCR multi-platform CI + `docker-compose.ghcr.yml` overlay (ADR 036)
- **LUM-223** ✅ Done — lumogis-web TypeScript / Docker build fix
- **LUM-103**, **LUM-102**, **LUM-101** ✅ Done — CI / compose-test / ruff fixes
- **LUM-87**, **LUM-86**, **LUM-85**, **LUM-84**, **LUM-83** ✅ Done — Product OS harness, drift tooling, dashboards

**Active high-priority themes** (P1 from export; verify in Linear):
- **Security / privacy hardening** — LUM-29 (JWT revocation), LUM-31 (CSRF), LUM-23 (graph-stats user_id leak), LUM-190 (security audit), LUM-141 (safety playground), LUM-125 (circuit breakers), LUM-123 (atomic action claims)
- **Pre-launch docs / compliance** — LUM-217 (TELEMETRY.md), LUM-183 (SECURITY.md), LUM-182 (CLA), LUM-55 (licence docs)
- **Web UI / UX** — LUM-28 (Web Push notifications), LUM-22 (LLM per-user dashboard)
- **Credential / session** — LUM-46 (CalDAV/credential UX), LUM-37 (CalDAV signal/lookahead), LUM-35 (backup CSRF/erasure)
- **KG / graph** — LUM-26 (MCP KG gateway), LUM-57 (graph drift detection), LUM-58 ([Explore] NER/REBEL upgrade)

**P2 near-term candidates:** LUM-224 (npm ci Dockerfile), LUM-23 (graph-stats privacy), LUM-128 (Site pattern sessions), LUM-93 (notification settings UI), LUM-54 (test debt graph backfill).

**Cautions:** `migration:needs-dedupe` items (LUM-56 class); human-review rows (LUM-80, LUM-81); `drift_check.mjs` currently exits 1 due to pre-existing WARN in `lumogis_mobile_cloud_fallback_sync.plan.md` — not a new gap introduced by LUM-225 work.

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
- `docs/decisions/` — ADRs (001–037; two 034-* files — collision)
- `docs/release/` — release notes / logs when present
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
- If an update implies new actionable work, record **Linear outcomes** per `AGENTS.md`.
