#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
#
# LUM-187 — operator update flow for a git-checkout Lumogis install:
#   capture rollback state -> pull the images that are not built here ->
#   build the Lumogis images from this checkout -> rescue capture media and the
#   stop-entities list from the old container -> restart -> run migrations
#   (on boot, via orchestrator/docker-entrypoint.sh) -> confirm health.
#
# An install from the release files has no scripts: it updates with
#   docker compose pull && docker compose up -d   (docs/deployment/upgrading.md).
# With the ghcr overlay (COMPOSE_FILE=…:docker-compose.ghcr.yml) the same steps
# pull the Lumogis images instead, and the build finds nothing to build (LUM-980).
#
# Safe by design:
#   * Records per-service image digests to a state file BEFORE pulling,
#     generates rollback-compose.override.yml, so `make rollback` pins them back
#     (required for ghcr-overlay installs, where :latest would otherwise float).
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
This will rebuild Lumogis from this checkout (pulling the images it does
not build) and restart the stack.
Database migrations run automatically on restart (idempotent).
Recommended: run `make backup` first so `make rollback` can restore data.
MSG
  read -r -p "Proceed with update? [y/N] " reply
  [[ "$reply" =~ ^[Yy]$ ]] || die "aborted by operator"
fi

# --- Capture rollback state (per-service image refs) ------------------------
capture_rollback_state "$STATE_FILE" "$OVERRIDE_FILE" "$WRITE_OVERRIDE"

# --- Pull + build -----------------------------------------------------------
installing="$(git -C "$ROOT" describe --tags --always --dirty 2>/dev/null || echo "unknown")"
log "installing Lumogis ${installing} from this checkout"

log "pulling the images that are not built here"
# --ignore-buildable: the Lumogis images are built from this checkout
# (pull_policy: build) and have no tag to pull (LUM-900). With the ghcr overlay
# they are not buildable, so this pulls them too.
docker compose pull --ignore-buildable

# Every Lumogis image bakes its code (and backup bakes scripts/backup/, LUM-892),
# so rebuild them all; the layer cache makes it quick when nothing changed. Under
# the ghcr overlay there is nothing to build. A failed build stops here, before
# anything is stopped: `up` would only try the same build again.
log "building the Lumogis images from this checkout"
docker compose build --pull \
  || die "building the Lumogis images failed (see above; network?). Nothing was stopped or changed. Fix the error and run the update again."

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
