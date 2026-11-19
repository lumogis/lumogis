#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
# Shared helpers for scripts/update/{update,rollback}.sh (LUM-187).

log() { printf '==> %s\n' "$*"; }
warn() { printf 'WARN: %s\n' "$*" >&2; }
die() { printf '%s: FAIL: %s\n' "${UPDATE_SCRIPT_NAME:-update}" "$*" >&2; exit 1; }

# Print KEY's value from ./.env the way Compose reads it: the last KEY= line wins,
# an optional `export ` prefix, surrounding quotes stripped, and for an unquoted
# value a ` #` comment dropped. Prints nothing when the file or the key is absent.
_dotenv_value() {
  local key="$1" line val
  [[ -f .env ]] || return 0
  line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?${key}=" .env | tail -1)" || return 0
  val="${line#*=}"
  val="${val#"${val%%[![:space:]]*}"}"
  if [[ "$val" == \"*\"* ]]; then
    val="${val#\"}"; val="${val%%\"*}"
  elif [[ "$val" == \'*\'* ]]; then
    val="${val#\'}"; val="${val%%\'*}"
  else
    val="${val%% #*}"
    val="${val%"${val##*[![:space:]]}"}"
  fi
  printf '%s' "$val"
}

# Append the generated rollback override to COMPOSE_FILE for image pinning.
# Exporting COMPOSE_FILE overrides the one in .env, so the .env value (where
# households set their overlays: ghcr, gpu, falkordb, ...) is carried over first;
# otherwise rollback would start the previous images without those overlays
# (LUM-990 MS-1, 2026-11-18).
compose_with_rollback_override() {
  local override="$1" files sep
  [[ -f "$override" ]] || die "missing rollback override at $override (re-run make update to regenerate)"
  files="${COMPOSE_FILE:-$(_dotenv_value COMPOSE_FILE)}"
  sep="${COMPOSE_PATH_SEPARATOR:-$(_dotenv_value COMPOSE_PATH_SEPARATOR)}"
  sep="${sep:-:}"
  export COMPOSE_FILE="${files:-docker-compose.yml}${sep}${override}"
}

# --- Rollback images (LUM-984) ----------------------------------------------
# `make update` rebuilds the Lumogis images under the same compose tag, so the
# running image loses its only name. Before anything is pulled or built, the
# update tags every running service's image
#   lumogis-rollback/<project>/<service>:<UTC time>
# and records that tag as the rollback ref. The prefix is local-only: it is never
# pulled (pull_policy: never in the override) and never pushed. The same constant
# lives in write_rollback_override.py; a test keeps the pair equal.
ROLLBACK_REPO_PREFIX="lumogis-rollback/"
# <p> and <s> are the output of _rollback_name_part; <ts> is one UTC time per capture.
ROLLBACK_REF_RE='^lumogis-rollback/[a-z0-9]([a-z0-9-]*[a-z0-9])?/[a-z0-9]([a-z0-9-]*[a-z0-9])?:[0-9]{8}T[0-9]{6}Z$'

# True when the ref is one of our local rollback tags (the one prefix test).
_is_rollback_ref() { [[ "$1" == "${ROLLBACK_REPO_PREFIX}"* ]]; }

# Prints "lumogis-rollback/<project>/" for a ref that matches the grammar;
# prints nothing and returns 1 otherwise (a hand-edited state prunes nothing).
_rollback_ref_project() {
  local ref="$1"
  if [[ "$ref" =~ $ROLLBACK_REF_RE ]]; then
    printf '%s%s/\n' "$ROLLBACK_REPO_PREFIX" "$(printf '%s' "${ref#"$ROLLBACK_REPO_PREFIX"}" | cut -d/ -f1)"
    return 0
  fi
  return 1
}

# A Docker path component: lower-case [a-z0-9] runs joined by '-'. A name that is
# already valid is used as is. Any other name is normalised and gets the POSIX
# cksum of the raw name, so two raw names that normalise alike stay distinct.
_rollback_name_part() {
  local text="$1" norm
  if [[ "$text" =~ ^[a-z0-9]([a-z0-9-]*[a-z0-9])?$ ]]; then
    printf '%s\n' "$text"
    return 0
  fi
  norm="$(printf '%s' "$text" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9][^a-z0-9]*/-/g; s/^-//; s/-$//')"
  [[ -n "$norm" ]] || norm=x
  printf '%s-%s\n' "$norm" "$(printf '%s' "$text" | cksum | awk '{print $1}')"
}

# `capture_rollback_state` writes it; `update.sh` removes it once the stack is healthy;
# `rollback.sh` removes it once its own health gate passes (D8).
finish_update_marker() { rm -f "$(dirname "$1")/update-in-progress"; }

# Generate the rollback override from the state file, at the moment it is used.
# The state file is the only record; the override is a pure function of it and .env.
write_rollback_override_from_state() {
  local state_file="$1" override_file="$2" write_script="$3" tmp
  mkdir -p "$(dirname "$override_file")"
  tmp="$(mktemp "$override_file.XXXXXX")"
  if ! python3 "$write_script" "$state_file" "$tmp" --env-file "${ROOT:-.}/.env"; then
    rm -f "$tmp"
    die "failed to write rollback compose override"
  fi
  chmod 0644 "$tmp"
  mv -f "$tmp" "$override_file"
}

# Record per-service image refs for rollback. Each running service's image is
# tagged lumogis-rollback/<project>/<service>:<UTC time> first (LUM-984), so the
# image keeps a name when the update rebuilds the compose tag.
#
# The state file is written atomically and only when every service was tagged.
# It is the only record; rollback.sh derives the override from it.
capture_rollback_state() {
  local state_file="$1"
  local override_file="$2"
  local marker
  marker="$(dirname "$state_file")/update-in-progress"
  ROLLBACK_KEPT_FROM=""

  # D8: an earlier update that never passed its health gate keeps its state while
  # the stack still fails the gate, so these images (possibly broken) never become
  # the rollback target and their prune never deletes the last good images.
  if [[ -f "$marker" && -s "$state_file" ]]; then
    local first_ref kept_ts _svc _img
    IFS=$'\t' read -r _svc _img first_ref <"$state_file" || true
    if _is_rollback_ref "$first_ref"; then kept_ts="${first_ref##*:}"; else kept_ts="an earlier update"; fi
    if ! ( wait_for_stack_ready "$(orchestrator_health_url)" 1 ) >/dev/null 2>&1; then
      log "the previous update (rollback state from $kept_ts) did not pass its health check and the stack is not healthy now, so its rollback state is kept: 'make rollback' still goes back to the images that ran before it. To record the images running now instead, delete $marker and run 'make update' again."
      # shellcheck disable=SC2034  # read by update.sh, which sources this file
      ROLLBACK_KEPT_FROM="$kept_ts"
      return 0
    fi
    log "the previous update did not pass its health check in time, but the stack is healthy now: recording the running images for rollback."
  fi

  log "recording per-service image refs for rollback -> $state_file"
  mkdir -p "$(dirname "$state_file")"

  local ts made="" state_tmp svc cid out img id project ref err t
  ts="$(date -u +%Y%m%dT%H%M%SZ)"
  state_tmp="$(mktemp "$state_file.XXXXXX")"

  while IFS= read -r svc; do
    [[ -z "$svc" ]] && continue
    cid="$(docker compose ps -q "$svc" 2>/dev/null | head -1)"
    [[ -z "$cid" ]] && continue
    # Project last: tab is IFS whitespace, so an empty middle field would collapse.
    if ! out="$(docker inspect --format '{{.Config.Image}}{{"\t"}}{{.Image}}{{"\t"}}{{index .Config.Labels "com.docker.compose.project"}}' "$cid" 2>&1)"; then
      while IFS= read -r t; do
        if [[ -n "$t" ]]; then docker rmi "$t" >/dev/null 2>&1 || true; fi
      done <<<"$made"
      rm -f "$state_tmp"
      die "could not read the running $svc container ($cid) to record its image: $out. Nothing was changed; run 'make update' again."
    fi
    IFS=$'\t' read -r img id project <<<"$out"
    [[ -n "$project" ]] || project="${COMPOSE_PROJECT_NAME:-default}"
    ref="${ROLLBACK_REPO_PREFIX}$(_rollback_name_part "$project")/$(_rollback_name_part "$svc"):$ts"
    if ! err="$(docker tag "$id" "$ref" 2>&1)"; then
      while IFS= read -r t; do
        if [[ -n "$t" ]]; then docker rmi "$t" >/dev/null 2>&1 || true; fi
      done <<<"$made"
      rm -f "$state_tmp"
      die "could not keep the running $svc image ($id) for rollback. Docker said: $err. Most likely the image is no longer on this host, because it was rebuilt or pulled after the container started; then the version running now cannot be rolled back to. Nothing was changed. To update, run 'make build' (it copies captures and the stop-entities list out of the old container before restarting on this checkout), then 'make update' again, which records the new images for the next rollback."
    fi
    made="${made}${ref}"$'\n'
    printf '%s\t%s\t%s\n' "$svc" "$img" "$ref" >>"$state_tmp"
  done < <(docker compose config --services 2>/dev/null)

  # D9: nothing running means nothing to roll back to. Keeping the state from the
  # update before would make `make rollback` silently go back two versions.
  if [[ ! -s "$state_tmp" ]]; then
    rm -f "$state_tmp" "$state_file" "$override_file" "$marker"
    warn "no running Lumogis services, so there is nothing to roll back to; the rollback state from the previous update was removed"
    return 0
  fi

  chmod 0644 "$state_tmp"
  mv -f "$state_tmp" "$state_file"
  : >"$marker"
  log "recorded rollback images -> $state_file"
}

# Stop before touching the stack when a recorded rollback tag is gone.
check_rollback_images_present() {
  local state_file="$1" svc img ref missing=""
  while IFS=$'\t' read -r svc img ref; do
    [[ -z "$ref" ]] && continue
    _is_rollback_ref "$ref" || continue
    docker image inspect "$ref" >/dev/null 2>&1 || missing="${missing}${missing:+, }$svc ($ref)"
  done <"$state_file"
  if [[ -n "$missing" ]]; then
    die "the previous image for: $missing is no longer on this host (removed by 'docker image prune -a' or 'docker system prune -a'?). Rollback cannot restore it, and nothing was stopped. To go back, check out the previous version and run 'make update'."
  fi
}

# Pull the recorded refs that live in a registry (state files from before LUM-984
# hold digests). A local rollback tag is never pulled.
pull_rollback_registry_refs() {
  local state_file="$1" svc img ref
  while IFS=$'\t' read -r svc img ref; do
    [[ -z "$ref" ]] && continue
    _is_rollback_ref "$ref" && continue
    docker pull "$ref" || warn "could not pull $ref (may already be local)"
  done <"$state_file"
}

# After a healthy update, remove this project's rollback tags that the current
# state does not reference. Never fails the caller; plain `rmi`, never -f, so
# Docker still refuses to remove the last name of an image a container uses.
prune_rollback_tags() {
  local state_file="$1" keep="" prefixes="" nl=$'\n' ref prefix t p drop _svc _img
  [[ -s "$state_file" ]] || return 0
  while IFS=$'\t' read -r _svc _img ref; do
    prefix="$(_rollback_ref_project "$ref")" || continue
    keep="${keep}${ref}${nl}"
    case "$nl$prefixes" in *"$nl$prefix$nl"*) ;; *) prefixes="${prefixes}${prefix}${nl}" ;; esac
  done <"$state_file"
  [[ -n "$keep" ]] || return 0
  while IFS= read -r t; do
    [[ -n "$t" ]] || continue
    drop=""
    while IFS= read -r p; do
      if [[ -n "$p" && "$t" == "$p"* ]]; then drop=1; fi
    done <<<"$prefixes"
    [[ -n "$drop" ]] || continue
    case "$nl$keep" in *"$nl$t$nl"*) continue ;; esac
    docker rmi "$t" >/dev/null 2>&1 || warn "could not remove old rollback tag $t"
  done < <(docker image ls --format '{{.Repository}}:{{.Tag}}' 2>/dev/null)
  return 0
}

# The orchestrator's health URL on this host. Asks compose for the published
# port, so ORCHESTRATOR_HOST_PORT (environment or .env) and override files are
# honoured; a hard-coded :8000 failed every update on a moved port (LUM-900).
# LUMOGIS_HEALTH_URL still wins. Call it after `docker compose up`.
orchestrator_health_url() {
  if [[ -n "${LUMOGIS_HEALTH_URL:-}" ]]; then
    printf '%s\n' "$LUMOGIS_HEALTH_URL"
    return 0
  fi
  local addr port
  addr="$(docker compose port orchestrator 8000 2>/dev/null | head -1 || true)"
  port="${addr##*:}"
  [[ "$port" =~ ^[0-9]+$ ]] || port=8000
  printf 'http://127.0.0.1:%s/healthz\n' "$port"
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
