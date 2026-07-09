#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
#
# LUM-187 — roll the stack back to the image refs captured by the last
# `make update` (scripts/update/update.sh). Pins the previous images via
# rollback-compose.override.yml and restarts. Schema migrations are
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
HEALTH_URL="${LUMOGIS_HEALTH_URL:-http://127.0.0.1:8000/healthz}"
HEALTH_RETRIES="${LUMOGIS_HEALTH_RETRIES:-60}"
BACKUP_MAX_AGE_HOURS="${LUMOGIS_BACKUP_MAX_AGE_HOURS:-24}"

command -v docker >/dev/null 2>&1 || die "docker not found on PATH"
docker compose version >/dev/null 2>&1 || die "docker compose v2 required"
[[ -s "$STATE_FILE" ]] || die "no rollback state at $STATE_FILE — run 'make update' first (it records previous images)"

# --- Require a recent backup (LUM-185) ------------------------------------
log "checking for a backup snapshot within ${BACKUP_MAX_AGE_HOURS}h"
newest_age_min="$(
  docker compose run --rm -T backup sh -c '
    d=/backups/snapshots
    latest=$(ls -1t "$d" 2>/dev/null | grep -v "^\.tmp$" | head -1)
    [ -n "$latest" ] || exit 3
    now=$(date +%s)
    mt=$(date -r "$d/$latest" +%s 2>/dev/null || stat -c %Y "$d/$latest")
    echo $(( (now - mt) / 60 ))
  ' 2>/dev/null
)" || die "no backup snapshot found — run 'make backup' before rolling back (or set LUMOGIS_BACKUP_MAX_AGE_HOURS to override window)"

if [[ -n "$newest_age_min" ]] && (( newest_age_min > BACKUP_MAX_AGE_HOURS * 60 )); then
  die "newest backup is ${newest_age_min}min old (> ${BACKUP_MAX_AGE_HOURS}h). Run 'make backup' first."
fi
log "backup OK (newest snapshot ${newest_age_min:-?}min old)"

# --- Confirmation ---------------------------------------------------------
if [[ "${LUMOGIS_ASSUME_YES:-}" != "1" ]]; then
  echo "About to pin previously-running images and restart:"
  cut -f3 "$STATE_FILE" 2>/dev/null | sed 's/^/  /' || cut -f2 "$STATE_FILE" | sed 's/^/  /'
  read -r -p "Proceed with rollback? [y/N] " reply
  [[ "$reply" =~ ^[Yy]$ ]] || die "aborted by operator"
fi

# --- Re-pin previous images + restart -------------------------------------
log "pulling previous image digests"
while IFS=$'\t' read -r _svc _img ref; do
  [[ -z "$ref" ]] && continue
  docker pull "$ref" || warn "could not pull $ref (may already be local)"
done <"$STATE_FILE"

compose_with_rollback_override "$OVERRIDE_FILE"

log "restarting stack on previous images (compose override pinned)"
docker compose up -d

# --- Health + migration gate ----------------------------------------------
wait_for_stack_ready "$HEALTH_URL" "$HEALTH_RETRIES"
log "rollback complete — orchestrator healthy"
log "if data changed under the new version, restore with: make restore SNAPSHOT=<id>"
