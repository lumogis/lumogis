# Changelog

All notable changes to this project are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions follow [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

---

## [0.5.0] — 2026-06-05

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
