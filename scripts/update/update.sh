#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
#
# LUM-187 — operator update flow for a Docker Compose Lumogis install:
#   pull latest images -> capture rollback state -> restart -> run migrations
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
HEALTH_URL="${LUMOGIS_HEALTH_URL:-http://127.0.0.1:8000/healthz}"
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
docker compose pull

log "restarting stack (entrypoint applies pending migrations on boot)"
docker compose up -d

# --- Health + migration gate ----------------------------------------------
wait_for_stack_ready "$HEALTH_URL" "$HEALTH_RETRIES"
log "update complete — orchestrator healthy and migrations up to date"
