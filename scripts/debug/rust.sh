#!/usr/bin/env bash
# Summary-first wrapper for lumogis-search cargo test (LUM-377 / LUM-430).
set -euo pipefail
DEBUG_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/debug/_common.sh
source "${DEBUG_DIR}/_common.sh"

lumogis_debug_init rust

SEARCH="${REPO_ROOT}/clients/lumogis-search/src-tauri"
if [[ ! -d "$SEARCH" ]]; then
  echo "SKIP: no lumogis-search crate in tree"
  exit 0
fi
if ! command -v cargo >/dev/null 2>&1; then
  echo "SKIP: cargo not on PATH"
  exit 0
fi

set +e
(
  cd "$SEARCH"
  cargo test
) 2>&1 | tee -a "$LOG_FILE"
rc=${PIPESTATUS[0]}
set -e

lumogis_debug_print_summary "$rc" "$LOG_FILE" ""
exit "$rc"
