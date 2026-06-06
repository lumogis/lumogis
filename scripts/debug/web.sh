#!/usr/bin/env bash
# Summary-first wrapper for web make targets (LUM-377).
set -euo pipefail
DEBUG_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/debug/_common.sh
source "${DEBUG_DIR}/_common.sh"

VERBOSE=0
SUBCMD=""
for arg in "$@"; do
  case "$arg" in
    --verbose) VERBOSE=1 ;;
    --heavy) ;;
    unit | lint | e2e) SUBCMD="$arg" ;;
    *)
      echo "lumogis debug web: unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

if [[ -z "$SUBCMD" ]]; then
  echo "usage: web.sh {unit|lint|e2e} [--verbose] [--heavy]" >&2
  exit 2
fi

lumogis_debug_init "web-${SUBCMD}"

case "$SUBCMD" in
  unit)
    make_target=web-test
    ;;
  lint)
    make_target=web-lint
    ;;
  e2e)
    lumogis_debug_require_heavy "$@"
    make_target=web-e2e
    ;;
esac

rc="$(lumogis_debug_run_make_logged "$make_target")"
lumogis_debug_print_summary "$rc" "$LOG_FILE" ""
exit "$rc"
