# ADR-070: Folder watch — auto-ingest into `/workspace/inbox` (LUM-330)

**Status:** Finalised
**Created:** 2026-05-27
**Last updated:** 2026-05-28
**Decided by:** `/explore` LUM-330 + `/review-plan` R1 + `/verify-plan` LUM-330
**Finalised by:** `/verify-plan` LUM-330 — implementation confirmed

## Context

v0.1 Docker-track frictionless ingest: files dropped into `/workspace/inbox` must auto-index with no manual trigger or API call. A working `watchdog` watcher already shipped in `orchestrator/services/ingest.py` (`_InboxHandler`, lifespan-gated on `INBOX_OWNER_USER_ID`). The LUM-330 exploration audited ten hardening gaps (write-completion races, failure quarantine, operator env surface, poll fallback for SMB/NFS, observability) — none architectural.

## Decision

**Keep `watchdog>=5.0.0` and harden the in-process `_InboxHandler`.** All watcher and poll paths funnel through **`enqueue_inbox_file(path, *, user_id, source)`** before `ingest_file`. Operator surface via env: `LUMOGIS_INBOX_PATH`, `LUMOGIS_INBOX_MODE` ∈ `{event, poll, off}` (invalid → **`off`** fail-closed), `LUMOGIS_INBOX_STABILITY_DELAY_MS`, `LUMOGIS_INBOX_POLL_INTERVAL_S` (floor 5 s), `LUMOGIS_INBOX_MAX_FILE_MB`; `INBOX_OWNER_USER_ID` unchanged. Poll mode uses **`inbox_poll_should_ingest`** (mtime vs `file_index.updated_at`) before `ingest_file` to avoid re-hashing unchanged files. Quarantine terminal failures under `{WORKSPACE}/quarantine/` with `.error.json` sidecars. **`GET /healthz`** exposes opaque liveness tokens only; absolute **`inbox_path`** on auth-gated **`GET /api/v1/admin/diagnostics`**.

## Alternatives considered

See `.cursor/explorations/archived/LUM-330-folder-watch-inbox.md` — `watchfiles`, pure polling-only, Docker sidecar, `pyinotify` rejected or deferred.

## Consequences

**Easier:** Documented inbox contract; single seam for future LUM-132 `PreIngest` hooks; poll mode for degraded bind mounts.

**Harder / deferred:** per-file **`ingest_folder`** bulk walk now routes through **`_ingest_file_safe`** with containment (**LUM-409**, 2026-05-30); funneling bulk folder ingest through **`enqueue_inbox_file`** (inbox stability/quarantine semantics) remains deferred; per-user inbox subdirs; `Event.INBOX_FILE_QUARANTINED`.

**As shipped (verify notes):**

- Phases **E→A→B→D→C** delivered in one parent **LUM-330** implementation pass (exploration’s five child stories optional).
- `system_monitor` inbox depth counts top-level files only (recursive watcher caveat documented).

## Status history

- 2026-05-27: Draft created by `/explore` LUM-330.
- 2026-05-28: Revised during `/review-plan --arbitrate` R1 — poll fast-path, `/healthz` path removal, `ingest_folder` seam deferred.
- 2026-05-28: Finalised by `/verify-plan` LUM-330 — implementation confirmed; canonical copy this file.
- 2026-05-31: **LUM-409** — `ingest_folder` per-file walk uses shared **`_ingest_file_safe`** guards; **`enqueue_inbox_file`** seam for bulk folder ingest still open.

## Relation to other decisions

- **ADR 012** — single-owner inbox via `INBOX_OWNER_USER_ID` for v0.1.
- **ADR 013** — `file_index` dedupe reused by poll fast-path and `ingest_file`.
