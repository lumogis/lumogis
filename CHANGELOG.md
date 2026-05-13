# Changelog

All notable changes to this project are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions follow [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

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
