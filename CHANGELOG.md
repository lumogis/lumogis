# Changelog

All notable changes to this project are documented here.

Format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
Versions follow [Semantic Versioning](https://semver.org/).

---

## [Unreleased]

### Added

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

### Notes

- The in-process `permissions._mode_cache` is single-worker only and
  is **not** invalidated across orchestrator replicas. Lumogis-Core
  ships single-worker today; if you deploy with `--workers > 1` a
  followers may serve stale modes for up to the next cache miss.
  Promote to a shared cache (Redis) only if you actually run
  multi-worker. Tracked as deferred follow-up.
- Disabling a user clears that user's cache slots and revokes MCP
  bearer rows immediately, but JWT-bearer access tokens already in
  flight remain valid for up to `ACCESS_TOKEN_TTL_SECONDS`
  (default 900s); the disabled-user check inside
  `get_connector_mode` is a deferred follow-up.

### Added (continued)

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
  `docs/decisions/{NNN}-credential_scopes_shared_system.md`.

### Changed (BREAKING — operator-only)

- **`GET /api/v1/admin/diagnostics/credential-key-fingerprint` response
  shape.** Previously a flat `{ rows_by_key_version: {...} }` for the
  per-user store only. Now returns a per-tier nested breakdown:
  `{ tiers: { user: {...}, household: {...}, system: {...} },
    rows_by_key_version: {<aggregate>} }`. The web UI rotation badge
  (`orchestrator/web/index.html`) consumes the new shape; any external
  scrapers must be updated. Endpoint remains admin-only.

### Added — Graph extraction (continued)

- **`lumogis-graph` out-of-process knowledge-graph service.** The graph
  pipeline (entity projection, FalkorDB writes, daily reconciliation,
  weekly quality jobs, `query_graph` tool, `/context` injection) can now
  run as a standalone container in addition to the existing in-process
  plugin. Enable by adding `docker-compose.premium.yml` to your
  `COMPOSE_FILE` and setting `GRAPH_MODE=service`,
  `KG_SERVICE_URL=http://lumogis-graph:8001`, and a shared
  `GRAPH_WEBHOOK_SECRET`. See `services/lumogis-graph/README.md`.
- **`GRAPH_MODE` env var** (`inprocess` | `service` | `disabled`) on
  Core. `inprocess` (default) preserves the existing behaviour;
  `service` proxies graph traffic to `lumogis-graph` via webhooks +
  HTTP `/context`; `disabled` turns the graph plugin off entirely.
- **`management_url`** field on `CapabilityManifest`. Capability
  services may now advertise an operator-facing UI; Core's status page
  renders it as a clickable link.
- **`POST /webhook` and `POST /context` HTTP contracts** (defined in
  `orchestrator/models/webhook.py`). Used by Core's
  `services/graph_webhook_dispatcher.py` to talk to `lumogis-graph`.
- **`make sync-vendored`** to re-vendor `models/webhook.py` and
  `models/capability.py` into `services/lumogis-graph/models/` after
  changing the canonical Core copy.
- **`make test-kg` / `make compose-test-kg`** targets for KG service
  unit tests (host venv and containerised respectively).
- **`make test-graph-parity`** target. Boots the stack in
  `GRAPH_MODE=inprocess` and `GRAPH_MODE=service` over the same
  fixture corpus and asserts FalkorDB state matches; serves as the
  regression gate for the extraction.
- **`docker-compose.parity.yml`** overlay used by the parity test to
  bind-mount `tests/fixtures/` into the orchestrator container.

### Changed

- Core's `routes/chat.py` injects context fragments via the KG
  service's `/context` endpoint when `GRAPH_MODE=service` (40 ms hard
  timeout; on miss the chat reply is unaffected).
- The in-process `plugins/graph/__init__.py` self-disables (no router,
  no hooks, no jobs) when `GRAPH_MODE != "inprocess"` to prevent
  duplicate projection in service mode.
- Core's lifespan no longer schedules the weekly KG quality job in
  service or disabled mode; the KG container owns it.

### Notes

- The default `GRAPH_MODE=inprocess` is fully backward-compatible. No
  configuration changes are required to upgrade.
- `plugins/graph/` is intentionally retained in Core for this release.
  Its removal is out of scope and will follow once `service` mode has
  burned in across deployments.

---

## [0.3.0rc1] — 2026-03-19

Initial public release candidate.
