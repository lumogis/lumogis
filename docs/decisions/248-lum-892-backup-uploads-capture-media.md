# ADR-248: Lumogis-owned files live in the workspace and are part of the DR snapshot
**Status:** Finalised
**Created:** 2026-11-09
**Last updated:** 2026-11-09
**Decided by:** claude-opus-5-5 (/explore --headless LUM-892), revised at /review-plan arbitration R1
**Finalised by:** /verify-plan 2026-11-09 (claude-opus-5-5)
**Plan:** `.cursor/plans/LUM-892-backup-uploads-capture-media.plan.md`
**Exploration:** `.cursor/explorations/LUM-892-backup-uploads-capture-media.md`
**Draft mirror:** `.cursor/adrs/LUM-892-backup-uploads-capture-media.md`
**Linear:** LUM-892
**Amends:** ADR 098 (backup and restore, LUM-185): the DR snapshot now holds files as well as databases.

## Context

The LUM-185 DR backup (ADR 098) snapshots Postgres, Qdrant and FalkorDB, but not the files those indexes point at: uploaded documents (`ai-workspace/uploads/`), inbox-ingested originals (`ai-workspace/inbox/`) and capture media. Capture media defaulted to `/opt/lumogis/data/captures` inside the orchestrator container, and no compose file mounted that path. On 2026-11-09 a throwaway-container check showed that a recreate, which every `make update` and `up -d --build` triggers, deletes it. A new container cannot reach the old writable layer, so a boot-time migration cannot rescue it. LUM-887 (ADR 246) runs the orchestrator as the checkout owner. It re-owns root-owned entries in host-visible "shared" trees and everything in container-only "owned" trees, and it forbids widening "owned" to host-visible paths. The Family library (LUM-890) will keep shared uploads in Lumogis storage, so this storage needs to be durable and backed up first.

## Decision

1. **Storage.** Capture media defaults to `{WORKSPACE_PATH}/captures` (host `${WORKSPACE_HOST_DIR:-./ai-workspace}/captures`), next to uploads and the inbox. An explicit `CAPTURE_MEDIA_ROOT` or `LUMOGIS_DATA_DIR` still wins; blank values are ignored (`services/media_storage.py` `_resolve_capture_media_root`). `capture_attachments.storage_key` is relative, so no data rewrite is needed. The rule is: everything Lumogis stores lives in the workspace, minus `backups/`.
2. **Snapshot.** The DR snapshot gains a `files` store (`scripts/backup/lib/files.sh`): a hard-linked copy of the workspace, minus `backups/`, `mcp/` and any directory that is the backup root, under `snapshots/<id>/files/workspace/`. It is made **after** `pg_dump` with `rsync -a --numeric-ids --hard-links --chown=0:0 --chmod=Fa-w,Fa-s,Dgo-w --link-dest=<newest snapshot with a file tree that passed verify>`, plus a `files.list` in `sha256sum` format and a name-free `files.inodes` hash cache (`inode:size:mtime` keys, reused only from a verified snapshot). Snapshot files are **root-owned and read-only**, and snapshot directories lose group/other write: the non-root orchestrator mounts `BACKUP_HOST_DIR` read-write, and hard-linked snapshots share inodes, so a writable snapshot file would let one write change every retained snapshot. When rsync fails (for example CIFS refusing ownership changes), the copy is retried once without owner and permissions (`ownership: "not_preserved"`). If that also fails, the snapshot keeps its databases, records `stores.files.status: "failed"`, and `backup.sh` exits 1 after saving it. Missing `rsync` or an unmounted workspace still fail the whole backup. The manifest moves to `schema_version: 2`; verify and restore accept 1 and 2, and `manifest.json` is written `0644` so the non-root orchestrator can read it. The backup service mounts the workspace (`WORKSPACE_HOST_DIR`, default `./ai-workspace`) read-write, because restore writes there.
3. **Restore.** Order is Postgres → Qdrant → FalkorDB → files, after a `--deep` checksum pass that reports damaged files and lets the restore continue (exit 1 at the end). File restore never deletes: extra files are reported, and files that differ are moved to `restore-displaced/<snapshot-id>/`; a symlinked `restore-displaced` is refused. Files are **copied** (never hard-linked), as `LUMOGIS_RUN_UID:GID` or else the owner of the workspace root, with owner write restored. ADR 246's shared-tree repair re-owns only root-owned entries, so it cannot fix a restore that carries another machine's uid. It still finishes the job when the workspace owner is root.
4. **Rescue.** `scripts/update/rescue-captures.sh` (`make rescue-captures`) copies the old container's capture root out of the **old** container before it is recreated, unless that root is on a mount. It checks first while the container runs, checks free space, stops the container only when there is something to copy, copies once, and merges without overwriting (a differing file stops the update and keeps the staged copy). It is host-portable bash. `make update`, `make build` and `make dev` run it right before the recreate, and each stops if it fails. `make update` also rebuilds the `build:`-only backup image (a failed build only warns), and `make rollback` pins `CAPTURE_MEDIA_ROOT=/workspace/captures` for the orchestrator when `.env` sets no capture root.
5. **Honest status.** `GET /api/v1/admin/diagnostics/backup-status` gains a `files` store item and a fixed ten-item `coverage` list of what is covered and not covered (library folder, `.env` with the credential key, models, stop-entities list). It also gains `files_missing` and `files_missing_by_area`: uploaded documents and capture attachments whose file is absent raise `backup_files_missing`, counted with `DISTINCT` so a published document's shared-projection row is not counted twice. Inbox originals are reported but raise no warning, because people clear the inbox by hand. A latest snapshot without a file tree, or with a failed one, raises `backup_files_not_in_snapshot`. The count is cached for 300 s.

## Alternatives Considered

See `.cursor/explorations/LUM-892-backup-uploads-capture-media.md`.

- **Named volume at `/opt/lumogis/data`:** two storage rules, and captures stay hidden from the host owner's own backups.
- **Bind onto `/opt/lumogis/data`:** needs a special case in ADR 246's owned/shared repair.
- **tar per snapshot:** retention multiplies the whole library about 11×. Kept as the fallback if hard links are unavailable.
- **restic local repo:** repository password and an opaque store; belongs to offsite (LUM-489).
- **Entrypoint migration / Dockerfile `VOLUME`:** cannot reach data already in the writable layer.
- **offen/docker-volume-backup:** needs the Docker socket, and its separate format sits outside the manifest/verify/status chain.
- **Link against any finalised snapshot, whatever its verify status** (the plan's arbitration, D4-2): rejected during implementation. rsync's quick check compares size and mtime, so a bit-rotted inode in a snapshot that failed verify would be linked forward and blessed by a fresh `files.list`. With no verified snapshot the next one is a full copy.
- **A read-only workspace mount for the scheduled backup service:** rejected at arbitration. The container is already root with full database credentials, and a second `-v` for restore has uncertain merge semantics. A shell test asserts the snapshot path never writes to the workspace instead.

## Consequences

- **Easier:** one host folder to understand and copy. Restores bring back openable, writable documents and downloadable captures, even on a machine with a different uid. Anything Lumogis writes under the workspace (for example a future upload-to-Family destination) is covered automatically. The LUM-891 family **folder** is the library folder, outside the workspace, so it is not. LUM-489 offsite builds on plain folders.
- **Harder:**
  - The backup service now reads and writes the workspace, and the backup toolkit owns file ownership on both snapshot and restore.
  - Hard-linked snapshots are one physical copy of each unchanged file: they protect against deletion, not media failure (LUM-489). Reused hashes are not re-read, so bit rot is found only by `make backup-verify ARGS="<snapshot> --deep"`, which the backup guide advises monthly.
  - Snapshot size grows by the library plus changes, and the disk preflight sizes the delta (`files_estimate_bytes`).
  - Copying snapshots offsite with a tool that ignores hard links costs full size.
  - A database-only snapshot (failed file copy) still counts for retention, so a file copy that keeps failing past the keep window prunes the last file-bearing snapshot. The backup card warns on every such run (note at `scripts/backup/backup.sh`).
  - The scheduled job is `backup.sh run; backup.sh prune`, so retention also runs after a database-only snapshot.
  - Rolling the backup image back to a version before this change cannot verify or restore schema-2 snapshots.
- **Future chunks must know:**
  - Any new place Lumogis writes user files must be under `WORKSPACE_PATH`, or it is neither backed up nor listed as covered.
  - `manifest.json` readers must accept schema 1 and 2.
  - The repo `.gitignore` must ignore every workspace tree that holds household data.
  - `BACKUP_HOST_DIR` belongs at the default `ai-workspace/backups` or outside the workspace; another folder inside it is excluded from the snapshot but walked by ADR 246's ownership repair.

## Revisit conditions

- If a supported `BACKUP_HOST_DIR` filesystem cannot hard-link (e.g. a CIFS/NTFS mount becomes a documented setup), switch the files store to tar for that case or make it selectable.
- If a household library routinely exceeds the backup disk even with hard links, revisit restic (LUM-489) for the local tier.
- If Lumogis Server (Persona C, LUM-490) cannot express a single workspace root, revisit the "everything under `WORKSPACE_PATH`" rule.
- If ADR 246's shared-tree repair stops re-owning root-owned entries, a restore into a root-owned workspace must set the runtime owner itself.
- If LUM-891 decides the family folder belongs in the DR snapshot, the files store gains a second root.

## Status history
- 2026-11-09: Draft created by /explore (headless, `run-workflow` LUM-892)
- 2026-11-09: Revised during /review-plan --arbitrate R1. Snapshots are root-owned and read-only, and restore sets the owner (the critique showed that ADR 246's repair re-owns only root-owned entries, and that `rsync -a` made snapshots writable by the orchestrator uid). The file copy degrades instead of failing the DB backup. The rescue is mount-aware and host-portable. `make rollback` keeps captures in the workspace. The LUM-891 coverage claim is corrected, and `files.list` is in `sha256sum` format.
- 2026-11-09: Finalised by /verify-plan — implementation confirmed the decision, with deviations recorded: `--link-dest` and hash reuse only from a snapshot that passed verify; snapshot directories lose group/other write and files lose setuid/setgid; restore refuses a symlinked `restore-displaced`; `manifest.json` is `0644`; the cron line prunes after a failed run. Live proof: `make compose-test-backup` (foreign-uid restore, root-owned read-only snapshots, rescue of a stopped container), the I-3 rescue run, and 50 000 files measured at 13.4 s for an unchanged second backup and 0.6 s for the missing-file count.
