#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
#
# scripts/test-env.sh — one entry point for running tests in a clean,
# offline, throwaway environment (LUM-894, layers 1 and 2).
#
#   scripts/test-env.sh run [--tier auto|1|2] [--base REF] [-e VAR=VAL]... -- <command...>
#   scripts/test-env.sh build [--force]   build the test-runner image if its tag is missing
#   scripts/test-env.sh tag               print the test-runner image tag for this tree
#   scripts/test-env.sh tier [--base REF] [-- <command...>]   print the tier `auto` picks, and why
#   scripts/test-env.sh gc [--apply]      list (or remove) leftover tier-2 projects
#
# Tiers
#   1  Test-runner image only (docker/test-runner/Dockerfile), `--network none`.
#      Covers `make test`, `make lint`, `make test-kg`, `make test-lumogis-mcp`,
#      `npm run codegen:check`, `make web-test`, `make web-lint` and
#      `make web-codegen web-build`.
#   2  Tier 1 plus throwaway databases (docker/test-env/compose.dbs.yml):
#      Postgres 16.8 with init.sql and every migration applied, Qdrant and
#      FalkorDB, on an internal network with no host ports and tmpfs storage.
#      The command runs with LUMOGIS_REQUIRE_INTEGRATION_DB=1, so modules that
#      use the LUM-718 guard (_pgguard.py) FAIL instead of skipping when a
#      backend is unreachable.
#   auto  2 when the command is `make test` or mentions integration tests, or
#      the diff against --base touches a path in docker/test-env/tier2-paths.txt;
#      else 1.
#
# Whatever happens to the command, the tier-2 project (containers, network,
# volumes) is removed before this script exits.
#
# The worktree is bind-mounted at its own path and the command runs as the
# calling user, so files it writes (caches, build output) belong to you.
#
# Environment
#   LUMOGIS_TEST_ENV_PREFIX   compose project prefix (default: lumogis-testenv)
#   LUMOGIS_TEST_ENV_BASE     default --base for `auto` (default: origin/dev)
#   LUMOGIS_TEST_ENV_NO_BUILD 1 = fail rather than build a missing image
#   LUMOGIS_TEST_ENV_CHANGED_FILES  newline-separated path list that replaces
#                             the git diff in tier resolution (tests use this)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

IMAGE_REPO="lumogis-test-runner"
PREFIX="${LUMOGIS_TEST_ENV_PREFIX:-lumogis-testenv}"
DEFAULT_BASE="${LUMOGIS_TEST_ENV_BASE:-origin/dev}"
COMPOSE_FILE_DBS="$ROOT/docker/test-env/compose.dbs.yml"
TIER2_PATHS_FILE="$ROOT/docker/test-env/tier2-paths.txt"
LABEL="org.lumogis.test-env"

# Every file the image is built from. The tag is a hash of exactly these, so
# editing any of them (and nothing else) produces a new image.
IMAGE_INPUTS=(
  docker/test-runner/Dockerfile
  orchestrator/requirements.txt
  orchestrator/requirements-core.txt
  orchestrator/requirements-dev.txt
  orchestrator/requirements-test.txt
  stack-control/requirements.txt
  stack-control/requirements-dev.txt
  services/lumogis-graph/requirements.txt
  services/lumogis-graph/requirements-dev.txt
  clients/lumogis-mcp/pyproject.toml
  clients/lumogis-web/package.json
  clients/lumogis-web/package-lock.json
)

# Never fails: teardown logs through this, and a closed stderr (a killed pipe
# reader, a dropped terminal) must not stop it from removing the databases.
log() { printf 'test-env: %s\n' "$*" >&2 2>/dev/null || true; }
die() { log "ERROR: $*"; exit 2; }

usage() {
  sed -n '5,13p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
  exit 2
}

# ── image ────────────────────────────────────────────────────────────────────

image_hash() {
  local f
  (
    cd "$ROOT"
    for f in "${IMAGE_INPUTS[@]}"; do
      printf '%s\n' "$f"
      sha256sum <"$f"
    done
  ) | sha256sum | cut -c1-16
}

image_tag() {
  local f
  # Checked here, not inside image_hash's subshell, where `die` would only
  # leave the subshell and a wrong tag would still be printed.
  for f in "${IMAGE_INPUTS[@]}"; do
    [[ -f "$ROOT/$f" ]] || die "image input missing: $f"
  done
  printf '%s:%s\n' "$IMAGE_REPO" "$(image_hash)"
}

image_exists() { docker image inspect "$1" >/dev/null 2>&1; }

build_image() {
  local force="${1:-0}" tag ctx f
  tag="$(image_tag)"
  if [[ "$force" != 1 ]] && image_exists "$tag"; then
    log "image $tag is present"
    return 0
  fi
  [[ "${LUMOGIS_TEST_ENV_NO_BUILD:-0}" == 1 ]] && die "image $tag is missing and LUMOGIS_TEST_ENV_NO_BUILD=1"
  mkdir -p "${XDG_CACHE_HOME:-$HOME/.cache}"
  ctx="$(mktemp -d "${XDG_CACHE_HOME:-$HOME/.cache}/lumogis-test-env-ctx.XXXXXX")"
  local rc=0
  (
    for f in "${IMAGE_INPUTS[@]}"; do
      mkdir -p "$ctx/$(dirname "$f")" && cp "$ROOT/$f" "$ctx/$f" || exit 1
    done
    log "building $tag (context: ${#IMAGE_INPUTS[@]} files; network is used for this step only)"
    DOCKER_BUILDKIT=1 docker build \
      --label "$LABEL=runner" \
      -f "$ctx/docker/test-runner/Dockerfile" \
      -t "$tag" "$ctx" >&2
  ) || rc=$?
  rm -rf "$ctx"
  [[ "$rc" == 0 ]] || die "image build failed (exit $rc)"
  log "built $tag"
}

# ── tier resolution ──────────────────────────────────────────────────────────

changed_files() {
  local base="$1" mb
  if [[ -n "${LUMOGIS_TEST_ENV_CHANGED_FILES+x}" ]]; then
    printf '%s\n' "$LUMOGIS_TEST_ENV_CHANGED_FILES"
    return 0
  fi
  if ! mb="$(git -C "$ROOT" merge-base "$base" HEAD 2>/dev/null)"; then
    return 1
  fi
  # Committed + staged + unstaged changes since the merge base, plus untracked
  # files. --no-renames lists BOTH sides of a move, so moving a file out of a
  # tier-2 path still counts. Explicit `|| return`: this runs where errexit is
  # suppressed, and a half-failed diff must not read as "no tier-2 path".
  git -C "$ROOT" diff --no-renames --name-only "$mb" || return 1
  git -C "$ROOT" ls-files --others --exclude-standard || return 1
}

# Prints "<tier> <reason>".
resolve_auto_tier() {
  local base="$1"
  shift
  local cmd="$*" files pattern hit
  if [[ "$cmd" =~ (tests/integration|test-integration|integration) ]]; then
    printf '2 command mentions integration tests\n'
    return 0
  fi
  # `make test` collects orchestrator/tests/integration too (LUM-750: deselect
  # none). At tier 1 those tests would skip, which is the silent green this
  # environment exists to end, so the full suite gets the databases, as in CI.
  if [[ "$cmd" =~ ^make([[:space:]]+-[^[:space:]]+)*[[:space:]]+test([[:space:]]|$) ]]; then
    printf '2 make test collects orchestrator/tests/integration\n'
    return 0
  fi
  if ! files="$(changed_files "$base")"; then
    printf '2 cannot diff against %s, so assuming the larger tier\n' "$base"
    return 0
  fi
  pattern="$(grep -vE '^[[:space:]]*(#|$)' "$TIER2_PATHS_FILE" | paste -sd '|' -)"
  if [[ -n "$pattern" ]] && hit="$(printf '%s\n' "$files" | grep -E -m1 "$pattern")"; then
    printf '2 diff against %s touches %s\n' "$base" "$hit"
    return 0
  fi
  printf '1 diff against %s touches no tier-2 path\n' "$base"
}

# ── run ──────────────────────────────────────────────────────────────────────

PROJECT=""
PG_PASSWORD=""
RUNNER_NAMES=()
INTERACTIVE=0

# Run a long foreground step so INT/TERM traps can fire while it runs. Bash
# defers a trap until the foreground child exits, so a `timeout` or a TERM sent
# to this script would otherwise wait out the whole test run. Backgrounding the
# child and `wait`ing on it lets the trap run at once; teardown then removes
# the runner by name. With a TTY, stay in the foreground: Ctrl-C reaches the
# docker CLI directly there, and a background job would lose the terminal.
interruptible() {
  if [[ "$INTERACTIVE" == 1 ]]; then
    "$@"
    return
  fi
  "$@" &
  wait "$!"
}

# Every compose call goes through here. compose.dbs.yml requires the password
# variable (`:?`), and compose interpolates the whole file on EVERY command,
# `down` included — so a teardown that forgot it failed and leaked the project.
dbs_compose() {
  LUMOGIS_TEST_ENV_PG_PASSWORD="${PG_PASSWORD:-teardown-only}" \
    docker compose -p "$1" -f "$COMPOSE_FILE_DBS" "${@:2}"
}

# Last resort when `compose down` fails: remove by the project label, which
# compose puts on everything it creates, scoped to our own db label as well.
remove_project_by_label() {
  local project="$1" ids
  ids="$(docker ps -aq --filter "label=com.docker.compose.project=$project" --filter "label=$LABEL=db")"
  # shellcheck disable=SC2086  # word-splitting the id list is intended
  [[ -z "$ids" ]] || docker rm -f -v $ids >/dev/null
  docker network ls -q --filter "label=com.docker.compose.project=$project" | xargs -r docker network rm >/dev/null
  docker volume ls -q --filter "label=com.docker.compose.project=$project" | xargs -r docker volume rm >/dev/null
}

teardown() {
  local status=$? name
  # Finish the cleanup whatever else goes wrong: no SIGPIPE if the reader of
  # our output died with the run, no further INT/TERM, no errexit.
  trap '' PIPE INT TERM
  set +e
  for name in "${RUNNER_NAMES[@]}"; do
    docker rm -f "$name" >/dev/null 2>&1 || true
  done
  if [[ -n "$PROJECT" ]]; then
    log "removing tier-2 project $PROJECT"
    if ! dbs_compose "$PROJECT" down -v --remove-orphans --timeout 5 >/dev/null 2>&1; then
      log "WARNING: 'compose down' failed for $PROJECT; removing by label"
      remove_project_by_label "$PROJECT" \
        || log "WARNING: $PROJECT may be left over; run: scripts/test-env.sh gc --apply"
    fi
    PROJECT=""
  fi
  return "$status"
}

# Shared node_modules for Lumogis Web, copied once per image and uid from the
# image's /opt/lumogis-web/node_modules into a named volume, then mounted over
# the worktree's own node_modules. Host files are never read or changed.
ensure_web_volume() {
  local tag="$1" vol="$2"
  # A marker written LAST, so an interrupted copy is redone, not trusted.
  local marker=".lumogis-test-env-complete"
  if docker volume inspect "$vol" >/dev/null 2>&1 \
      && docker run --rm --network none -v "$vol:/dst:ro" "$tag" test -f "/dst/$marker"; then
    return 0
  fi
  log "populating web dependency volume $vol"
  docker volume create --label "$LABEL=web-deps" --label "$LABEL.image=$tag" "$vol" >/dev/null
  if ! docker run --rm --network none -v "$vol:/dst" "$tag" \
      sh -c "find /dst -mindepth 1 -delete && cp -a /opt/lumogis-web/node_modules/. /dst/ \
             && chown -R $(id -u):$(id -g) /dst && touch /dst/$marker"; then
    docker volume rm -f "$vol" >/dev/null 2>&1 || true
    die "could not populate $vol"
  fi
}

cmd_run() {
  local tier="auto" base="$DEFAULT_BASE" extra_env=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --tier) tier="${2:?--tier needs a value}"; shift 2 ;;
      --tier=*) tier="${1#--tier=}"; shift ;;
      --base) base="${2:?--base needs a value}"; shift 2 ;;
      --base=*) base="${1#--base=}"; shift ;;
      -e|--env) extra_env+=(-e "${2:?$1 needs VAR=VAL}"); shift 2 ;;
      --) shift; break ;;
      *) die "unknown option '$1' (the command goes after --)" ;;
    esac
  done
  [[ $# -gt 0 ]] || die "no command given; usage: scripts/test-env.sh run [--tier auto|1|2] -- <command...>"

  local reason
  case "$tier" in
    1|2) reason="requested" ;;
    auto) read -r tier reason < <(resolve_auto_tier "$base" "$@") || die "could not resolve the auto tier" ;;
    3|4) die "tier $tier (app built from the branch / specialised fixtures) is not implemented yet" ;;
    *) die "unknown tier '$tier' (expected auto, 1 or 2)" ;;
  esac
  log "tier $tier ($reason)"

  command -v docker >/dev/null 2>&1 || die "docker is not on PATH"
  build_image 0
  local tag uid gid workdir git_common web_vol
  tag="$(image_tag)"
  uid="$(id -u)"
  gid="$(id -g)"
  workdir="$PWD"
  [[ "$workdir" == "$ROOT" || "$workdir" == "$ROOT"/* ]] || workdir="$ROOT"

  local args=(
    --rm --init
    --user "$uid:$gid"
    --label "$LABEL=run"
    -e HOME=/tmp
    -e LUMOGIS_TEST_ENV_TIER="$tier"
    # No index at test time: an install that would need the network fails
    # fast instead of hanging on DNS. `pip install -e` (test-lumogis-mcp)
    # still works because every dependency and the build backend are baked in.
    -e PIP_NO_INDEX=1
    -e PIP_USER=1
    -e PIP_NO_BUILD_ISOLATION=0
    -e npm_config_offline=true
    -e npm_config_cache=/tmp/.npm
    -v "$ROOT:$ROOT"
    -w "$workdir"
  )
  # A linked worktree's .git is a file pointing into the main repository's
  # .git; mount that read-only at the same path so git works inside.
  git_common="$(git -C "$ROOT" rev-parse --path-format=absolute --git-common-dir 2>/dev/null || true)"
  if [[ -n "$git_common" && "$git_common" != "$ROOT"/* ]]; then
    args+=(-v "$git_common:$git_common:ro")
  fi
  if [[ -d "$ROOT/clients/lumogis-web" ]]; then
    web_vol="lumogis-test-runner-web-${tag#*:}-$uid"
    ensure_web_volume "$tag" "$web_vol"
    # Pre-create the mount point as the caller, or Docker creates it as root.
    mkdir -p "$ROOT/clients/lumogis-web/node_modules"
    args+=(-v "$web_vol:$ROOT/clients/lumogis-web/node_modules")
  fi
  if [[ -t 0 && -t 1 ]]; then
    INTERACTIVE=1
    args+=(-it)
  fi

  trap teardown EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM

  if [[ "$tier" == 1 ]]; then
    local name="${PREFIX}-t1-$$-$RANDOM"
    RUNNER_NAMES+=("$name")
    log "running (no network): $*"
    local rc=0
    interruptible docker run --name "$name" --network none "${args[@]}" "${extra_env[@]}" "$tag" "$@" || rc=$?
    log "command exited $rc"
    return "$rc"
  fi

  # Tier 2.
  PROJECT="${PREFIX}-$(date +%s)-$$"
  local net
  PG_PASSWORD="$(od -An -N12 -tx1 /dev/urandom | tr -d ' \n')"
  local pg_password="$PG_PASSWORD"
  log "starting throwaway databases (project $PROJECT)"
  if ! interruptible dbs_compose "$PROJECT" up -d --wait --wait-timeout 180 >&2; then
    dbs_compose "$PROJECT" logs --tail 50 >&2 || true
    die "databases did not become healthy"
  fi
  net="${PROJECT}_testnet"
  local db_env=(
    -e POSTGRES_HOST=postgres
    -e POSTGRES_PORT=5432
    -e POSTGRES_USER=lumogis
    -e POSTGRES_PASSWORD="$pg_password"
    -e POSTGRES_DB=lumogis
    -e LUMOGIS_INTEGRATION_POSTGRES_DSN="postgresql://lumogis:${pg_password}@postgres:5432/lumogis"
    -e QDRANT_URL=http://qdrant:6333
    -e FALKORDB_URL=redis://falkordb:6379
    -e LUMOGIS_REQUIRE_INTEGRATION_DB=1
    -e LUMOGIS_MIGRATIONS_DIR="$ROOT/postgres/migrations"
  )
  local prep="${PROJECT}-prepare"
  RUNNER_NAMES+=("$prep")
  log "waiting for backends and applying migrations"
  interruptible docker run --name "$prep" --network "$net" "${args[@]}" "${db_env[@]}" "$tag" \
    python3 "$ROOT/docker/test-env/prepare_dbs.py" --orchestrator "$ROOT/orchestrator" \
    || die "database preparation failed"
  local name="${PROJECT}-runner"
  RUNNER_NAMES+=("$name")
  log "running (internal network, databases only): $*"
  local rc=0
  interruptible docker run --name "$name" --network "$net" "${args[@]}" "${db_env[@]}" "${extra_env[@]}" "$tag" "$@" || rc=$?
  log "command exited $rc"
  return "$rc"
}

cmd_tier() {
  local base="$DEFAULT_BASE"
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --base) base="${2:?--base needs a value}"; shift 2 ;;
      --) shift; break ;;
      *) die "unknown option '$1'" ;;
    esac
  done
  resolve_auto_tier "$base" "$@"
}

cmd_gc() {
  local apply=0 name running
  [[ "${1:-}" == "--apply" ]] && apply=1
  local found=0
  while read -r name; do
    [[ -n "$name" ]] || continue
    found=1
    running="$(docker ps -q --filter "name=^${name}-(runner|prepare)$")"
    if [[ -n "$running" ]]; then
      log "$name: a run is still in progress, leaving it"
      continue
    fi
    if [[ "$apply" == 1 ]]; then
      log "$name: removing"
      dbs_compose "$name" down -v --remove-orphans --timeout 5 >&2 || remove_project_by_label "$name"
    else
      log "$name: leftover (re-run with --apply to remove)"
    fi
  done < <(docker ps -a --filter "label=$LABEL=db" \
             --format '{{.Label "com.docker.compose.project"}}' | grep -E "^${PREFIX}-" | sort -u)
  [[ "$found" == 1 ]] || log "no leftover tier-2 projects with prefix $PREFIX"
}

main() {
  [[ $# -gt 0 ]] || usage
  local sub="$1"
  shift
  case "$sub" in
    run) cmd_run "$@" ;;
    build) build_image "$([[ "${1:-}" == --force ]] && echo 1 || echo 0)" ;;
    tag) image_tag ;;
    tier) cmd_tier "$@" ;;
    gc) cmd_gc "$@" ;;
    -h|--help|help) usage ;;
    *) die "unknown subcommand '$sub'" ;;
  esac
}

main "$@"
