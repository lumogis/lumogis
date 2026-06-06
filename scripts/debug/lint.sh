#!/usr/bin/env bash
# Summary-first wrapper for make lint / compose-lint (LUM-377).
set -euo pipefail
DEBUG_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/debug/_common.sh
source "${DEBUG_DIR}/_common.sh"

VERBOSE=0
for arg in "$@"; do
  [[ "$arg" == "--verbose" ]] && VERBOSE=1
done

lumogis_debug_init lint

if [[ "${LUMOGIS_DEBUG_COMPOSE:-}" == "1" ]]; then
  target=compose-lint
else
  target=lint
fi

rc="$(lumogis_debug_run_make_logged "$target")"
lumogis_debug_print_summary "$rc" "$LOG_FILE" ""
exit "$rc"
