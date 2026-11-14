# Backup and restore (disaster recovery)

> Status: Active  
> Last reviewed: 2026-06-18  
> Verified against commit: 2e9320e  
> Owner: Docs Librarian

Lumogis ships **instance-scoped disaster recovery** for the Docker Compose stack: scheduled snapshots of Postgres, Qdrant, (when graph is enabled) FalkorDB, **and your uploaded documents, inbox files and capture photos and voice notes** into a host directory you control.

> **Snapshots hold your household's original documents and photos, unencrypted.** A `BACKUP_HOST_DIR` on a USB disk, a NAS share or a synced folder carries them too. Treat the backup folder like the documents themselves.

## What is covered

The admin **System status → Disaster recovery backup** card shows this list for your install ("Covered" and "Not covered"). It is a fixed list built by Core:

| Covered | Not covered |
| --- | --- |
| Database (accounts, settings, document index) | Your library folder (`INGEST_PATHS` / `FILESYSTEM_ROOT`): your own folder, back it up with your usual tools |
| Search index | The `.env` settings file: it holds the key that decrypts saved passwords, so keep a copy somewhere safe |
| Knowledge graph (when on) | Downloaded AI models: downloaded again when needed |
| Uploaded documents (`ai-workspace/uploads`) | |
| Inbox documents (`ai-workspace/inbox`, and `quarantine/`) | |
| Capture photos and voice notes (`ai-workspace/captures`) | |
| The stop-entities list (`ai-workspace/config/stop_entities.txt`) | |

The file copy is the workspace folder (`WORKSPACE_HOST_DIR`, default `./ai-workspace`) minus `backups/` and `mcp/`. Anything Lumogis writes under the workspace is covered; a path you moved outside it (`LUMOGIS_UPLOADS_PATH`, `LUMOGIS_INBOX_PATH`, `CAPTURE_MEDIA_ROOT`, `STOP_ENTITIES_PATH`) is listed as not covered, with its location.

The stop-entities list is the phrase list an admin edits on the knowledge graph settings page. Before LUM-922 it lived inside the orchestrator container and was lost on every recreate; on its first boot the orchestrator copies the shipped list to `config/stop_entities.txt` once and never overwrites it after that. `make update` and `make build` first copy a list that was edited inside the old container (`make rescue-stop-entities` runs that step alone). A restore puts your list back over the shipped one, which is moved to `restore-displaced/`.

`quarantine/` (inbox files that failed to ingest) is backed up and restored on purpose: it is household data waiting for a person, and restored files go back to `quarantine/`, never into the inbox.

The card also warns when documents or captures the database knows about are **missing from disk** (`files_missing`). If you did not delete them, restore them from a backup.

## Quick operator commands

| Command | Purpose |
| --- | --- |
| `make backup` | Run one DR snapshot now |
| `make backup-verify` | Re-verify the latest snapshot and refresh manifest status |
| `make backup-prune` | Apply retention (7 daily + 4 weekly successful snapshots) |
| `make restore SNAPSHOT=YYYYMMDD-HHMMSS` | Restore a snapshot (interactive `--yes` required) |

## Layout

Host path (default `./ai-workspace/backups`, override with `BACKUP_HOST_DIR`):

```
backups/
  snapshots/
    YYYYMMDD-HHMMSS/
      manifest.json       # schema_version 2 (1 = databases only; both are readable)
      postgres.dump
      qdrant/*.snapshot
      falkordb/dump.rdb   # when graph enabled
      files/workspace/    # uploads, inbox, captures (LUM-892)
      files.list          # sha256 of every file (sha256sum -c format)
      files.inodes        # hash cache for the next snapshot (no file names)
  backup_*.zip            # legacy logical exports (POST /backup) — not DR
  users/                  # per-user export archives (LUM-35)
```

Inside containers:

- Orchestrator reads **`/workspace/backups`** (fixed path).
- Backup sidecar writes **`/backups`** (same host bind via Compose) and reads and restores the workspace at **`/workspace`** (the same host folder the orchestrator uses, `WORKSPACE_HOST_DIR`).

### Files in a snapshot

- **One physical copy of each unchanged file.** Snapshots are hard-linked against the previous one (`rsync --link-dest`), so a daily backup of a large library adds only what changed. Hard links protect against deletion and overwrite, **not against disk failure**: that is the job of an offsite copy (LUM-489). Run `make backup-verify ARGS="/backups/snapshots/<snapshot-id> --deep"` by hand about once a month; it re-reads every file against `files.list`. Nothing schedules it, because a full read of the library every week costs too much on a household disk. A snapshot that failed verification is never used as the base of the next one, so the next backup is a full copy from the workspace and corruption is not carried forward.
- **Snapshot files are owned by root and read-only on purpose.** The orchestrator mounts the backup folder read-write, and hard links make every retained snapshot share one file, so a single write would change them all. Do not `chmod` or `chown` files inside `snapshots/`.
- **Filesystems.** The backup folder must support hard links. On CIFS/NTFS shares (and some Docker Desktop binds) the copy is retried without owner and permission changes; if hard links are missing too, each snapshot is a full copy and the log says so (`hard links unavailable`). If the copy still fails, the snapshot keeps the databases and the card shows the files as missing from the backup.
- **Where to put the backup folder.** The default `ai-workspace/backups`, or a folder **outside** the workspace. Never another folder inside it: the backup excludes it, but the orchestrator's ownership repair walks it on every boot.
- **The first backup after upgrading** copies and hashes the whole library and can take longer than the 900 s warning once.
- **The backup image carries the backup scripts.** `make update` rebuilds it. After updating a source install any other way, run `docker compose build backup` once, or backups stay database-only (the card says "The latest backup has no copy of uploaded documents or captures"). Rolling the backup image back to a version before schema 2 cannot verify or restore schema-2 snapshots.
- **Avoid updating at the backup hour** (03:00 by default): `make update` recreates the backup container, which stops a backup that is running at that moment.

## Configuration (.env)

| Variable | Default | Notes |
| --- | --- | --- |
| `BACKUP_HOST_DIR` | `./ai-workspace/backups` | Host bind only — relocation knob |
| `BACKUP_ENABLED` | `true` | Set `false` and `docker compose stop backup` to opt out |
| `BACKUP_SCHEDULE_CRON` | `0 3 * * *` | Container-local time — set `TZ` for local 03:00 |
| `BACKUP_STALE_HOURS` | `24` | Admin UI stale warning |
| `BACKUP_INCLUDE_FALKORDB` | auto | `true` when `GRAPH_MODE` ≠ `disabled` and `FALKORDB_URL` set |
| `WORKSPACE_HOST_DIR` | `./ai-workspace` | Host folder mounted at `/workspace` in the orchestrator **and** the backup sidecar |

Do **not** set `BACKUP_DIR` to relocate backups — use `BACKUP_HOST_DIR` on the host.

### Capture photos and voice notes

Captures live in `<workspace>/captures` (`ai-workspace/captures` by default). Before this change they sat inside the orchestrator container and were deleted by every image update. `make update` and `make build` copy any captures still inside the old container to the workspace first (`make rescue-captures` does the same by hand). If you run `docker compose up -d --build` yourself, run `make rescue-captures` first. If you mounted `/opt/lumogis/data` yourself, set `LUMOGIS_DATA_DIR=/opt/lumogis/data` to keep using it. An explicit `CAPTURE_MEDIA_ROOT` or `LUMOGIS_DATA_DIR` must point at a mounted path, or the files are lost on recreate and are not backed up.

## Restore procedure

1. **Stop** `orchestrator` and `lumogis-web`. Restore also probes `/healthz` on both services and **refuses** if either is still responding (override only for maintainer scripts: `RESTORE_SKIP_QUIESCE=1`).
2. Run `make restore SNAPSHOT=<id>` and confirm with `--yes`.
3. If the snapshot manifest includes FalkorDB (not `skipped`), restore copies `dump.rdb` **even when graph is currently disabled** — match the snapshot, not today's `GRAPH_MODE`.
4. If FalkorDB was restored via `dump.rdb`, restart the `falkordb` service.
5. Start orchestrator + web; check `/healthz` and a sample search.

Restore order: Postgres → Qdrant (recreate collections from manifest meta, upload snapshots) → FalkorDB → files.

**Files.** Before touching anything, restore re-reads every file in the snapshot against `files.list`; a mismatch is reported and the restore still goes on (one bad file beats no restore), then exits non-zero. Files are **copied into the workspace and nothing is deleted**: a file that differs from the snapshot is moved to `ai-workspace/restore-displaced/<snapshot-id>/` first, and files that exist only in the workspace stay where they are (the log lists them). Restored files are owned by `LUMOGIS_RUN_UID` (from `.env`) or, if it is unset, by the owner of the workspace folder, so a restore on a new machine does not leave the old machine's user id behind. If that owner is root (a workspace folder that Docker itself created), the orchestrator hands the files to its runtime user on its next boot. Only the owner gets write permission back; a group-writable workspace loses `g+w` on restored files.

A snapshot made before file backup (schema 1) restores the databases only; restore logs `snapshot holds no files` and, after the stack starts, the card warns about documents missing from disk.

**Backup capture notes:** FalkorDB dumps wait for `BGSAVE` and require `LASTSAVE` to advance before copying `dump.rdb`. Qdrant backup **fails** if `/collections` is unreachable (no silent empty-vector snapshot).

## Legacy POST /backup

`POST /backup` remains a **logical JSON zip export** (lossy, no FalkorDB, Qdrant vectors omitted). Use **`make backup`** for DR.

## Admin UI

**System status → Disaster recovery backup** shows last verified snapshot, age, size (the disk this snapshot actually uses, not the logical size), store coverage, the Covered / Not covered lists, and warnings: a stale backup, a backup that has no copy of the files, and documents or captures missing from disk.

## See also

- Decision record: [`docs/decisions/098-lum-185-backup-restore.md`](../decisions/098-lum-185-backup-restore.md) (**ADR 098**, LUM-185)
- Maintainer notes: `docs/private/ops/backup-restore-dev.md`
- Release gate: `docs/RELEASE-MANUAL-CHECKLIST.md` MS-009
