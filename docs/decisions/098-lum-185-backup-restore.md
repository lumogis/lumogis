# ADR-098: Backup and restore — automated Postgres/FalkorDB/Qdrant snapshot

**Status:** Finalised
**Created:** 2026-06-14
**Last updated:** 2026-06-14
**Decided by:** /explore --headless LUM-185; implemented and finalised by /verify-plan --headless LUM-185

## Context

Lumogis is local-first; a single disk failure loses the knowledge graph,
document index, sessions, and entity memory — existential for a product built
on long-term personal knowledge. LUM-185 (v1.2) requires automated daily
snapshots of three heterogeneous stores (**Postgres**, **FalkorDB** optional,
**Qdrant**), a *tested* restore, integrity verification, `make backup` /
`make restore` / `make backup-verify`, and an admin backup-status surface.

Constraints shaping the option space: the household instance is **always on**
(downtime-during-backup is undesirable); backups must survive an orchestrator
crash (operational independence); artefacts must be portable across image
versions and individually verifiable; the solution must ship in the public
**AGPL Compose** stack **and** be reusable by the non-Docker **Lumogis Server**
(Persona C); and it must not overload the existing lossy logical `POST /backup`
route or the user-scoped per-user export (LUM-35 / ADR 016).

## Decision

Implement backup as **store-native dumps driven by a dedicated Compose sidecar
service**, with the dump/restore/verify logic factored into a shared
`scripts/backup/` toolkit that the **Lumogis Server** supervisor reuses via a
`systemd --user` timer.

Per run: `pg_dump -Fc` (Postgres, over the Docker network, no downtime);
Qdrant **snapshot REST API** + download; FalkorDB `BGSAVE` then copy `dump.rdb`
from a read-only `falkordb_data` mount when the FalkorDB overlay is active
(**DUMP/RESTORE** fallback when the volume mount is absent). Write
`manifest.json` + per-store artefacts under **`BACKUP_HOST_DIR`** (host bind) →
in-container **`/backups`** (sidecar) / **`/workspace/backups`** (orchestrator
readers); apply **7 daily / 4 weekly** retention; run an integrity check;
skip FalkorDB when `GRAPH_MODE=disabled`. Surface last-backup time/size/coverage
via **`GET /api/v1/admin/diagnostics/backup-status`** in the LUM-178 System
status panel. The legacy `POST /backup` is repositioned as a lightweight
logical export, not the canonical disaster-recovery path.

**Implementation notes (as-shipped):** `BACKUP_HOST_DIR` is the sole host
relocation knob; orchestrator status readers use a fixed `/workspace/backups`
mount. Restore requires quiescing orchestrator + lumogis-web; Qdrant empty-volume
restore uses Qdrant `POST …/snapshots/upload` multipart recovery (snapshot
embeds collection config). Integration
CI runs backup+verify smoke via `make compose-test-backup`; full wipe/restore
round-trip remains **MS-009** manual until a stable isolated CI path exists.
FalkorDB daily verify pins `redis-check-rdb-v13` to the same `FALKORDB_IMAGE`
tag as the graph service (vended into the backup sidecar at build time).

## Alternatives Considered

- **offen/docker-volume-backup sidecar** — filesystem-level volume tar with
  stop-during-backup; rejected for household downtime and version-locked,
  coarse volume restores.
- **restic/borg sidecar** — dedup/encryption/offsite; deferred as the future
  opt-in **offsite** path, not the v1 local baseline (still needs native dumps).
- **In-orchestrator `/backup` + APScheduler** — rejected: couples the backup to
  the process whose failure makes backups matter, and keeps a lossy re-embed
  restore.
- **Host cron + `docker exec`** / **Ofelia** / **whole-volume tar** — ruled out
  (host coupling, extra service, coarse/version-locked).

## Consequences

**Easier:** consistent, portable, individually-verifiable backups with no
downtime; a single shared toolkit serves both Compose and native Server; a
tested restore unblocks LUM-187 (update/rollback); admin visibility reuses
LUM-178.

**Harder / new:** one new Compose service to maintain; FalkorDB capture method
validated on dev stack (BGSAVE primary); Qdrant snapshot is single-node only
(acceptable for self-host); restore is a documented, gated operator procedure
(by design).

**Future chunks must know:** the canonical DR artefact layout + `manifest.json`
schema; that `/backup` is no longer the DR path; that offsite/encryption is a
separate later opt-in (restic/S3); that the backup-status endpoint is read-only.

## Revisit conditions

- **Offsite / encryption demand** → revisit restic/S3 as a child of LUM-185 with
  the shared scripts as the pre-dump step.
- **Qdrant adds non-REST or multi-node backup** → reconsider full-storage vs
  per-collection snapshot strategy.
- **FalkorDB ships a first-class export** beyond RDB/`DUMP` → re-evaluate the
  graph capture method.
- **Notification dispatcher (ADR 077) ships** → move the ">24h" warning fully
  onto the inbox channel (**LUM-424**).

## Status history

- 2026-06-14: Draft created by /explore --headless LUM-185.
- 2026-06-14: Finalised by /verify-plan --headless LUM-185 — implementation confirmed decision.
