#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
#
# LUM-187 — operator update flow for a Docker Compose Lumogis install:
#   pull latest images -> capture rollback state -> rebuild the backup image ->
#   rescue capture media and the stop-entities list from the old container ->
#   restart -> run migrations
#   (on boot, via orchestrator/docker-entrypoint.sh) -> confirm health.
#
# Safe by design:
#   * Records per-service image digests to a state file BEFORE pulling,
#     generates rollback-compose.override.yml, so `make rollback` pins them back
#     (required for GHCR installs where IMAGE_TAG=:latest would otherwise float).
#   * Never deletes data. Migrations are idempotent (orchestrator/db_migrations.py).
#   * Post-health gate verifies no pending migrations and no boot migration errors.
#   * Strongly recommends a fresh backup first (see `make backup`, LUM-185).
#
# Usage: scripts/update/update.sh            # prompts unless LUMOGIS_ASSUME_YES=1
#        LUMOGIS_ASSUME_YES=1 make update
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

UPDATE_SCRIPT_NAME=update
# shellcheck source=scripts/update/common.sh
source "$ROOT/scripts/update/common.sh"

STATE_DIR="${LUMOGIS_UPDATE_STATE_DIR:-$ROOT/.lumogis-state}"
STATE_FILE="$STATE_DIR/previous-images.txt"
OVERRIDE_FILE="$STATE_DIR/rollback-compose.override.yml"
WRITE_OVERRIDE="$ROOT/scripts/update/write_rollback_override.py"
HEALTH_RETRIES="${LUMOGIS_HEALTH_RETRIES:-60}"

command -v docker >/dev/null 2>&1 || die "docker not found on PATH"
docker compose version >/dev/null 2>&1 || die "docker compose v2 required"

# --- Confirmation ---------------------------------------------------------
if [[ "${LUMOGIS_ASSUME_YES:-}" != "1" ]]; then
  cat <<'MSG'
This will pull the latest Lumogis images and restart the stack.
Database migrations run automatically on restart (idempotent).
Recommended: run `make backup` first so `make rollback` can restore data.
MSG
  read -r -p "Proceed with update? [y/N] " reply
  [[ "$reply" =~ ^[Yy]$ ]] || die "aborted by operator"
fi

# --- Capture rollback state (per-service image refs) ------------------------
capture_rollback_state "$STATE_FILE" "$OVERRIDE_FILE" "$WRITE_OVERRIDE"

# --- Pull + restart -------------------------------------------------------
log "pulling latest images"
# --ignore-buildable: qdrant (lumogis-qdrant) and the other build-only images
# have no registry; a plain pull asks for them and exits non-zero (LUM-900).
docker compose pull --ignore-buildable

# The backup service is build-only (no GHCR image) and bakes scripts/backup/ into
# its image, so pull + up never refresh it. Without this an updated install keeps
# the old image and writes database-only snapshots forever (LUM-892). A failed
# build (GitHub, apt or Docker Hub unreachable) must not block an image update;
# the backup card reports the stale image.
log "rebuilding the backup image (its scripts are baked in; LUM-892)"
docker compose build --pull backup \
  || warn "could not rebuild the backup image (network?); backups stay database-only until 'docker compose build backup' succeeds — the backup card will say so"

# Capture media used to live in the orchestrator container's writable layer and
# was deleted by the recreate below. Copy it to the workspace first. Runs last,
# right before the recreate, so the orchestrator is stopped for seconds only.
log "rescuing capture media from the current orchestrator container (LUM-892)"
bash "$ROOT/scripts/update/rescue-captures.sh" \
  || die "capture rescue failed: the stack was not restarted, and your captures are still in the old orchestrator container. See the message above; 'docker compose start orchestrator' brings it back unchanged."

# The admin's stop-entities list lived in the same writable layer (LUM-922).
log "rescuing the stop-entities list from the current orchestrator container (LUM-922)"
bash "$ROOT/scripts/update/rescue-stop-entities.sh" \
  || die "stop-entities rescue failed: the stack was not restarted, and the list is still in the old orchestrator container. See the message above; 'docker compose start orchestrator' brings it back unchanged."

log "restarting stack (entrypoint applies pending migrations on boot)"
docker compose up -d \
  || die "docker compose up -d failed. If the capture rescue ran, the old orchestrator is stopped and its captures are already in <workspace>/captures. Fix the error above and run 'docker compose up -d' again."

# --- Health + migration gate ----------------------------------------------
wait_for_stack_ready "$(orchestrator_health_url)" "$HEALTH_RETRIES"
log "update complete — orchestrator healthy and migrations up to date"
