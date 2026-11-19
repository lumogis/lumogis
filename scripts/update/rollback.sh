#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
#
# LUM-187 — roll the stack back to the image refs captured by the last
# `make update` (scripts/update/update.sh). Pins the local rollback tags that
# `make update` made (lumogis-rollback/<project>/<service>:<time>, LUM-984),
# through rollback-compose.override.yml, which is generated from the state file on
# each run, and restarts. Schema migrations are
# forward-only and NOT reverted here — a rollback that crosses a migration
# boundary needs a data restore, so this script REQUIRES a recent backup
# (LUM-185) before proceeding.
#
# Usage: scripts/update/rollback.sh           # prompts unless LUMOGIS_ASSUME_YES=1
#        LUMOGIS_BACKUP_MAX_AGE_HOURS=24 (default) — fail if no snapshot within window
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

UPDATE_SCRIPT_NAME=rollback
# shellcheck source=scripts/update/common.sh
source "$ROOT/scripts/update/common.sh"

STATE_DIR="${LUMOGIS_UPDATE_STATE_DIR:-$ROOT/.lumogis-state}"
STATE_FILE="$STATE_DIR/previous-images.txt"
OVERRIDE_FILE="$STATE_DIR/rollback-compose.override.yml"
HEALTH_RETRIES="${LUMOGIS_HEALTH_RETRIES:-60}"
BACKUP_MAX_AGE_HOURS="${LUMOGIS_BACKUP_MAX_AGE_HOURS:-24}"

command -v docker >/dev/null 2>&1 || die "docker not found on PATH"
docker compose version >/dev/null 2>&1 || die "docker compose v2 required"
[[ -s "$STATE_FILE" ]] || die "no rollback state at $STATE_FILE — run 'make update' first (it records previous images)"
# Before the backup check (which builds the backup image): local, cheap, and
# nothing has happened yet (LUM-984).
check_rollback_images_present "$STATE_FILE"

# --- Require a recent backup (LUM-185) ------------------------------------
log "checking for a backup snapshot within ${BACKUP_MAX_AGE_HOURS}h"
# In a checkout `run` builds the image first (pull_policy: build, LUM-980), and
# with some Compose versions the build progress goes to stdout. Read only the
# marked line, so any output around it cannot be mistaken for the age.
newest_age_min="$(
  docker compose run --rm -T backup sh -c '
    d=/backups/snapshots
    latest=$(ls -1t "$d" 2>/dev/null | grep -v "^\.tmp$" | head -1)
    [ -n "$latest" ] || exit 3
    now=$(date +%s)
    mt=$(date -r "$d/$latest" +%s 2>/dev/null || stat -c %Y "$d/$latest")
    echo "LUMOGIS_BACKUP_AGE_MIN=$(( (now - mt) / 60 ))"
  ' 2>/dev/null | sed -n 's/^LUMOGIS_BACKUP_AGE_MIN=\([0-9][0-9]*\)$/\1/p'
)" || die "no backup snapshot found — run 'make backup' before rolling back (or set LUMOGIS_BACKUP_MAX_AGE_HOURS to override window)"

[[ -n "$newest_age_min" ]] || die "could not read the age of the newest backup snapshot — run 'make backup' before rolling back"
if (( newest_age_min > BACKUP_MAX_AGE_HOURS * 60 )); then
  die "newest backup is ${newest_age_min}min old (> ${BACKUP_MAX_AGE_HOURS}h). Run 'make backup' first."
fi
log "backup OK (newest snapshot ${newest_age_min}min old)"

# --- Confirmation ---------------------------------------------------------
log "rolling back to (datastore images such as postgres and qdrant go back too; if this update changed their version, restore the backup afterwards):"
cut -f1,3 "$STATE_FILE" | sed 's/^/  /'
if [[ "${LUMOGIS_ASSUME_YES:-}" != "1" ]]; then
  echo "About to pin previously-running images and restart."
  read -r -p "Proceed with rollback? [y/N] " reply
  [[ "$reply" =~ ^[Yy]$ ]] || die "aborted by operator"
fi

# --- Re-pin previous images + restart -------------------------------------
log "pulling previous registry images (local rollback tags are not pulled)"
pull_rollback_registry_refs "$STATE_FILE"

# The override is derived from the state file every time, so a deleted or stale
# one cannot block a rollback (LUM-984).
WRITE_OVERRIDE="$ROOT/scripts/update/write_rollback_override.py"
write_rollback_override_from_state "$STATE_FILE" "$OVERRIDE_FILE" "$WRITE_OVERRIDE"
compose_with_rollback_override "$OVERRIDE_FILE"

log "restarting stack on previous images (compose override pinned)"
docker compose up -d \
  || die "starting the previous images failed (see above). Some services may already run the previous images and others the new ones; 'docker compose ps' shows which. Fix the cause and run 'make rollback' again; if data changed under the new version, restore with: make restore SNAPSHOT=<id>"

# --- Health + migration gate ----------------------------------------------
wait_for_stack_ready "$(orchestrator_health_url)" "$HEALTH_RETRIES"
# The stack is back on the recorded images and healthy: no update is unfinished.
finish_update_marker "$STATE_FILE"
log "rollback complete — orchestrator healthy"
log "if data changed under the new version, restore with: make restore SNAPSHOT=<id>"
