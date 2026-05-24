#!/usr/bin/env bash
# Lumogis doctor entrypoint (LUM-199). See scripts/doctor/README.md
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -n "${LUMOGIS_DOCTOR_REPO_ROOT:-}" ]; then
  REPO="$(cd "${LUMOGIS_DOCTOR_REPO_ROOT}" && pwd)"
else
  REPO="$(cd "$SCRIPT_DIR/../.." && pwd)"
fi

DOCTOR_JSON=0
DOCTOR_SECURITY=0
for arg in "$@"; do
  case "$arg" in
    --json) DOCTOR_JSON=1 ;;
    --security) DOCTOR_SECURITY=1 ;;
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

DOCTOR_STREAM="$(mktemp)"
DOCTOR_PS_CACHE="$(mktemp)"
DOCTOR_PS_ERR="$(mktemp)"
DOCTOR_CONFIG_CACHE="$(mktemp)"
DOCTOR_CONFIG_ERR="$(mktemp)"
DOCTOR_EXIT_FILE="$(mktemp)"

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

bash "$SCRIPT_DIR/checks/config.sh" >>"$DOCTOR_STREAM"
bash "$SCRIPT_DIR/checks/services.sh" >>"$DOCTOR_STREAM"
bash "$SCRIPT_DIR/checks/models.sh" >>"$DOCTOR_STREAM"
bash "$SCRIPT_DIR/checks/storage.sh" >>"$DOCTOR_STREAM"
bash "$SCRIPT_DIR/checks/network.sh" >>"$DOCTOR_STREAM"
bash "$SCRIPT_DIR/checks/security.sh" >>"$DOCTOR_STREAM"

export DOCTOR_EXIT_FILE
set +e
bash "$SCRIPT_DIR/format.sh" "$MODE" "$DOCTOR_STREAM"
fmt_ec=$?
set -e
if [ "$fmt_ec" != "0" ]; then
  echo "DOCTOR_FATAL: formatter script failed" >&2
  rm -f "$DOCTOR_STREAM" "$DOCTOR_PS_CACHE" "$DOCTOR_PS_ERR" "$DOCTOR_CONFIG_CACHE" "$DOCTOR_CONFIG_ERR" "$DOCTOR_EXIT_FILE"
  exit 3
fi

final_ec="$(cat "$DOCTOR_EXIT_FILE")"
rm -f "$DOCTOR_STREAM" "$DOCTOR_PS_CACHE" "$DOCTOR_PS_ERR" "$DOCTOR_CONFIG_CACHE" "$DOCTOR_CONFIG_ERR" "$DOCTOR_EXIT_FILE"
exit "${final_ec:-3}"
