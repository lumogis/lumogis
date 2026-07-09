# Backup and restore (disaster recovery)

> Status: Active  
> Last reviewed: 2026-06-18  
> Verified against commit: 2e9320e  
> Owner: Docs Librarian

Lumogis ships **instance-scoped disaster recovery** for the Docker Compose stack: scheduled snapshots of Postgres, Qdrant, and (when graph is enabled) FalkorDB into a host directory you control.

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
      manifest.json
      postgres.dump
      qdrant/*.snapshot
      falkordb/dump.rdb   # when graph enabled
  backup_*.zip            # legacy logical exports (POST /backup) — not DR
  users/                  # per-user export archives (LUM-35)
```

Inside containers:

- Orchestrator reads **`/workspace/backups`** (fixed path).
- Backup sidecar writes **`/backups`** (same host bind via Compose).

## Configuration (.env)

| Variable | Default | Notes |
| --- | --- | --- |
| `BACKUP_HOST_DIR` | `./ai-workspace/backups` | Host bind only — relocation knob |
| `BACKUP_ENABLED` | `true` | Set `false` and `docker compose stop backup` to opt out |
| `BACKUP_SCHEDULE_CRON` | `0 3 * * *` | Container-local time — set `TZ` for local 03:00 |
| `BACKUP_STALE_HOURS` | `24` | Admin UI stale warning |
| `BACKUP_INCLUDE_FALKORDB` | auto | `true` when `GRAPH_MODE` ≠ `disabled` and `FALKORDB_URL` set |

Do **not** set `BACKUP_DIR` to relocate backups — use `BACKUP_HOST_DIR` on the host.

## Restore procedure

1. **Stop** `orchestrator` and `lumogis-web`. Restore also probes `/healthz` on both services and **refuses** if either is still responding (override only for maintainer scripts: `RESTORE_SKIP_QUIESCE=1`).
2. Run `make restore SNAPSHOT=<id>` and confirm with `--yes`.
3. If the snapshot manifest includes FalkorDB (not `skipped`), restore copies `dump.rdb` **even when graph is currently disabled** — match the snapshot, not today's `GRAPH_MODE`.
4. If FalkorDB was restored via `dump.rdb`, restart the `falkordb` service.
5. Start orchestrator + web; check `/healthz` and a sample search.

Restore order: Postgres → Qdrant (recreate collections from manifest meta, upload snapshots) → FalkorDB.

**Backup capture notes:** FalkorDB dumps wait for `BGSAVE` and require `LASTSAVE` to advance before copying `dump.rdb`. Qdrant backup **fails** if `/collections` is unreachable (no silent empty-vector snapshot).

## Legacy POST /backup

`POST /backup` remains a **logical JSON zip export** (lossy, no FalkorDB, Qdrant vectors omitted). Use **`make backup`** for DR.

## Admin UI

**System status → Disaster recovery backup** shows last verified snapshot, age, size, store coverage, and a stale warning when older than `BACKUP_STALE_HOURS`.

## See also

- Decision record: [`docs/decisions/098-lum-185-backup-restore.md`](../decisions/098-lum-185-backup-restore.md) (**ADR 098**, LUM-185)
- Maintainer notes: `docs/private/ops/backup-restore-dev.md`
- Release gate: `docs/RELEASE-MANUAL-CHECKLIST.md` MS-009
