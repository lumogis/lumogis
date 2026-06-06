#!/usr/bin/env bash
# Heavy-gated wrappers for integration / RC targets (LUM-377).
set -euo pipefail
DEBUG_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/debug/_common.sh
source "${DEBUG_DIR}/_common.sh"

SUBCMD=""
for arg in "$@"; do
  case "$arg" in
    --verbose | --heavy) ;;
    integration | integration-full | rc | restart-e2e | graph-parity | m1-compat)
      SUBCMD="$arg"
      ;;
    *)
      echo "lumogis debug integration: unknown argument: $arg" >&2
      exit 2
      ;;
  esac
done

if [[ -z "$SUBCMD" ]]; then
  echo "usage: integration.sh {integration|integration-full|rc|restart-e2e|graph-parity|m1-compat} [--heavy]" >&2
  exit 2
fi

lumogis_debug_require_heavy "$@"
lumogis_debug_init "integration-${SUBCMD}"

case "$SUBCMD" in
  integration)
    rc="$(lumogis_debug_run_make_logged test-integration)"
    ;;
  integration-full)
    rc="$(lumogis_debug_run_make_logged test-integration-full)"
    ;;
  rc)
    rc="$(lumogis_debug_run_cmd_logged "${REPO_ROOT}/scripts/integration-public-rc.sh" full-cycle)"
    ;;
  restart-e2e)
    rc="$(lumogis_debug_run_make_logged e2e-ingest-restart)"
    ;;
  graph-parity)
    rc="$(lumogis_debug_run_make_logged test-graph-parity)"
    ;;
  m1-compat)
    rc="$(lumogis_debug_run_make_logged m1-compat-with-retry)"
    ;;
esac

lumogis_debug_print_summary "$rc" "$LOG_FILE" ""
exit "$rc"
