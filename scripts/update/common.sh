#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
# Shared helpers for scripts/update/{update,rollback}.sh (LUM-187).

log() { printf '==> %s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
die() { printf '%s: FAIL: %s\n' "${UPDATE_SCRIPT_NAME:-update}" "$*" >&2; exit 1; }

# Append the generated rollback override to COMPOSE_FILE for image pinning.
compose_with_rollback_override() {
  local override="$1"
  [[ -f "$override" ]] || die "missing rollback override at $override (re-run make update to regenerate)"
  if [[ -n "${COMPOSE_FILE:-}" ]]; then
    export COMPOSE_FILE="${COMPOSE_FILE}:${override}"
  else
    export COMPOSE_FILE="docker-compose.yml:${override}"
  fi
}

# Record per-service image refs (with digest when available) for rollback pinning.
capture_rollback_state() {
  local state_file="$1"
  local override_file="$2"
  local write_script="$3"

  log "recording per-service image refs for rollback -> $state_file"
  mkdir -p "$(dirname "$state_file")"
  : >"$state_file"

  local svc cid img ref
  while IFS= read -r svc; do
    [[ -z "$svc" ]] && continue
    cid="$(docker compose ps -q "$svc" 2>/dev/null | head -1)"
    [[ -z "$cid" ]] && continue
    img="$(docker inspect --format '{{.Config.Image}}' "$cid")"
    ref="$(docker inspect --format '{{if .RepoDigests}}{{index .RepoDigests 0}}{{else}}{{.Config.Image}}{{end}}' "$cid" 2>/dev/null || true)"
    if [[ -z "$ref" ]]; then
      ref="$(docker image inspect --format '{{index .RepoDigests 0}}' "$img" 2>/dev/null || echo "$img")"
    fi
    printf '%s\t%s\t%s\n' "$svc" "$img" "$ref" >>"$state_file"
  done < <(docker compose config --services 2>/dev/null)

  if [[ ! -s "$state_file" ]]; then
    warn "could not capture running service images; rollback may be incomplete"
    return 0
  fi

  if ! python3 "$write_script" "$state_file" "$override_file"; then
    die "failed to write rollback compose override"
  fi
  log "wrote rollback override -> $override_file"
}

# Wait for /healthz then verify migrations applied and boot did not log migration errors.
wait_for_stack_ready() {
  local health_url="$1"
  local health_retries="$2"

  log "waiting for health: $health_url"
  local i
  for i in $(seq 1 "$health_retries"); do
    if curl -sf --connect-timeout 2 "$health_url" >/dev/null 2>&1; then
      break
    fi
    if [[ "$i" -eq "$health_retries" ]]; then
      warn "orchestrator did not become healthy within $((health_retries * 3))s"
      warn "check logs: docker compose logs --tail=200 orchestrator"
      die "health gate failed — consider 'make rollback' (restores previous images)"
    fi
    sleep 3
  done

  log "checking migration status"
  local dry_out
  if ! dry_out="$(docker compose exec -T orchestrator python3 /app/db_migrations.py --dry-run 2>&1)"; then
    warn "$dry_out"
    die "migration dry-run failed — stack may be running with incomplete schema"
  fi
  if echo "$dry_out" | grep -q 'pending migration(s) would be applied'; then
    warn "$dry_out"
    die "pending migrations remain after restart — update did not apply schema changes"
  fi

  if docker compose logs orchestrator 2>&1 | tail -300 | grep -qE \
    '\[entrypoint\] WARNING: migration runner exited non-zero|\[migrations\] ERROR while applying'; then
    die "orchestrator boot logs report migration errors — inspect: docker compose logs orchestrator"
  fi
}
