# Changelog

All notable changes to this project are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions follow [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Security

- **stack-control (FastAPI / Starlette).** Pinned FastAPI to `0.123.10` so
  dependencies resolve to Starlette 0.50.x and satisfy CVE-2025-54121 and
  CVE-2025-62727. FastAPI 0.115.x capped Starlette below 0.47, which could not
  meet those patched ranges. See comments in
  `stack-control/requirements.txt`.

### Fixed

- **stack-control tests:** the env-file `RESTART_SECRET` restart-flow test
  mocks Docker Compose instead of shelling out, so CI passes without a compose
  project working directory.

### Changed — CI tooling

- **Developer CI:** pinned Ruff to the remediation version and stabilized
  Ruff format/lint across the orchestrator tree with a mechanical baseline,
  so `ruff check orchestrator/` and `ruff format --check orchestrator/`
  gates stay deterministic.

### Added — Knowledge graph (FalkorDB)

- **Core graph plugin (`GRAPH_MODE=inprocess`, default).** Falkor-backed
  entity projection from chat/batch/capture-driven workloads, MCP/tool surface
  for **`query_graph`**, admin **`/graph/*`** inspection and visualization,
  Postgres-backed **`kg_settings`** and the quality **`004`**–**`009`** chain (entity
  quality metadata, FK constraints on graph artefacts, heuristic edge scoring,
  duplicate rollup, Splink linkage, exposed operator knobs), plus scheduled
  reconciliation and weekly quality passes when Core owns the plugin
  lifecycle.
- **`lumogis-graph` out-of-process service.** The same logical pipeline can run
  in a sibling container (`entity` projection → FalkorDB, daily reconcile,
  weekly quality, **`query_graph`**, webhook-driven **`/context`** for chat
  augmentation). Compose via **`docker-compose.premium.yml`**, **`GRAPH_MODE=service`**,
  **`KG_SERVICE_URL`**, and shared **`GRAPH_WEBHOOK_SECRET`**. See
  `services/lumogis-graph/README.md`.
- **`GRAPH_MODE` env var** (`inprocess` | `service` | `disabled`) on Core.
  **`service`** proxies graph traffic through **`services/graph_webhook_dispatcher.py`**
  with **`POST /webhook`** and **`POST /context`** contracts (`orchestrator/models/webhook.py`);
  **`disabled`** turns the in-process plugin off entirely.
- **`management_url`** on **`CapabilityManifest`** so capability services can
  link an operator-facing UI from Core's status/overview surfaces.
- **`make sync-vendored`** to re-vendor `models/webhook.py` and `models/capability.py`
  into `services/lumogis-graph/models/` after Core-side edits.
- **`make test-kg`** / **`make compose-test-kg`** targets for **`lumogis-graph`**
  unit tests (host venv vs containerised).
- **`make test-graph-parity`** compares FalkorDB state for **`GRAPH_MODE=inprocess`**
  vs **`service`** against a shared fixture corpus (regression gate for the extraction).
- **`docker-compose.parity.yml`** overlays bind-mount **`tests/fixtures/`** during
  parity runs.

### Added — Household multi-user platform

- **Accounts / roles.** Migration **`010-users-and-roles.sql`** establishes the
  durable multi-account model exercised by **`test_two_user_isolation`** and
  **`test_household_sharing`** (household-aligned visibility rules without
  weakening per-user connectors).
- **Per-user MCP bearer tokens.** Migration **`014-mcp-tokens.sql`**; issuance and
  resolution keyed by **`user_id`** (`/api/v1/me/mcp-tokens` + admin routes).
- **Encrypted connector credential stores.** Migration **`015-user-connector-credentials.sql`**
  (per-user rows) feeds the resolver that composes with **household** and **system**
  tiers (**`018`**, surfaced under **Connector credential tiers** below) as
  **`user → household → system → environment`**, failing closed on decrypt errors.
  **`scripts.rotate_credential_key`** rotates every persisted tier consistently.
- **Per-user connector permissions** appear under **Connector permissions** below
  (migration **`016`**).
- **Per-user durable batch jobs.** Migration **`017-per-user-batch-jobs.sql`**
  adds **`user_batch_jobs`** so ingest / **`batch_queue`** / entity-extract ledger
  rows attribute work to **`user_id`** (`orchestrator/services/batch_queue.py`).
- **Portable user backup / restore.** **`POST /api/v1/me/export`** (ZIP) and
  **`/api/v1/admin/user-imports`** for server-side archives with dry-run preview;
  Web Admin surfaces import/export with redacted responses.

### Added — Scoped memory

- **`personal` / `shared` / `system` scaffolding.** Migration **`013-memory-scopes.sql`**
  adds scope columns (`published_from` linkages where applicable) across
  Postgres memory-bearing tables — foundation for publishing personal traces into
  household-visible projections without rewriting originals (`docs/decisions/` plan
  references linked from the migration header).

### Added — Lumogis Web (first-party SPA / PWA)

- **React client (`clients/lumogis-web/`).** Chat (**SSE streaming**), memory search,
  approvals, **`/me/*`** (profile + password rotation, connectors, synced connector
  permissions snapshot, MCP tokens, notifications + Web Push opt-in/out, LLM
  provider tiers **read-only**, backup export kick-off, diagnostics-only tools/capabilities
  list), and **`/admin/*`** (user directory with import/export + password resets,
  connector credentials **tier** editors, cross-user connector permissions editor,
  MCP admin, audit, diagnostics snapshot).
- **QuickCapture (`/capture`).** Mobile-first capture UX with IndexedDB drafts +
  outbound queue when offline-ish; multipart flows land on **`/api/v1/captures`**;
  Web Share Target **prefills** title/text/url but **never auto-saves** without an
  explicit tap (see **`clients/lumogis-web/README.md`**).
- **PWA mechanics.** **`manifest.webmanifest`**, service worker (**`src/pwa/swPush.ts`**)
  for **Web Push** subscription + safe **`notificationclick`** routing, and the
  global offline **banner** for honest network expectations.
- **Schema — `019-lumogis-web.sql`.** Persists **`webpush_subscriptions`** and the
  **forward-compat `auth_refresh_revocations`** scaffold (today's refresh JWT path
  still uses **`users.refresh_token_jti`** as documented inside the migration).
- **Serving model.** **`lumogis-web`** nginx static image behind **Caddy** at **`/`**
  with same-origin **`/api`**, **`/api/v1`**, **`/mcp`** routing so **`SameSite=Strict`**
  refresh cookies behave without CORS juggling. Smoke via **`tests/integration/test_lumogis_web_smoke.py`**
  and **`make web-e2e`**.

### Added — Scoped search / file index

- **Tenant-scoped ingestion index.** **`001-add-user-id-to-file-index.sql`** + **`011-per-user-file-index.sql`**
  attribute indexed chunks/metadata to **`user_id`** so hybrid search honours household
  boundaries.

### Added — Voice & speech-to-text

- **`POST /api/v1/voice/transcribe`** (`routes/api/v1/voice.py`) fronts the shared
  **`services/speech_to_text.transcribe_blob`** pathway used by captures.
- **`STT_BACKEND`:** **`none`** (feature disabled → HTTP **`stt_disabled`**), **`fake_stt`**
  for deterministic suites, **`whisper_sidecar`** (HTTP caller to **`STT_SIDECAR_URL`**
  with loopback-first defaults plus explicit **`STT_SIDECAR_ALLOW_REMOTE`** escapes and
  URL / host validation to reduce SSRF), **`faster_whisper`** as a reserved value that
  **fails fast** (backend not implemented in this repository). MIME allowlist, **`STT_MAX_AUDIO_BYTES`** /
  **`STT_MAX_DURATION_SEC`**, optional **ffprobe** duration probes, semaphore-limited
  concurrency, and admin diagnostics surfaces describe the active adapter.

### Added — Captures (Phase 5 ingestion)

- **`/api/v1/captures` API** (`routes/api_v1/captures.py` + `services/captures.py`):
  personal capture CRUD with idempotent **`(user_id, local_client_id)`** creates,
  paginated listing, multipart **attachments** (image/audio) stored under **`LUMOGIS_DATA_DIR`**
  via **`services/media_storage.py`**, capture-scoped **transcribe** (STT stack above),
  and **index-to-memory** promotion with **409** when already **`indexed`**.
- **Schema — `020-captures.sql`.** **`captures`**, **`capture_attachments`**, **`capture_transcripts`**
  with CHECK-frozen status/type/provenance enums; optional **`note_id`** pointer after
  successful promotion.
- **Exports.** Per-user ZIP archives include capture metadata plus **`captures/media/`**
  binaries (while still omitting non-user tables such as household/system credentials).

### Added — Connector permissions

- **Per-user connector permissions (audit A2 closure).** Connector
  `ASK` / `DO` modes are now strictly per-user. New surfaces:
  `GET/PUT/DELETE /api/v1/me/permissions[/{connector}]` for the
  caller, and
  `GET/PUT/DELETE /api/v1/admin/users/{user_id}/permissions[/{connector}]`
  plus a cross-user `GET /api/v1/admin/permissions` enumeration for
  admins. Connectors without an explicit per-user row resolve to the
  `_DEFAULT_MODE = 'ASK'` lazy fallback. `routine_do_tracking` is
  also per-user (15-approval auto-elevation no longer crosses
  household boundaries). Migration `016-per-user-connector-permissions.sql`
  fans existing rows out per real user (eager backfill, gated by
  `EXISTS (SELECT 1 FROM users)`); empty deployments are left
  untouched and the bootstrap admin inherits any non-`ASK` rows on
  first user creation. `POST /permissions/{connector}/elevate` now
  requires `require_user` (closes a pre-existing unauthenticated
  elevation hole). Documented in `docs/connect-and-verify.md` Step 9f.

### Deprecated

- **Legacy `GET /permissions` and `PUT /permissions/{connector}`.**
  These endpoints still respond this release but now require
  `require_admin`, emit a `Deprecation: true` + `Link: rel="successor-version"`
  pair pointing at `/api/v1/me/permissions/{connector}`, and the
  legacy `PUT` writes the **calling admin's own** per-user row (not a
  global one — there is no global row anymore). Each call logs a
  single `legacy_*_permissions_used` WARN line. **Slated to return
  `410 Gone` in the next minor release.** Pre-multi-user
  `routine_do_tracking` counters that cannot be attributed to a
  specific user are remapped onto the bootstrap admin via
  `db_default_user_remap`; this is the most defensible interpretation
  but is not perfect — pre-A2 data simply does not carry per-user
  attribution.

### Notes — Permissions & auth cookies

- The in-process `permissions._mode_cache` is single-worker only and
  is **not** invalidated across orchestrator replicas. Lumogis-Core
  ships single-worker today; if you deploy with `--workers > 1`,
  follower workers may serve stale modes for up to the next cache miss.
  Promote to a shared cache (Redis) only if you actually run
  multi-worker. Tracked as deferred follow-up.
- Disabling a user clears that user's cache slots and revokes MCP
  bearer rows immediately, but JWT-bearer access tokens already in
  flight remain valid for up to `ACCESS_TOKEN_TTL_SECONDS`
  (default 900s); the disabled-user check inside
  `get_connector_mode` is a deferred follow-up.

### Added — Connector credential tiers

- **Household and instance-system connector credential tiers.** New
  admin-only stores `household_connector_credentials` and
  `instance_system_connector_credentials` (migration
  `018-household-and-instance-system-connector-credentials.sql`),
  surfaced via two admin-only router groups —
  `GET/PUT/DELETE /api/v1/admin/connector-credentials/household[/{connector}]`
  and `GET/PUT/DELETE /api/v1/admin/connector-credentials/system[/{connector}]`.
  Runtime resolution walks `user → household → system → env` with
  fail-fast on decrypt failure (no silent fall-through across tiers).
  All three tiers share the same `LUMOGIS_CREDENTIAL_KEY[S]` family;
  `python -m scripts.rotate_credential_key` walks every tier and
  exits non-zero iff aggregated `failed > 0`. Audit attribution: per-user
  writes keep `audit_log.user_id == target_user_id`; household/system
  writes record the **acting admin's** id (or `"default"` for the
  `system` actor sentinel). Every `__connector_credential__.*` audit
  `input_summary` now carries a `tier` discriminator
  (`"user" | "household" | "system"`). Household and instance-system
  credentials are explicitly excluded from per-user exports
  (`_OMITTED_NON_USER_TABLES` in `services/user_export.py`).
  Documented in
  `docs/decisions/027-credential_scopes_shared_system.md`.

### Changed (BREAKING — operator-only)

- **`GET /api/v1/admin/diagnostics/credential-key-fingerprint` response
  shape.** Previously a flat `{ rows_by_key_version: {...} }` for the
  per-user store only. Now returns a per-tier nested breakdown:
  `{ tiers: { user: {...}, household: {...}, system: {...} },
    rows_by_key_version: {<aggregate>} }`. The web UI rotation badge
  (`orchestrator/web/index.html`) consumes the new shape; any external
  scrapers must be updated. Endpoint remains admin-only.

### Changed — Knowledge graph runtime

- Core's `routes/chat.py` injects context fragments via the KG
  service's `/context` endpoint when `GRAPH_MODE=service` (40 ms hard
  timeout; on miss the chat reply is unaffected).
- The in-process `plugins/graph/__init__.py` self-disables (no router,
  no hooks, no jobs) when `GRAPH_MODE != "inprocess"` to prevent
  duplicate projection in service mode.
- Core's lifespan no longer schedules the weekly KG quality job in
  service or disabled mode; the KG container owns it.

### Notes — Knowledge graph

- The default `GRAPH_MODE=inprocess` is fully backward-compatible. No
  configuration changes are required to upgrade.
- `plugins/graph/` is intentionally retained in Core for this release.
  Its removal is out of scope and will follow once `service` mode has
  burned in across deployments.

---

## [0.3.0rc1] — 2026-03-19

Initial public release candidate.
