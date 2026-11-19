#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Regression (LUM-999): restore must refuse while FalkorDB runs, when the snapshot
# carries a FalkorDB dump that restore would copy into the data directory. A running
# engine saves its in-memory graph over the restored dump.rdb when it stops.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=scripts/backup/lib/common.sh
source "${REPO_ROOT}/scripts/backup/lib/common.sh"

failures=0
fail() {
  echo "FAIL: $*" >&2
  failures=$((failures + 1))
}

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
mkdir -p "$work/bin" "$work/data"
export PATH="$work/bin:$PATH"

mock_redis() {
  if [[ "$1" == "up" ]]; then
    printf '#!/usr/bin/env bash\necho PONG\n' >"$work/bin/redis-cli"
  else
    printf '#!/usr/bin/env bash\necho "Could not connect to Redis" >&2\nexit 1\n' >"$work/bin/redis-cli"
  fi
  chmod +x "$work/bin/redis-cli"
}

manifest() {
  printf '%s\n' "$1" >"$work/manifest.json"
  echo "$work/manifest.json"
}

with_dump='{"stores":{"falkordb":{"file":"falkordb/dump.rdb","skipped":false}}}'
skipped='{"stores":{"falkordb":{"file":"falkordb/dump.rdb","skipped":true}}}'
no_falkor='{"stores":{}}'

check() {
  local want="$1" desc="$2" m="$3"
  if restore_falkordb_violation "$m" 2>/dev/null; then got=refuse; else got=allow; fi
  [[ "$got" == "$want" ]] || fail "$desc: want $want, got $got"
}

export FALKORDB_DATA_DIR="$work/data"
mock_redis up
check refuse "running FalkorDB, dump in snapshot, data dir mounted" "$(manifest "$with_dump")"
mock_redis down
check allow "FalkorDB stopped" "$(manifest "$with_dump")"
mock_redis up
check allow "snapshot skipped FalkorDB" "$(manifest "$skipped")"
check allow "snapshot has no FalkorDB entry" "$(manifest "$no_falkor")"
if [[ ! -d /falkordb-data ]]; then
  FALKORDB_DATA_DIR="$work/absent" check allow "no data dir mounted (GRAPH import fallback needs FalkorDB up)" "$(manifest "$with_dump")"
fi

msg="$(restore_falkordb_violation "$(manifest "$with_dump")" 2>&1 || true)"
[[ "$msg" == *"stop falkordb before restore"* ]] || fail "refusal message names the fix: $msg"

if (( failures > 0 )); then
  echo "test_backup_restore_falkordb_quiesce: $failures failure(s)" >&2
  exit 1
fi
echo "test_backup_restore_falkordb_quiesce: ok"
