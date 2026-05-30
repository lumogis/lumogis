#!/usr/bin/env bash
# Lumogis doctor entrypoint (LUM-199, LUM-320). See scripts/doctor/README.md
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -n "${LUMOGIS_DOCTOR_REPO_ROOT:-}" ]; then
  REPO="$(cd "${LUMOGIS_DOCTOR_REPO_ROOT}" && pwd)"
else
  REPO="$(cd "$SCRIPT_DIR/../.." && pwd)"
fi

DOCTOR_JSON=0
DOCTOR_SECURITY=0
DOCTOR_FIX=0
DOCTOR_APPLY=0
DOCTOR_YES=0
DOCTOR_DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --json) DOCTOR_JSON=1 ;;
    --security) DOCTOR_SECURITY=1 ;;
    --fix) DOCTOR_FIX=1 ;;
    --apply) DOCTOR_APPLY=1 ;;
    --yes) DOCTOR_YES=1 ;;
    --dry-run) DOCTOR_DRY_RUN=1 ;;
  esac
done
if [ "${LUMOGIS_DOCTOR_RUN_SECURITY:-}" = "1" ]; then
  DOCTOR_SECURITY=1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "DOCTOR_FATAL: docker not found in PATH" >&2
  exit 3
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "DOCTOR_FATAL: docker compose not available" >&2
  exit 3
fi

if [ "$DOCTOR_JSON" = "1" ] && ! command -v jq >/dev/null 2>&1; then
  echo "DOCTOR_FATAL: jq not found (required for --json)" >&2
  exit 3
fi

if [ ! -f "$REPO/docker-compose.yml" ]; then
  echo "DOCTOR_FATAL: not a Lumogis checkout (missing docker-compose.yml at repo root)" >&2
  exit 3
fi

export LUMOGIS_REPO_ROOT="$REPO"
export DOCTOR_JSON="$DOCTOR_JSON"
export DOCTOR_SECURITY="$DOCTOR_SECURITY"
export DOCTOR_FIX="$DOCTOR_FIX"

# Snapshot compose identity for checks + repair (LUM-320).
export LUMOGIS_DOCTOR_COMPOSE_FILE="${COMPOSE_FILE-}"
export LUMOGIS_DOCTOR_COMPOSE_PROJECT_NAME="${COMPOSE_PROJECT_NAME-}"
export LUMOGIS_DOCTOR_COMPOSE_PROFILES="${COMPOSE_PROFILES-}"
if [ -n "${LUMOGIS_DOCTOR_COMPOSE_FILE}" ]; then
  export COMPOSE_FILE="${LUMOGIS_DOCTOR_COMPOSE_FILE}"
else
  unset COMPOSE_FILE 2>/dev/null || true
fi
if [ -n "${LUMOGIS_DOCTOR_COMPOSE_PROJECT_NAME}" ]; then
  export COMPOSE_PROJECT_NAME="${LUMOGIS_DOCTOR_COMPOSE_PROJECT_NAME}"
else
  unset COMPOSE_PROJECT_NAME 2>/dev/null || true
fi
if [ -n "${LUMOGIS_DOCTOR_COMPOSE_PROFILES}" ]; then
  export COMPOSE_PROFILES="${LUMOGIS_DOCTOR_COMPOSE_PROFILES}"
else
  unset COMPOSE_PROFILES 2>/dev/null || true
fi

CLEANUP_FILES=()
cleanup() {
  set +e
  local f
  for f in "${CLEANUP_FILES[@]}"; do
    [ -n "$f" ] && rm -f "$f"
  done
}
trap cleanup EXIT INT TERM HUP

DOCTOR_STREAM1="$(mktemp)"
CLEANUP_FILES+=("$DOCTOR_STREAM1")
DOCTOR_PS_CACHE="$(mktemp)"
DOCTOR_PS_ERR="$(mktemp)"
DOCTOR_CONFIG_CACHE="$(mktemp)"
DOCTOR_CONFIG_ERR="$(mktemp)"
DOCTOR_EXIT_FILE="$(mktemp)"
CLEANUP_FILES+=("$DOCTOR_PS_CACHE" "$DOCTOR_PS_ERR" "$DOCTOR_CONFIG_CACHE" "$DOCTOR_CONFIG_ERR" "$DOCTOR_EXIT_FILE")

cd "$REPO"

set +e
timeout 30s docker compose ps --format json >"$DOCTOR_PS_CACHE" 2>"$DOCTOR_PS_ERR"
export DOCTOR_PS_EC=$?
timeout 30s docker compose config --format json >"$DOCTOR_CONFIG_CACHE" 2>"$DOCTOR_CONFIG_ERR"
export DOCTOR_CONFIG_EC=$?
set -e

export DOCTOR_PS_CACHE DOCTOR_CONFIG_CACHE

MODE="human"
if [ "$DOCTOR_JSON" = "1" ]; then
  MODE="json"
fi

run_checks() {
  local out="$1"
  : >"$out"
  bash "$SCRIPT_DIR/checks/config.sh" >>"$out"
  bash "$SCRIPT_DIR/checks/services.sh" >>"$out"
  bash "$SCRIPT_DIR/checks/models.sh" >>"$out"
  bash "$SCRIPT_DIR/checks/storage.sh" >>"$out"
  bash "$SCRIPT_DIR/checks/network.sh" >>"$out"
  bash "$SCRIPT_DIR/checks/security.sh" >>"$out"
}

run_checks "$DOCTOR_STREAM1"

DOCTOR_REPAIR_RESULT_PATH=""
DOCTOR_ARGV_WANTS_APPLY=0
DOCTOR_APPLY_MUTATIONS=0

if [ "$DOCTOR_FIX" = 1 ]; then
  APPLY_REQUESTED=0
  if [ "$DOCTOR_APPLY" = 1 ]; then
    if [ "$DOCTOR_DRY_RUN" = 1 ]; then
      echo "DOCTOR_WARN: --dry-run overrides --apply" >&2
      APPLY_REQUESTED=0
    else
      APPLY_REQUESTED=1
    fi
  fi

  if [ "$APPLY_REQUESTED" = 1 ] && [ "$DOCTOR_SECURITY" = 1 ]; then
    echo "DOCTOR_REFUSED: security audit mode cannot apply repairs" >&2
    cleanup
    trap - EXIT INT TERM HUP
    exit 4
  fi

  if [ "$APPLY_REQUESTED" = 1 ] && [ "$DOCTOR_YES" != "1" ]; then
    if [ ! -t 0 ] || [ ! -t 2 ]; then
      echo "DOCTOR_REFUSED: --fix --apply requires interactive TTY on stdin+stderr or pass --yes" >&2
      cleanup
      trap - EXIT INT TERM HUP
      exit 4
    fi
  fi

  if [ "$APPLY_REQUESTED" = 1 ]; then
    DOCTOR_ARGV_WANTS_APPLY=1
    DOCTOR_APPLY_MUTATIONS=1
  fi

  DOCTOR_REPAIR_RESULT_PATH="$(mktemp)"
  CLEANUP_FILES+=("$DOCTOR_REPAIR_RESULT_PATH")

  export DOCTOR_STREAM_PATH="$DOCTOR_STREAM1"
  export DOCTOR_REPAIR_RESULT_PATH
  export DOCTOR_APPLY_MUTATIONS
  export DOCTOR_YES="$DOCTOR_YES"
  export DOCTOR_ARGV_WANTS_APPLY
  DOCTOR_FULL_ARGV_JSON="$(python3 -c 'import json,sys; print(json.dumps(sys.argv[1:]))' "$@")"
  export DOCTOR_FULL_ARGV_JSON

  set +e
  bash "$SCRIPT_DIR/repair.sh"
  repair_ec=$?
  set -e
  if [ "$repair_ec" = 4 ]; then
    cleanup
    trap - EXIT INT TERM HUP
    exit 4
  fi
  if [ "$repair_ec" != "0" ]; then
    echo "DOCTOR_FATAL: repair stage failed (exit $repair_ec)" >&2
    cleanup
    trap - EXIT INT TERM HUP
    exit 3
  fi
  if [ ! -s "$DOCTOR_REPAIR_RESULT_PATH" ]; then
    echo "DOCTOR_FATAL: repair stage produced no result contract" >&2
    cleanup
    trap - EXIT INT TERM HUP
    exit 3
  fi

  rm -f "$DOCTOR_PS_CACHE" "$DOCTOR_CONFIG_CACHE"
  DOCTOR_PS_CACHE="$(mktemp)"
  DOCTOR_CONFIG_CACHE="$(mktemp)"
  CLEANUP_FILES+=("$DOCTOR_PS_CACHE" "$DOCTOR_CONFIG_CACHE")

  set +e
  timeout 30s docker compose ps --format json >"$DOCTOR_PS_CACHE" 2>"$DOCTOR_PS_ERR"
  export DOCTOR_PS_EC=$?
  timeout 30s docker compose config --format json >"$DOCTOR_CONFIG_CACHE" 2>"$DOCTOR_CONFIG_ERR"
  export DOCTOR_CONFIG_EC=$?
  set -e

  DOCTOR_STREAM2="$(mktemp)"
  CLEANUP_FILES+=("$DOCTOR_STREAM2")
  run_checks "$DOCTOR_STREAM2"
  FINAL_STREAM="$DOCTOR_STREAM2"
else
  FINAL_STREAM="$DOCTOR_STREAM1"
fi

export DOCTOR_EXIT_FILE
export DOCTOR_REPAIR_RESULT_PATH
export DOCTOR_ARGV_WANTS_APPLY
if [ "$DOCTOR_FIX" = 1 ] && [ -n "${DOCTOR_REPAIR_RESULT_PATH:-}" ]; then
  export DOCTOR_REPAIRS_JSON="$DOCTOR_REPAIR_RESULT_PATH"
else
  unset DOCTOR_REPAIRS_JSON 2>/dev/null || true
fi

set +e
bash "$SCRIPT_DIR/format.sh" "$MODE" "$FINAL_STREAM"
fmt_ec=$?
set -e
if [ "$fmt_ec" != "0" ]; then
  echo "DOCTOR_FATAL: formatter script failed" >&2
  cleanup
  trap - EXIT INT TERM HUP
  exit 3
fi

final_ec="$(cat "$DOCTOR_EXIT_FILE")"
cleanup
trap - EXIT INT TERM HUP
exit "${final_ec:-3}"
