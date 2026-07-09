# Changelog

All notable changes to this project are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions follow [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

---

## [0.9.5] — 2026-07-09

### Fixed

- **CI (private and public)** — lumogis-graph stats privacy tests mock `fetch_one` for LUM-577 `allows_shared` DB lookups; purge sweeper graph projection test skips when the premium `plugins.graph.writer` module is absent on the AGPL export.

---

## [0.9.4] — 2026-07-09

### Fixed

- **Public repository CI** — doctor integration reaches Postgres inside the compose network (overlay pins `POSTGRES_HOST=postgres`); Lumogis MCP stdio smoke tests use shared fast-start lifespan stubs; document-chat injection tests skip premium graph webhook patching when the AGPL export omits that module.

---

## [0.9.3] — 2026-07-09

### Fixed

- **Public repository CI** — doctor integration skips Ollama model pulls on GitHub Actions; Lumogis MCP stdio tests no longer block on Postgres remap during in-process startup; export-tree pytest skips when run from the published AGPL snapshot (no private export templates in-tree).

---

## [0.9.2] — 2026-07-09

### Fixed

- **Public repository CI** — pytest collection on the AGPL export tree no longer imports premium-only graph integration tests; doctor integration startup is more reliable on slow CI hosts (graph disabled in the doctor overlay, extended compose wait); Lumogis MCP stdio integration tests tolerate slower uvicorn bind on GitHub Actions.

---

## [0.9.1] — 2026-07-09

### Fixed

- **Public repository CI** — GitHub Actions on the AGPL export tree passes orchestrator lint, backup round-trip (Postgres + Qdrant when the premium FalkorDB overlay is absent), and doctor integration startup checks again after the 0.9.0 publish.

---

## [0.9.0] — 2026-07-09

### Added

- **Household document sharing** — owners can share library documents with the household; shared chunks become searchable and usable in document-chat for other members; large shares run as background jobs with honest partial-success reporting.
- **Household entity sharing** — owners can publish extracted entities from Search; when graph mode is enabled, shared documents also cascade entities into shared Postgres, Qdrant, and (in service graph mode) FalkorDB with refcounted retraction on unshare or purge.
- **Household conversation sharing** — share a conversation summary with the household from chat; members can discover shared threads through the existing sharing surfaces.
- **Household invite flow** — admins mint single-use invite links; new members redeem, set credentials, and complete an optional welcome onboarding step; admins can gate whether invitees may access shared scope.
- **Household admin panel** — member and admin counts, last-active column, promote/demote with confirmation, and self-guard rails on disable and delete.
- **Member audit log** — **`/audit`** for all authenticated users with date presets, event-type filters, pagination, and privacy or cloud markers on external-call rows.
- **Cloud LLM privacy mode** — hard local-only enforcement at the provider chokepoint; fresh installs default to local-only; per-user further restriction and instance-level admin lock; blocked remote attempts are audit-logged; plan or complex flows fall back to local Ollama with a warning.
- **MCP stdio bridge for Cursor** — new AGPL client package **`clients/lumogis-mcp/`** (`lumogis-mcp`) forwards MCP tool calls to Core's Streamable HTTP endpoint; **`make lumogis-cursor-install`** merges server config into **`~/.cursor/mcp.json`**.
- **MCP memory write surface** — **`add_memory`**, **`add_entity`**, and **`add_relation`** on **`/mcp/`** persist into Postgres and Qdrant with optional graph projection; **`forget`**, **`update_observation`**, and **`checkpoint`** support reversible archive and supersede flows.
- **MCP `recall` fusion tool** — read tool combining semantic, BM25 keyword, one-hop graph, and temporal validity filters with reciprocal rank fusion and optional cross-encoder rerank.
- **MCP token scopes** — mint-time **`mcp:read`** / **`mcp:write`** selection; the Web UI defaults to read-only unless write is explicitly granted; omitted scopes now mint read-only tokens instead of unrestricted ones.
- **Multi-bank memory isolation** — separate **`coding`**, **`personal`**, and **`default`** banks for MCP memories and graph projections.
- **Document chat mode** — scoped **`POST /api/v1/chat/completions`** with optional **`document_id`**, citation metadata on responses, and Lumogis Web route **`/documents/:documentId/chat`** with a context strip.
- **Ingest job progress** — per-stage progress (extract → chunk → embed → graph) via poll endpoints and SSE on the events stream; uploads return **`job_id`**; the upload panel shows multi-file batch progress.
- **Software update visibility** — admin System status card comparing the running version to the latest GitHub release; operator **`scripts/update/`** pull and rollback helpers with migration preview (**`make migrate-dry-run`**).
- **Feature-flag registry** — experimental subsystems gated by **`LUMOGIS_FF_*`** env vars (all default off) with a read-only admin visibility endpoint.
- **Household biography conflict review** — divergent shared-scope facts surface for admin review with represent-both, confirm-one, keep-both, and dismiss outcomes; losers are archived, not deleted.
- **LLM circuit breaker** — consecutive-failure breaker on the central LLM provider path to limit runaway spend when a model or upstream is down.
- **Lumogis Search Wayland recovery** — CLI **`--toggle`** / **`--show`** / **`--hide`**, cold-start show-once, and compositor-keybinding guidance when in-app global hotkeys fail on Wayland.
- **`EVALUATION.md`** — public operator self-evaluation guide at repo root on AGPL export; linked from **`README.md`**.

### Changed

- **Household RBAC hardening** — scope publish and unpublish routes require authenticated users; **`last_seen_at`** is tracked for admin visibility.
- **Privacy mode defaults** — new installs start local-only; existing cloud-using installs are migrated to explicit opt-in via settings migration.
- **MCP tool annotations** — every Core and KG MCP tool advertises MCP **`ToolAnnotations`** hints so clients can auto-approve safe read-only tools.
- **MCP `forget` / `update_observation`** — archived memories also purge projected FalkorDB relationships on the correct bank graph when graph mode is enabled.
- **User data export/import** — FalkorDB export covers all configured banks; legacy combined export format is retained for backward compatibility.
- **`make doctor`** — versioned core-service allowlist manifest, **`--fix`** Makefile shortcuts, and restart-loop guard on automated compose restarts.
- **Unscoped v1 chat context** — **`POST /api/v1/chat/completions`** without **`document_id`** now receives session memory, optional auto-RAG, and graph snippets consistent with legacy chat posture.
- **Web UX polish** — shared loading skeletons, plain-language error states with retry, and a non-admin service-health banner on chat when Ollama, Qdrant, or graph paths degrade.

### Fixed

- **Document re-ingest orphans** — sparse Qdrant chunks left after partial ingest blocks are cleared via payload-scoped delete before re-indexing.
- **Share / unshare races** — rapid toggle no longer drops an unshare intent when a share job is still in flight.
- **Shared-scope visibility for personal-only members** — knowledge-graph Qdrant and Cypher filters honour **`allows_shared=false`** in parity with orchestrator paths.
- **Conversation purge leftovers** — background sweeper retries failed Qdrant or graph arms for partially purged conversations.
- **Audit log routing** — browser navigations to **`/audit`** serve the Lumogis Web SPA instead of the legacy JSON API route.
- **Shared entity badges on Search** — the entity list refreshes after publish or unpublish so household badges stay in sync with the card.
- **LLM circuit breaker streaming** — successful streamed replies no longer leave a false consecutive-failure streak that could open the breaker.

### Removed

- **Dead admin Mint MCP-token control** — the Lumogis Web admin MCP-tokens view no longer exposes a Mint button for a route that was never implemented; minting remains self-service at **`/me/mcp-tokens`**.

### Security

- **Deterministic local-only LLM routing** — remote models are blocked, hidden from **`/v1/models`**, and audit-logged when privacy mode is on.
- **MCP Origin-header DNS-rebinding guard** — Core **`/mcp/*`** rejects non-localhost **`Origin`** headers with **`403`** before token validation.
- **Qdrant loopback-only host publish** — default Compose binds the Qdrant host port to **`127.0.0.1`** so the unauthenticated vector HTTP API is not reachable on the LAN.
- **Legacy `users.refresh_token_jti` column** — dropped; refresh state lives only in **`auth_sessions`** and **`users.token_version`**.

---

## [0.8.0] — 2026-06-15

### Added — scheduled Postgres, Qdrant, and optional FalkorDB snapshots via a Compose backup sidecar; operator commands **`make backup`**, **`make backup-verify`**, **`make backup-prune`**, and **`make restore SNAPSHOT=…`**; integrity manifests and 7-daily / 4-weekly retention; operator guide **[`docs/guides/backup-restore.md`](docs/guides/backup-restore.md)**.
- **Admin backup status** — read-only **Disaster recovery backup** panel on **System status** (last verified snapshot, age, size, store coverage, stale warning).
- **Notification routing (v1)** — in-process dispatcher with per-user preference storage, ntfy / Web Push / in-app SSE channel adapters, and producer migration off ad-hoc notification hooks.
- **Notification preferences in Lumogis Web** — editable per-notification-type × per-channel matrix under **Me → Notifications** with optimistic saves.
- **Admin Ollama management (typed v1 API)** — discovery, async model pull with job polling, and model delete from the **System status** panel via **`/api/v1/admin/ollama/*`** (legacy HTML dashboard routes remain as thin delegates).
- **Lumogis Search overlay** — system tray with **Show Lumogis** and **Quit** for discoverable summon/recovery alongside the global hotkey; refreshed settings panel layout and design tokens; admin ingest-path editing and restart-required banner from the overlay; Playwright and WebdriverIO e2e coverage for overlay flows.
- **Cold-start embedding readiness** — orchestrator waits for embedding model availability and can bootstrap Qdrant collections after restarts when ingest paths need re-indexing.
- **Backup integration CI** — compose-backed smoke for backup scripts and retention policy.

### Changed

- **Admin System status** — combines stack health, Ollama pull/delete controls, and DR backup visibility in one panel.
- **Legacy `POST /backup`** — documented and positioned as a lightweight logical JSON export; **`make backup`** is the canonical disaster-recovery path.
- **Public export hygiene** — Lumogis Search remains the sole shipped desktop client tree; proprietary server-only sources stay out of the AGPL export.

### Fixed

- **Conversation transcript sync** — further hardening for client-minted thread IDs and purge invariants (carried from prior release line).
- **Public RC verification** — integration gate tolerates co-located dev stacks when the test Compose project owns host ports; full gate includes Playwright prove mode after Caddy auth readiness.

### Security

- **Backup artefacts** — instance-scoped snapshots live under operator-controlled host paths; restore requires explicit confirmation and quiesces Core before store writes.

---

## [0.7.1] — 2026-06-15

### Added

- **`CONTRIBUTING-BEGINNERS.md`** in the public AGPL tree — step-by-step onboarding for first-time contributors, including a copy-paste agent prompt; root copy is produced by the export pipeline from `docs/public-export/`.
- **Persona A distribution documentation** — Persona A/B/C matrix in the reference manual, Lumogis Search install how-to for Docker-track self-hosters, and cross-links from the root README and capabilities overview.

### Changed

- **`CONTRIBUTING.md` extractor how-to** — aligned with live `@extractor(".ext")` registration in Core (canonical example: `orchestrator/adapters/pdf_extractor.py`).

### Fixed

- **Conversation transcript sync** — `PUT /api/v1/conversations/{id}` upserts the `web_conversations` header for client-minted thread IDs so debounced message sync no longer fails silently; purged conversations stay blocked after hard delete.

---

## [0.7.0] — 2026-06-06

### Added

- **Lumogis Search desktop overlay** at **`clients/lumogis-search/`** — AGPL household memory search client (global hotkey, **`GET /api/v1/memory/search`**, OS keychain session, role-gated admin ingest paths), in-webview first-run onboarding (server URL → **`/healthz`** → auth → search), and **`overlay.json` schema v2** with **`onboardingComplete`**. Build with **`make search-dev`** / **`make search-build`**.
- **Conversation history in Lumogis Web** — browse, continue, and delete past conversations with server-backed session lists and multi-store purge APIs.
- **Admin stack health** — read-only **System status** panel combining curated admin diagnostics with stack-control service rows.
- **First-run onboarding** in Lumogis Web — skippable orientation modal (per-user completion timestamp) plus a shared empty state for the chat zero-state.
- **First wow moment** — guided first-query and entity-discovery cards on chat after onboarding and when entities are ready; server wow-state APIs and live readiness hints over the events stream.
- **Inbox folder-watch hardening** — configurable inbox path and mode (`event` | `poll` | `off`), write-stability before ingest, poll fallback, quarantine for terminal failures, and inbox liveness on **`/healthz`** without exposing absolute paths.
- **`make doctor`** — read-only operator health CLI (Compose status/config, `.env` grammar checks, optional **`/healthz`** probes, optional JSON output). See **`scripts/doctor/README.md`**.
- **First-run quickstart** — **[`docs/deployment/quickstart.md`](docs/deployment/quickstart.md)** for the published GHCR image path; **`README.md`** cross-links for discoverability.
- **paperless-ngx ingest** (Docker / self-hosted) — read-only REST polling into the normal chunk → embed → Qdrant path with per-user encrypted credentials and deduplicated external document tracking.
- **Hybrid context building** — word-boundary entity matches plus optional Qdrant semantic top-up, configurable entity budget env vars, and a dedicated entities token slice in chat context allocation.
- **GHCR image provenance** — SLSA Level 2 build attestations for **`ghcr.io/lumogis/lumogis-orchestrator`** and **`ghcr.io/lumogis/lumogis-web`**; verify with **`gh attestation verify`** (see **`docs/capabilities.md`**).
- **CHANGELOG CI gate** on pull requests that touch product paths unless explicitly skipped in the PR body.
- **Chat auto-RAG (opt-in)** — optional per-turn injection of top document chunks into **`POST /v1/chat/completions`** context via **`LUMOGIS_AUTO_RAG_*`** env knobs (default **off**).
- **Notification architecture decision record** — documents the planned unified dispatcher, preference schema, and channel adapters; **no runtime behaviour change** in this release.

### Security

- **Pre-launch security audit artefacts** — structured findings under **`docs/security-audit/`** plus a committed OWASP ZAP baseline report; path-gated CI **`security-audit`** job runs local audit checks and advisory static analysis on Core (and graph service when present).
- **Qdrant household-union filters** — vector search filters now honour OR-shaped visibility constraints so semantic search cannot return unscoped points across household members.
- **Graph stats privacy tests** — canonical regression coverage for **`GET /graph/stats`** visibility binding in the graph service test suite.

### Changed

- **Admin settings ingest paths** — breaking **`GET /settings`** shape: `filesystem_root` fields removed; `ingest_paths`, `pending_ingest_paths`, `restart_required`, and `paperless_configured` added with multi-path env round-trip on PUT/restart.
- **Push ingest upload** — **`POST /api/v1/ingest/upload`** accepts multipart uploads, returns **`202`** with `file_id`, and queues batch ingest from workspace uploads.
- **Public webhook `DOCUMENT_INGESTED` payload** — additive **`ingestion_source_kind`** (`filesystem` | `external`); external sources may use stable logical URIs instead of filesystem paths.
- **CI OpenAPI contract check** — path-gated **`openapi-check`** job and **`make openapi-check`** alias for offline snapshot drift detection.
- **`GRAPH_MODE` default is `disabled`** — fresh installs omit graph wiring until operators opt into in-process or service modes; missing premium modules degrade with a single structured warning.
- **`LUMOGIS_TOOL_CATALOG_ENABLED` defaults on** when unset so capability tools merge when services are healthy; set **`false`** explicitly to restore the previous opt-in behaviour.

### Fixed

- **Release verification** — `make verify-public-rc-full` brings the RC stack back up for Playwright, rebuilds the web image, and waits for auth readiness before e2e; Playwright prove mode runs serially with stabler login and session-harness refresh.

---

## [0.6.0] — 2026-05-30

### Added

- **Inbox folder-watch hardening (LUM-330):** configurable `LUMOGIS_INBOX_PATH` / `LUMOGIS_INBOX_MODE` (`event` | `poll` | `off`), write-stability before ingest, `enqueue_inbox_file` seam, poll-mode mtime fast-path, quarantine on terminal failures under `ai-workspace/quarantine/`, `/healthz` inbox liveness fields (no absolute paths), and auth-gated inbox block on `GET /api/v1/admin/diagnostics`.
- **Desktop memory overlay (LUM-329):** Tauri 2 app under **`clients/lumogis-desktop/`** — global hotkey (default **Ctrl+Shift+L** / **⌃⇧L**), frameless overlay, **`GET /api/v1/memory/search`** (5 hits), OS keychain for bearer JWT, library-root sandbox for native open/reveal; **`make desktop-dev`** / **`make desktop-build`**; CI workflow **`.github/workflows/desktop-build.yml`**. Proprietary tree — excluded from public export via **`scripts/public-export-strip-list.txt`**.
- **Client-only overlay distribution (LUM-398):** Persona B household-member profile — CI artefacts **`lumogis-overlay-*`**, **`tauri.client-only.conf.json`**, in-webview first-run onboarding (server URL → **`/healthz`** → auth → search with optional empty library roots), **`overlay.json` schema v2** with **`onboardingComplete`**; **`make desktop-build-client-only`** for local parity.
- **`make doctor` (LUM-199):** read-only operator health CLI — Compose **`ps`/`config`**, config grammar checks on **`.env`** (no **`source`**), optional **`/healthz`** probes, optional **`--json`** v1 contract (**`scripts/doctor/schema.v1.json`**), security category opt-in (**`--security`** / **`LUMOGIS_DOCTOR_RUN_SECURITY=1`**). See **`scripts/doctor/README.md`**.
- **First-run onboarding (LUM-165):** skippable Lumogis Web orientation modal (per-user `users.onboarding_completed_at`, `GET`/`PATCH /api/v1/me/onboarding`) plus a shared `EmptyState` for the chat zero-state.
- **First-run quickstart (LUM-184):** operator guide **[`docs/deployment/quickstart.md`](docs/deployment/quickstart.md)** for the published **GHCR** image path (`COMPOSE_FILE=docker-compose.yml:docker-compose.ghcr.yml`, first-boot **Ollama** / **Postgres** behaviour, **`curl`** health smoke vs **`make health`**, and common errors); **`README.md`** and **`docs/README.md`** cross-link for discoverability.
- **paperless-ngx ingest (LUM-281, Docker / self-hosted):** read-only REST polling into the normal chunk → embed → Qdrant path; per-user encrypted credentials (`paperless` connector); `POST /api/v1/sources` with `source_type: "paperless"`; `sources.poll_cursor` + `external_documents` dedup; operator env `PAPERLESS_*`, `PAPERLESS_POLL_PAGE_SIZE`, and outbound URL policy knobs `LUMOGIS_ALLOW_PRIVATE_OUTBOUND_URLS` / `LUMOGIS_OUTBOUND_PRIVATE_HOST_ALLOWLIST` (see reference manual).
- **CONTEXT_BUILDING (LUM-210):** hybrid entity selection — word-boundary explicit matches plus optional Qdrant semantic top-up (`LUMOGIS_CONTEXT_BUILDER_SEMANTIC`), configurable entity budget and ranking env vars (mirrored in Core + `lumogis-graph` config), `user_id` threaded through the hook and KG `POST /context`, dedicated **`entities`** token slice in chat `allocate()`, and `visible_qdrant_filter` mirrored into **`services/lumogis-graph/visibility.py`** for the semantic path.
- **GHCR images** (`ghcr.io/lumogis/lumogis-orchestrator`, `ghcr.io/lumogis/lumogis-web`): GitHub-hosted SLSA Level 2 build-provenance attestations from the public **`lumogis/lumogis`** publish workflow; verify with **`gh attestation verify`** (see **`docs/capabilities.md`** — **Verifying image provenance**).
- **CHANGELOG CI gate** on pull requests that touch product paths: GitHub Actions enforces a `CHANGELOG.md` update unless `Skip-Changelog` or `[skip changelog]` in the PR body; see `CONTRIBUTING.md`, `.github/workflows/changelog.yml`, and local `make changelog-check`.
- **Chat auto-RAG (LUM-308):** optional per-turn injection of top **`documents`** chunks into **`POST /v1/chat/completions`** context (`LUMOGIS_AUTO_RAG_*` env knobs; default **off**); hybrid/RRF vs dense gating, BGE reranker floor when configured, and **`search_files`** dedupe against injected point ids.

### Security

- **Pre-launch hybrid audit (LUM-190):** structured findings under **`docs/security-audit/pre-launch-audit-2026.md`** (path differs from plan text `docs/security/` when a root-owned `docs/security` mount exists — see doc header) + committed **`docs/security-audit/zap-baseline-2026.json`** (OWASP ZAP baseline against `https://example.com`, pinned `ghcr.io/zaproxy/zaproxy` digest, auth **`none`**); path-gated CI job **`security-audit`** runs **`make audit-local`** (blocking) and advisory **Bandit** on `orchestrator/` (+ `services/lumogis-graph/` when present); **`scripts/requirements-security-audit.txt`** pins Bandit; **`make bandit-check`** for local parity; **`.github/scripts/security-audit-paths.sh`** + contract tests in **`.github/scripts/test-security-audit-paths.sh`**. Also: **`stack-control/requirements.txt`** bumps **FastAPI** to **0.136.1** (Starlette **1.0.1** for pip-audit); **`clients/lumogis-web`** lockfile via **`npm audit fix`** for a clean **`npm audit`**.
- **Qdrant household-union filters:** `adapters/qdrant_store.py::_build_filter` now implements top-level `should` (OR of branches) and `match.any` clauses, matching `visibility.visible_qdrant_filter`’s default shape. Previously only flat `must` lists were translated; the default filter had no top-level `must`, so Qdrant received an empty `must` constraint and could return **unscoped** points — a cross-tenant isolation failure for semantic search on `documents` / `entities` and for CONTEXT_BUILDING Phase B when semantic top-up is enabled.
- **LUM-23 / FP-042 — graph stats privacy evidence:** removed the long-skipped Core `orchestrator/tests/premium/test_graph_viz_routes.py` module; canonical regression coverage for `GET /graph/stats` visibility binding lives in **`services/lumogis-graph/tests/test_graph_stats_privacy.py`** (records Cypher/SQL + params; see **`docs/decisions/DEBT.md`** Resolved row).

### Changed

- **Admin settings ingest paths (LUM-397, C1):** breaking `GET /settings` rename — `filesystem_root` and `pending_filesystem_root` removed; `ingest_paths`, `pending_ingest_paths`, `restart_required`, and `paperless_configured` added. Multi-path env round-trip via `INGEST_PATHS_HOST` and `INGEST_PATHS` on PUT/restart.
- **Push ingest upload (LUM-397, C4):** `POST /api/v1/ingest/upload` — multipart `file`, `require_user`, `202` with `file_id`; persistent store under workspace `uploads/`; batch `ingest_upload` handler.
- **LUM-322:** ADR-061 revisit conditions now document deferral of a parallel **`orchestrator.doctor`** CLI in favour of **`GET /admin/health`** until explicit gates fire; **`scripts/doctor/README.md`** cross-links when to use shell **`make doctor`** vs authenticated admin health.
- **Public webhook `DOCUMENT_INGESTED` payload** (Core → `lumogis-graph` when `GRAPH_MODE=service`): additive field **`ingestion_source_kind`** (`"filesystem"` \| `"external"`; default **`"filesystem"`**). When **`"external"`**, **`file_path`** may be a stable logical URI such as **`paperless://{source-uuid}/documents/{id}`** instead of a filesystem path.
- **LUM-94:** CI adds a path-gated **`openapi-check`** job (alongside existing **`lint-and-test`**) plus **`make openapi-check`** as an alias of **`make web-codegen-check`**; contributor docs now describe offline OpenAPI snapshot / codegen drift checks (`dump_openapi`, not a live orchestrator). **`openapi.snapshot.json`** refreshed to match current **`dump_openapi`** output (schema emission deltas).
- **`GRAPH_MODE` default is now `disabled`.** Fresh installs omit graph wiring until operators set `GRAPH_MODE=inprocess` (premium in-process plugin) or `GRAPH_MODE=service` (premium KG service overlay). Requests for `service`/`inprocess` degrade to `disabled` with a single structured WARNING when premium modules are absent (AGPL export / partial trees).
- **`LUMOGIS_TOOL_CATALOG_ENABLED`** defaults to **on** when unset: operators running capability services no longer need to set this flag for the LLM to merge eligible tools. Operators who want the previous behaviour (**no** merged capability tools / OOP dispatch) must set **`LUMOGIS_TOOL_CATALOG_ENABLED=false`** explicitly.

---

---

## [0.5.2] — 2026-05-24

### Fixed

- **Public CI lint:** orchestrator **`ruff format`** pass so the published **`lint-and-test`** job’s format check exits cleanly on GitHub Actions (no runtime behaviour change).

---

---

## [0.5.1] — 2026-05-24

### Fixed

- **Public CI lint:** orchestrator import ordering and line-length violations that blocked the **`lint-and-test`** job on published **`main`**.
- **Public CI doctor integration:** **`doctor-integration`** job compose chain no longer merges **`docker-compose.test.yml`**’s **`include:`** overlay with the base file (avoids **“services.orchestrator conflicts with imported resource”** on GitHub Actions); uses **`docker-compose.test-doctor.yml`** instead.

---

---

## [0.5.0] — 2026-05-24

### Added

- **`make doctor`:** read-only operator health CLI — Compose **`ps`/`config`**, config grammar checks on **`.env`** (no **`source`**), optional **`/healthz`** probes, optional **`--json`** v1 contract (**`scripts/doctor/schema.v1.json`**), security category opt-in (**`--security`** / **`LUMOGIS_DOCTOR_RUN_SECURITY=1`**). See **`scripts/doctor/README.md`**.
- **First-run onboarding:** skippable Lumogis Web orientation modal (per-user `users.onboarding_completed_at`, `GET`/`PATCH /api/v1/me/onboarding`) plus a shared empty-state component for the chat zero-state.
- **First-run quickstart:** operator guide **[`docs/deployment/quickstart.md`](docs/deployment/quickstart.md)** for the published **GHCR** image path (`COMPOSE_FILE=docker-compose.yml:docker-compose.ghcr.yml`, first-boot **Ollama** / **Postgres** behaviour, **`curl`** health smoke vs **`make health`**, and common errors); **`README.md`** and **`docs/README.md`** cross-link for discoverability.
- **Remote access guide:** **[`docs/deployment/remote-access.md`](docs/deployment/remote-access.md)** documents off-LAN household access patterns (Tailscale-first).
- **paperless-ngx ingest (Docker / self-hosted):** read-only REST polling into the normal chunk → embed → Qdrant path; per-user encrypted credentials (`paperless` connector); `POST /api/v1/sources` with `source_type: "paperless"`; `sources.poll_cursor` + `external_documents` dedup; operator env `PAPERLESS_*`, `PAPERLESS_POLL_PAGE_SIZE`, and outbound URL policy knobs `LUMOGIS_ALLOW_PRIVATE_OUTBOUND_URLS` / `LUMOGIS_OUTBOUND_PRIVATE_HOST_ALLOWLIST` (see reference manual).
- **Hybrid context building:** word-boundary explicit entity matches plus optional Qdrant semantic top-up (`LUMOGIS_CONTEXT_BUILDER_SEMANTIC`), configurable entity budget and ranking env vars, dedicated **`entities`** token slice in chat context allocation, and visibility filters mirrored into graph service queries when premium graph modules are present.
- **GHCR image attestations:** published **`ghcr.io/lumogis/lumogis-orchestrator`** and **`ghcr.io/lumogis/lumogis-web`** images carry GitHub-hosted SLSA Level 2 build-provenance attestations verifiable with **`gh attestation verify`** (see **`docs/capabilities.md`** — **Verifying image provenance**).
- **Chat auto-RAG:** optional per-turn injection of top **`documents`** chunks into **`POST /v1/chat/completions`** context (`LUMOGIS_AUTO_RAG_*` env knobs; default **off**); hybrid/RRF vs dense gating, BGE reranker floor when configured, and **`search_files`** dedupe against injected point ids.
- **Public CI:** path-gated **`openapi-check`**, **`doctor-integration`**, and **`security-audit`** jobs in **`.github/workflows/ci.yml`**; offline OpenAPI snapshot/codegen drift checks via **`make openapi-check`**; semantic breaking-change gate via **`make openapi-breaking-check`** (requires **oasdiff**).

### Security

- **Pre-launch security audit:** structured findings under **`docs/security-audit/pre-launch-audit-2026.md`** plus committed **`docs/security-audit/zap-baseline-2026.json`** (OWASP ZAP baseline, auth **`none`**); path-gated CI job **`security-audit`** runs **`make audit-local`** (blocking) and advisory **Bandit** on `orchestrator/` (+ `services/lumogis-graph/` when present). **`stack-control/requirements.txt`** bumps **FastAPI** to **0.136.1**; **`clients/lumogis-web`** lockfile refreshed for clean **`npm audit`**.
- **Qdrant household-union filters:** vector search now honours top-level **`should`** (OR) and **`match.any`** clauses in visibility filters, fixing a cross-tenant isolation failure for semantic search on **`documents`** / **`entities`** and for semantic context-building top-up when enabled.
- **Graph statistics privacy:** canonical regression coverage for operator graph statistics visibility lives in the knowledge-graph service test suite; redundant Core-side test scaffolding removed from the default layout.

### Changed

- **Public webhook `DOCUMENT_INGESTED` payload** (Core → graph service when `GRAPH_MODE=service`): additive field **`ingestion_source_kind`** (`"filesystem"` \| `"external"`; default **`"filesystem"`**). When **`"external"`**, **`file_path`** may be a stable logical URI such as **`paperless://{source-uuid}/documents/{id}`** instead of a filesystem path.
- **OpenAPI contract checks** use offline snapshot/codegen drift checks (`dump_openapi`, not a live orchestrator). **`openapi.snapshot.json`** refreshed to match current schema emission.
- **`GRAPH_MODE` default is now `disabled`.** Fresh installs omit graph wiring until operators set `GRAPH_MODE=inprocess` (premium in-process plugin) or `GRAPH_MODE=service` (premium KG service overlay). Requests for `service`/`inprocess` degrade to `disabled` with a single structured WARNING when premium modules are absent (AGPL export / partial trees).
- **`LUMOGIS_TOOL_CATALOG_ENABLED`** defaults to **on** when unset: operators running capability services no longer need to set this flag for the LLM to merge eligible tools. Operators who want the previous behaviour (**no** merged capability tools / out-of-process dispatch) must set **`LUMOGIS_TOOL_CATALOG_ENABLED=false`** explicitly.

---

---

## [0.4.0] — 2026-05-15

### Added

- **Ingest and context hardening** for retrieved material: pattern checks at ingest, structured origin metadata stored with vectors, explicit framing of retrieved fragments in chat context (with per-request scaffolding), a configurable cap on chained tool calls in a single model turn, and audit-friendly signals when high-severity ingest patterns fire. Operators can tune or disable the sanitiser via environment variables documented in the reference manual.
- **Action proposal execution** uses a database-backed queue with atomic claims so the same approved proposal cannot be executed twice by different clients; long-running or crashed workers time out to a terminal error state rather than returning work to an executable queue.
- **Multi-device sessions** for household accounts: refresh tokens are tracked per device session instead of a single rotating slot, and access tokens carry a monotonic per-user version so password changes and global sign-out invalidate outstanding access JWTs immediately instead of waiting for access-token expiry alone.
- **Maintainer and CI gates** for public-boundary work: Makefile targets run orchestrator tests in Compose, web lint/test/build, and optional integration slices; graph **RELATES_TO** merge policy is checked in CI to keep projection tests aligned with documented edge direction.

### Changed

- **`GRAPH_MODE` default is now `disabled`.** Fresh installs omit graph wiring until operators set `GRAPH_MODE=inprocess` (premium in-process plugin) or `GRAPH_MODE=service` (premium KG service overlay). Requests for `service`/`inprocess` degrade to `disabled` with a single structured WARNING when premium modules are absent in this tree.
- **`LUMOGIS_TOOL_CATALOG_ENABLED`** defaults to **on** when unset so capability tools merge when services are healthy and bearer trust is valid. Operators who want the previous behaviour (**no** merged capability tools) set **`LUMOGIS_TOOL_CATALOG_ENABLED=false`** explicitly.
- **Default Compose Qdrant host publish** uses **`${QDRANT_HOST_PORT:-6334}:6333`** so the developer stack avoids collision with another vector store on host port 6333; disposable integration env files can set a different host port for parallel stacks.
- **Public export profile** continues to ship AGPL Core, web client, and tests without bundling premium-only graph service source trees; premium graph remains a separate packaging concern.

### Fixed

- **Web OpenAPI contract check** normalises `info.version` the same way as the committed snapshot generator so semver drift on the live JSON endpoint does not false-fail release verification when the route surface matches.

---

## [0.3.0] — 2026-05-13

### BREAKING — Knowledge graph proxy when `GRAPH_MODE=service`

- **`query_graph` does not reach the out-of-process graph bridge** until **`GRAPH_WEBHOOK_SECRET`** is set on Core **or** **`LUMOGIS_GRAPH_PROXY_ALLOW_INSECURE_MISSING_SECRET`** is enabled (`true` / `1` / `yes`). Otherwise Core stays fail-closed (no outbound HTTP for that tool path; user-facing unavailable message).
- Deployments that relied on graph calls **without** **`GRAPH_WEBHOOK_SECRET`** on Core while the graph service accepted unsigned requests **must** align symmetric secrets or enable the Core opt-in deliberately. **`GRAPH_MODE=inprocess`** is unchanged.

### BREAKING — Operator diagnostics (credential fingerprint)

- **`GET /api/v1/admin/diagnostics/credential-key-fingerprint`** returns a **per-tier** shape (**user**, **household**, **system**) plus aggregate row counts. External integrations that assumed the older flat layout need updating. The Lumogis Web rotation badge expects the new shape.

### Security

- **stack-control (FastAPI / Starlette).** Pinned FastAPI to **0.123.10** so dependencies resolve to Starlette **0.50.x** and satisfy CVE-2025-54121 and CVE-2025-62727. Earlier FastAPI lines capped Starlette below **0.47**, which could not satisfy those patched ranges.

### Added

- **Household accounts and sessions.** **Admin** and **member** roles with JWT access tokens and rotating refresh cookies; Lumogis Web as the primary interactive shell when **`AUTH_ENABLED`**.

- **Password management and household portability.** Self-service password change, admin-initiated resets, documented CLI recovery for operators with shell access, and admin-mediated user export/import flows.

- **Connector-backed integrations.** Encrypted connector credential storage, CalDAV and **ntfy** connectors as surfaced in Lumogis Web, LLM provider API keys scoped per user where configured.

- **Opaque MCP bearer tokens** for **`/mcp/*`** clients in authenticated deployments—distinct from interactive JWTs—plus mint/revoke surfaces in Me and Admin.

- **Structured audit logging** with durable append-only trails and request correlation for operator review.

- **Household and instance-system connector credentials.** Admins store shared connector secrets at household or whole-instance scope; resolution walks **user → household → system → environment** and fails closed on decrypt errors without silent fallback across tiers. Rotation tooling and audit summaries understand all tiers; household/system secrets stay out of per-user export bundles.

- **Per-user connector Ask/Do permissions.** Permission modes are stored **per user**, drive catalog labels and execution checks, and routine Ask→Do elevation tracks approvals **per user**. Authenticated APIs let members manage their own connector modes and admins manage any account.

- **Out-of-process knowledge-graph service.** Optional **`lumogis-graph`** runs projection, FalkorDB writes, reconciliation jobs, **`query_graph`**, and **`/context`** for chat when **`GRAPH_MODE=service`**; **`GRAPH_MODE`** selects **`inprocess`**, **`service`**, or **`disabled`**. Capability manifests may advertise **`management_url`** for operator UIs.

- **Lumogis Web responsive and installable client.** Mobile-oriented UX for chat, approvals, and navigation shells; **PWA** manifest and service worker (**precache** and notification hooks—not full offline intelligence); **browser Web Push** opt-in with subscription storage and minimal payloads.

- **Capture ledger APIs.** Authenticated **`/api/v1/captures`** routes create and manage capture rows (including attachments and transcription hooks), list with pagination, and submit captures for indexing; complements the **`/capture`** QuickCapture UI.

- **Push-to-talk transcription.** **`POST /api/v1/voice/transcribe`** returns transcript text from uploaded audio when speech-to-text is enabled (`STT_BACKEND`); optional Whisper-class **sidecar** deployment via Compose overlay.

- **Admin diagnostics "foundation signals".** Read-only diagnostics expose extra orchestrator readiness hints used by the operator dashboard.

- **Published Docker images.** `ghcr.io/lumogis/lumogis-orchestrator` and `ghcr.io/lumogis/lumogis-web` are now published automatically to GitHub Container Registry on every release — supports `linux/amd64` and `linux/arm64` (Apple Silicon, Raspberry Pi, ARM servers). Self-hosters can pull pre-built images without building from source.

- **Pull-based deployment overlay.** `docker-compose.ghcr.yml` lets operators use published images via `COMPOSE_FILE=docker-compose.yml:docker-compose.ghcr.yml docker compose up -d --pull always` without modifying the default developer compose file.

### Changed

- Chat may inject optional graph **`/context`** fragments when **`GRAPH_MODE=service`** (short timeout; misses do not block replies).

- In-process graph plugin disables routers, hooks, and scheduled graph jobs when **`GRAPH_MODE` ≠ `inprocess`** so service mode does not double-write.

### Deprecated

- Legacy **`GET /permissions`** and **`PUT /permissions/{connector}`** still respond but require admin, advertise deprecation metadata, and steer callers to **`/api/v1/me/permissions`**; slated for **`410 Gone`** in an upcoming minor release. Legacy **`PUT`** affects only the caller's own per-user row.

### Fixed

- **stack-control tests:** the **`RESTART_SECRET`** restart-flow test mocks Docker Compose instead of shelling out, so CI passes without a compose project working directory.

- Capture attachment storage rejects unsafe absolute path segments in normalized keys.

- Speech-to-text sidecar URL allowlisting rejects IPv4-mapped IPv6 literals used to bypass host checks.

### Notes

- Makefile-driven Compose test targets pin **`COMPOSE_FILE`** so local **`docker compose`** overrides cannot pull in missing fragments during **`compose-test`** runs.

- CI enforces a Compose manifest policy check (allowed **`env_file`** usage and forbidden sensitive keys in tracked stacks).

- Connector permission mode caching is **single-worker**; multi-worker orchestrator replicas could briefly serve stale labels until cache refresh—single-worker remains the supported default.

- Disabling a user revokes MCP bearer tokens promptly; outstanding JWT access tokens may remain valid until expiry.

- Release verification gates (`make verify-public-rc`, `make verify-public-rc-full`) are now documented Makefile targets; maintainers can validate the full release pipeline locally before publishing.

---

## [0.3.0rc1] — 2026-03-19

Initial public release candidate.
