#!/usr/bin/env bash
# List or tail debug runner logs (LUM-377).
set -euo pipefail
DEBUG_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/debug/_common.sh
source "${DEBUG_DIR}/_common.sh"

lumogis_debug_validate_log_dir

SUB="${1:-}"
LINES="${2:-400}"

if [[ "$SUB" == "last" ]]; then
  mapfile -t logs < <(ls -1t "${LOG_DIR}"/*.log 2>/dev/null || true)
  if ((${#logs[@]} == 0)); then
    echo "lumogis debug: no log files in ${LOG_DIR}" >&2
    exit 1
  fi
  tail -n "$LINES" "${logs[0]}"
  exit 0
fi

mapfile -t logs < <(ls -1t "${LOG_DIR}"/*.log 2>/dev/null || true)
if ((${#logs[@]} == 0)); then
  echo "No logs in ${LOG_DIR}"
  exit 0
fi
printf '%s\n' "${logs[@]}"
