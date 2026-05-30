# ADR-071: Tauri overlay auth, ingest paths, and push upload (LUM-397)

**Status:** Finalised
**Created:** 2026-05-29
**Last updated:** 2026-05-29
**Decided by:** `/explore` → `/create-plan` → `/review-plan --arbitrate R1` → implement → `/verify-plan`
**Implementation:** C1 `81c6471a7`; C2–C6 product commit on `dev` (verify-plan 2026-05-29)

## Context

LUM-397 extends the LUM-329 desktop overlay with session auth (reactive refresh), admin **`ingest_paths`**, **`paperless_configured`**, per-user push upload, and multi-root filesystem ingest/watch boundaries. Exploration and plan: `.cursor/explorations/archived/LUM-397-tauri-overlay-auth-client.md`, `.cursor/plans/archived/LUM-397-tauri-overlay-auth-ingest.plan.md`.

## Decision

### Auth client (overlay)

- **Reactive refresh only** — no background timer; mirror lumogis-web.
- **Keychain:** service `lumogis-overlay`, account `session:{host-hash}` with JSON **`AuthSession`** (`access_token`, `refresh_token`, `expires_at_unix`, `role`). Replaces legacy separate access/refresh entries.
- **Refresh:** `POST /api/v1/auth/refresh` with **`Cookie: lumogis_refresh=…`** and **`Authorization: Bearer <access>`** (Bearer triggers `require_same_origin` CSRF bypass). `401` → re-login; `503` → auth-off.
- **Admin API:** overlay uses existing **`GET/PUT /settings`** with **`require_admin`** Bearer only — no `SETTINGS_ADMIN_TOKEN`.

### Settings: `filesystem_root` → `ingest_paths`

- Breaking JSON: **`ingest_paths`**, **`pending_ingest_paths`**, **`restart_required`**, **`paperless_configured`**; remove `filesystem_root` keys.
- **Dual env:** `INGEST_PATHS_HOST` (host list on GET) and `INGEST_PATHS` (container list for runtime). PUT/restart writes both plus `FILESYSTEM_ROOT` for index 0.
- Index **0** = host path; **1..n** = container-visible paths (`is_dir()` + `INGEST_PATHS_ALLOWED_PREFIX`).
- Legacy `app_settings.filesystem_root` migrates on read.

### Multi-root runtime

- **`orchestrator/services/path_containment.py`** — shared safe-prefix checks for tools, MCP, watchers.
- **Search** unions walks across `get_effective_ingest_paths()` until limit.
- **Ingest path watchers:** separate **`_ingest_paths_observer`** from inbox observer; stable files → batch **`ingest_watch_file`**; initial **`ingest_folder`** per root on start.
- **Inbox** (`LUMOGIS_INBOX_PATH`) and **Paperless** connector remain separate channels.

### Push upload

- **`POST /api/v1/ingest/upload`** — multipart, `require_user`, per-uploader only, **`202`** + opaque **`file_id`**.
- Persistent store under **`get_uploads_path() / {user_id} / {file_id}_{basename}`**; batch **`ingest_upload`**; handler does **not** delete file after ingest.

## Alternatives rejected

Proactive refresh timer; JWT `exp` decode in Rust; upload temp delete-after-ingest; direct `ingest_file` from watchdog threads; `library_roots` auto-sync with server paths; lumogis-web changes.

## Consequences

- Operators must **restart the Docker stack** after ingest path / env changes (overlay surfaces `restart_required`).
- Extra roots beyond index 0 use the **compose multi-bind generator** (ADR-072 / LUM-401): tiered snippet + optional auto-write of `docker-compose.override.yml` and `COMPOSE_FILE` chaining; stack restart still required.
- Settings JSON and OpenAPI snapshot are breaking for `filesystem_root` clients.
- Desktop tree remains **proprietary** (not in public AGPL export).

## Relation to other decisions

- **ADR 069** — LUM-329 desktop overlay.
- **ADR 070** — LUM-330 inbox watcher (separate from ingest-path watchers).

## Status history

- 2026-05-29: Partial — C1 verified.
- 2026-05-29: **Finalised** — C2–C6 verified; full plan closed.
