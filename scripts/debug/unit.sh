#!/usr/bin/env bash
# Summary-first wrapper for make test / compose-test (LUM-377).
set -euo pipefail
DEBUG_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/debug/_common.sh
source "${DEBUG_DIR}/_common.sh"

VERBOSE=0
for arg in "$@"; do
  [[ "$arg" == "--verbose" ]] && VERBOSE=1
done

lumogis_debug_init unit

if ! python3 -c "import pytest" 2>/dev/null; then
  echo "pytest not available. See CONTRIBUTING.md — Running tests (local venv)." >&2
  exit 2
fi

if [[ "${LUMOGIS_DEBUG_COMPOSE:-}" == "1" ]]; then
  TARGETS=(compose-test compose-test-stack-control)
else
  TARGETS=(test)
fi

export AUTH_ENABLED=false
unset PYTEST_ADDOPTS
if [[ "$VERBOSE" -eq 0 ]] && lumogis_debug_agent_digest_supported; then
  export PYTEST_ADDOPTS="--agent-digest=file --agent-digest-file=${DIGEST_FILE}"
elif [[ "$VERBOSE" -eq 0 ]]; then
  echo "WARN: pytest-agent-digest unavailable; using log tail for summary" >&2
  DIGEST_FILE=""
fi

rc=0
for target in "${TARGETS[@]}"; do
  trc="$(lumogis_debug_run_make_logged "$target")"
  if [[ "$trc" -ne 0 ]]; then
    rc="$trc"
  fi
done

lumogis_debug_print_summary "$rc" "$LOG_FILE" "${DIGEST_FILE:-}"
exit "$rc"
