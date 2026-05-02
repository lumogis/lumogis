# Lumogis automated testing strategy

This document is the **permanent** Lumogis test strategy for private **`main`**. It complements release-mechanics docs under `docs/release/` (which stay focused on RC promotion, export, and upstream publishing).

---

## A. Purpose

- **Permanent test platform:** The targets, tiers, and policies here live on **`main`** after RC merges and evolve with the product.
- **Extend with features:** Every meaningful feature should extend the automated suite with an appropriate layer (unit, integration, web, Playwright, migrations, hygiene).
- **Goal:** Make **manual app clicking exceptional**, not routine — operators and contributors rely on deterministic gates; exploratory QA stays for edge cases and UX polish.

---

## B. Test layers (tiers)

Each tier has a role in the pyramid from cheap hygiene to optional heavy checks.

### Tier 0 — hygiene / export / release safety

| | |
|--|--|
| **Purpose** | Catch licence/SPDX drift, forbidden paths, protected release files, and public-export shape **without** running the product stack. |
| **Examples** | `scripts/check-main-hygiene.sh`, `scripts/check-protected-release-files.sh`, `scripts/create-upstream-export-tree.sh` + `scripts/check-public-export.sh` |
| **Make target** | Invoked from **`make verify-public-rc`** / **`verify-public-rc-full`** (not always a standalone `make` target). |
| **Docker** | No. |
| **Seed data** | No. |
| **Required vs optional** | **Required** for merge/release posture (`verify-public-rc`). |

### Tier 1 — backend / service unit and import tests

| | |
|--|--|
| **Purpose** | Fast feedback on orchestrator, stack-control, mock-capability, **lumogis-graph**, and related Python contracts **without** Docker. |
| **Examples** | Orchestrator `tests/`, stack-control `test_main.py`, `services/lumogis-mock-capability/tests`, `services/lumogis-graph` pytest |
| **Make target** | **`make test-unit`** |
| **Docker** | No (local `.venv` gate). |
| **Seed data** | No. |
| **Required vs optional** | **Required** (`verify-public-rc`). |

### Tier 2 — web unit / build

| | |
|--|--|
| **Purpose** | Lint, typecheck, Vitest, and production build for **Lumogis Web**; ensures client logic and bundle health. |
| **Examples** | OpenAPI codegen from snapshot, `npm run lint`, `npm test`, `npm run build` |
| **Make target** | **`make test-web`** |
| **Docker** | No (Node on host/CI). |
| **Seed data** | No. |
| **Required vs optional** | **Required** (`verify-public-rc`). |

### Tier 3 — Compose-backed integration tests

| | |
|--|--|
| **Purpose** | Exercise **real services**, DBs, Caddy/front door, and cross-service behaviour under **`docker-compose.test.yml`** (or equivalent test profile). |
| **Examples** | Host pytest integration suites driven by **`scripts/integration-public-rc.sh`** (`gate-pytest`), `public_rc` markers, negative-path integration |
| **Make target** | **`make test-integration`** (full compose cycle); **`verify-public-rc`** uses **`gate-start`** → **`gate-pytest`** → UI → **`gate-end`** |
| **Docker** | **Yes** — isolated compose project (e.g. **`lumogis-test`**). |
| **Seed data** | Smoke bootstrap / fixtures as documented in **`config/test.env.example`** and integration helpers; no personal accounts. |
| **Required vs optional** | **Required** for product gate (`verify-public-rc`). |

### Tier 4 — Playwright desktop / mobile smoke

| | |
|--|--|
| **Purpose** | Real browser smoke against **Lumogis Web** via **`PLAYWRIGHT_BASE_URL`** — route coverage, gate UI projects, **desktop + mobile viewports** where applicable. |
| **Examples** | `e2e:gate-ui` (see **`clients/lumogis-web`** package scripts) |
| **Make target** | **`make test-ui`** (bring stack up → **`make test-ui-existing-stack`** → tear down); **`test-ui-existing-stack`** when compose already running |
| **Docker** | **Yes** — stack must be up (gate manages lifecycle for **`test-ui`**). |
| **Seed data** | Tier 3 stack defaults; **signed-in not required** for the gate UI tier. |
| **Required vs optional** | **Required** (`verify-public-rc`). |

### Tier 5 — signed-in / full Playwright clickthrough

| | |
|--|--|
| **Purpose** | Deeper workflows: navigation, actions, and authenticated paths using **seeded smoke credentials** — catches regressions smoke routes miss. |
| **Examples** | `e2e:full` suites after **`scripts/seed-public-rc-smoke-user.sh`** |
| **Make target** | **`make test-ui-full`** (start stack → seed smoke user → **`test-ui-full-existing-stack`** → end); **`test-ui-full-existing-stack`** when stack + seed already done |
| **Docker** | **Yes**. |
| **Seed data** | **Yes** — documented placeholders in **`config/test.env.example`** (`LUMOGIS_WEB_SMOKE_*`, bootstrap admin); seed script refuses wrong compose project name. |
| **Required vs optional** | **Full gate** (`verify-public-rc-full`); promote into required CI when stable and resource-worthy. |

### Tier 6 — optional / nightly / heavy

| | |
|--|--|
| **Purpose** | Docker-heavy image tests, graph parity, slow integration tails — **do not block** every PR if cost/flake dominated. |
| **Examples** | **`make compose-test`**, **`make compose-test-kg`**, **`make test-graph-parity`**, live-provider suites |
| **Make target** | Invoked as **optional tails** after **`verify-public-rc`** inside **`make verify-public-rc-full`** (best-effort `-` recipes may apply). |
| **Docker** | **Yes** (often exclusively). |
| **Seed data** | Fixture-dependent per suite. |
| **Required vs optional** | **Optional / nightly** unless explicitly promoted by policy. |

---

## C. Standard Make targets

| Target | Role |
|--------|------|
| **`make test-unit`** | Tier 1 — backend and service unit tests (venv + pytest across orchestrator, stack-control, mock-capability, lumogis-graph). |
| **`make test-web`** | Tier 2 — Lumogis Web install, codegen, lint, Vitest, production build. |
| **`make test-integration`** | Tier 3 — full **`scripts/integration-public-rc.sh full-cycle`** (compose up/down + pytest). |
| **`make test-ui`** | Tier 4 — **`gate-start`** → Playwright gate UI (**`test-ui-existing-stack`**) → **`gate-end`**. |
| **`make test-ui-full`** | Tier 5 — **`gate-start`** → **`scripts/seed-public-rc-smoke-user.sh`** → **`test-ui-full-existing-stack`** → **`gate-end`**. |
| **`make test-migrations`** | Fresh-DB migration discipline via **`scripts/check-migrations-fresh-db.sh`** (part of **`verify-public-rc-full`**). |
| **`make verify-public-rc`** | **Required product gate:** hygiene + protected files + **`test-unit`** + **`test-web`** + compose **`gate-pytest`** + **`test-ui-existing-stack`** + export tree + **`check-public-export`**. |
| **`make verify-public-rc-full`** | **Full gate:** **`verify-public-rc`** + **`test-migrations`** + **`test-ui-full`** + optional **`compose-test`**, **`compose-test-kg`**, **`test-graph-parity`**. |

**Variants:** **`make test-ui-existing-stack`** / **`make test-ui-full-existing-stack`** assume the RC compose stack is already running (for iterative debugging).

---

## D. Full product stack principle

Automated coverage should **eventually** touch (and regressions should be detectable at the right tier for):

- **Core / orchestrator**
- **`services/lumogis-graph`** (KG service)
- **`services/lumogis-mock-capability`** (mock capability surface)
- **Lumogis Web** (`clients/lumogis-web`)
- **Caddy / front door** (routing and TLS assumptions as exercised in compose tests)
- **Postgres, Qdrant, FalkorDB** (as wired in test compose)
- **MCP / capability layer** (contracts and mock paths)
- **KG** in **in-process** and **service** modes (parity where policy demands)
- **Captures / media** flows covered by integration or UI tests as features land
- **STT** via **mocked** paths in required gates (not live Whisper)
- **PWA / offline basics** where user-visible
- **Public export hygiene** (Tier 0 — scripts and forbidden-path discipline)

---

## E. Determinism rules

**Required gates must not depend on:**

- Live **Ollama** output (non-deterministic generations)
- Live **Whisper** transcription
- Real **Web Push** delivery
- External **CalDAV**, **ntfy**, **RSS**
- Real user **secrets** or **personal accounts**

**Required suites should use:**

- **Seeded smoke user** (documented test-only credentials)
- **Seeded fixtures** and stable test data
- **Mock capability service** (`lumogis-mock-capability`) where appropriate
- **Fake/stub providers** for externals
- **Isolated compose project** (predictable naming, no collision with dev stacks)
- **`config/test.env.example`** as the documented baseline for env defaults (`scripts/rc_test_env_defaults.py` merges with overrides)

---

## F. Feature test policy

For **every new feature**, extend automation according to this checklist:

| Change type | Add or update |
|-------------|----------------|
| Backend / API behaviour | **Backend unit or API test** (Tier 1 or orchestrator tests). |
| Services / DB / compose interaction | **Integration test** (Tier 3). |
| UI logic / components | **Web unit / Vitest** (Tier 2). |
| User-visible workflow | **Playwright** — desktop **and** mobile where the feature is surfaced (Tiers 4–5). |
| DB schema | **Migration test** (`**make test-migrations**` discipline + targeted migration tests if needed). |
| Public release surface | **Hygiene/export** scripts or fixtures (**Tier 0**). |
| Operator / developer behaviour | **Docs** (this file, README, or `config/test.env.example` — not only release mechanics). |

---

## G. Examples (apply §F)

| Scenario | Typical additions |
|----------|-------------------|
| **New capture attachment feature** | API/unit tests for upload/metadata; integration test with DB + storage mock path; Vitest for UI state; Playwright for attach → visible → round-trip (desktop + mobile). |
| **New connector** | Unit tests for adapter; integration test with stub HTTP/server or recorded fixtures; avoid live external APIs in required gates. |
| **New KG/graph feature** | Graph service tests + orchestrator contract tests; integration scenario with FalkorDB; optional **`test-graph-parity`** if behaviour must match across **`GRAPH_MODE`**. |
| **New admin screen** | Vitest for forms/tables; Playwright for navigation + critical actions (smoke user / admin seed path). |
| **New auth / security flow** | Unit tests for token/session logic; integration for cookie/header behaviour; Playwright for login/logout and protected routes (Tier 5). |
| **New PWA/offline feature** | Vitest for offline hooks/service worker helpers where unit-testable; Playwright for offline/simulated offline scenarios appropriate to CI stability. |

---

## H. Required vs full gate

- **`make verify-public-rc`** — **Deterministic required product gate:** hygiene, unit, web, compose pytest (`public_rc` integration scope), Playwright **gate UI**, export tree verification. This is the baseline **merge-quality** bar for RC → **`main`**.
- **`make verify-public-rc-full`** — **Required gate plus deeper/heavier checks:** adds **`test-migrations`**, **seeded full Playwright** (`test-ui-full`), and **optional** tails such as **`compose-test`**, **`compose-test-kg`**, **`test-graph-parity`** (graph parity across modes, docker image tests, signed-in deep navigation — subject to Makefile “optional” conventions).

Treat **`verify-public-rc-full`** as the **release-hardening** superset; **`verify-public-rc`** stays the **daily deterministic** contract.

---

## I. Playwright policy

- **Desktop and mobile:** Both viewports are expected for **user-visible** features unless explicitly desktop-only.
- **Selectors:** Prefer **`data-testid`** for stable hooks; avoid brittle **copy/text** selectors that break on i18n or copy tweaks.
- **Stability:** Assert **no fatal page errors** and **no critical same-origin 5xx** on exercised flows.
- **Auth:** Signed-in flows use **seeded smoke credentials** from **`config/test.env.example`** — never contributor personal accounts.
- **Depth:** **Route smoke alone is not enough** for core workflows — add **clickthrough/action tests** when behaviour matters (Tier 5).

---

## J. CI policy

- **Fast paths:** Hygiene, **`test-unit`**, **`test-web`** (and optionally lightweight integration slices) can run on **every PR/push**.
- **Docker / Playwright full RC gate:** May run on **`workflow_dispatch`**, **scheduled** windows, or dedicated jobs until **cost and flake rate** are acceptable for mandatory blocking status.
- **`main` discipline:** **`main`** should **not** rely on **manual local-only** testing forever — automation on **`main`** is the source of truth for regressions.
- **Future goal:** Promote **`verify-public-rc`** (or equivalent CI aggregation) into **required** GitHub checks when runner capacity and reliability allow.

---

## K. Optional / heavy tests

Keep **optional or nightly** unless explicitly promoted:

- Live **Ollama**
- Live **Whisper** STT
- Live **Web Push**
- External **CalDAV** / **ntfy** / **RSS**
- **Visual regression** (screenshot baselines)
- Long-running **graph parity** when **cost or duration** precludes every PR

---

## L. Maintenance rules

- **Flaky tests** must be **fixed**, **quarantined with documented reason**, or **moved to full/nightly** — not silently ignored.
- **Do not weaken** hygiene or export scripts to “green” tests — fix product or fix tests.
- **No tracked real secrets**; use examples and CI secrets only where appropriate.
- **No root-owned caches** in the repo; respect `.gitignore` for artifacts.
- **Generated artifacts** must be **ignored** or **deterministic** and committed only when intentional (e.g. OpenAPI snapshot policy).
- **Test failures** must be **classified** (product bug vs test bug vs env) before bypassing or skipping.

---

## References (implementation)

- **`Makefile`** — authoritative target wiring.
- **`config/test.env.example`** — test env defaults and smoke placeholders.
- **`scripts/integration-public-rc.sh`** — compose lifecycle and pytest phases.
- **`scripts/seed-public-rc-smoke-user.sh`** — idempotent smoke user for Tier 5.
- **`docs/release/rc-dev-clean-snapshot-plan.md`** — RC mechanics only; points here for **permanent** testing strategy.

---

*This document is the living contract for Lumogis automation on **`main`**; update it when tiers, targets, or CI policy change.*
