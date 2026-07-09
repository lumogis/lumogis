#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Regression: FalkorDB BGSAVE must not copy a pre-existing dump.rdb before LASTSAVE advances.
set -euo pipefail

export FALKORDB_BGSAVE_WAIT_SECONDS=2

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=scripts/backup/lib/common.sh
source "${REPO_ROOT}/scripts/backup/lib/common.sh"

failures=0

fail() {
  echo "FAIL: $*" >&2
  failures=$((failures + 1))
}

mock_bin="$(mktemp -d)"
state_file="$(mktemp)"
trap 'rm -rf "$mock_bin" "$state_file"' EXIT

write_mock_redis_cli() {
  local first_lastsave="$1"
  local second_lastsave="$2"
  cat >"${mock_bin}/redis-cli" <<EOF
#!/usr/bin/env bash
state_file="${state_file}"
cmd="\${@: -1}"
case "\$cmd" in
  LASTSAVE)
    n=\$(cat "\$state_file" 2>/dev/null || echo 0)
    n=\$((n + 1))
    echo "\$n" >"\$state_file"
    if (( n < 3 )); then
      echo "${first_lastsave}"
    else
      echo "${second_lastsave}"
    fi
    ;;
  BGSAVE) exit 0 ;;
  INFO) echo "redis_version:7.4.0" ;;
  *) exit 1 ;;
esac
EOF
  chmod +x "${mock_bin}/redis-cli"
}

test_rejects_stale_dump_before_lastsave_advances() {
  local data_dir dest
  data_dir="$(mktemp -d)"
  dest="$(mktemp -u)"
  mkdir -p "${data_dir}"
  echo stale >"${data_dir}/dump.rdb"

  : >"$state_file"
  write_mock_redis_cli 100 100
  export FALKORDB_DATA_DIR="$data_dir"
  export PATH="${mock_bin}:${PATH}"

  if falkordb_wait_for_bgsave_dump "$dest" 2>/dev/null; then
    fail "expected timeout when LASTSAVE never advances"
  fi
  if [[ -f "$dest" ]]; then
    fail "must not copy stale dump.rdb before LASTSAVE advances"
  fi
}

test_copies_dump_after_lastsave_advances() {
  local data_dir dest
  data_dir="$(mktemp -d)"
  dest="$(mktemp -u)"
  mkdir -p "${data_dir}"
  echo fresh >"${data_dir}/dump.rdb"

  : >"$state_file"
  write_mock_redis_cli 100 101
  export FALKORDB_DATA_DIR="$data_dir"
  export PATH="${mock_bin}:${PATH}"

  if ! falkordb_wait_for_bgsave_dump "$dest" 2>/dev/null; then
    fail "expected success once LASTSAVE advances"
  fi
  if ! cmp -s "${data_dir}/dump.rdb" "$dest"; then
    fail "expected fresh dump.rdb copied to destination"
  fi
}

test_restore_quiesce_detects_healthz() {
  local mock_curl
  mock_curl="$(mktemp -d)"
  cat >"${mock_curl}/curl" <<'EOF'
#!/usr/bin/env bash
if [[ "$*" == *"/healthz"* ]]; then
  exit 0
fi
exit 7
EOF
  chmod +x "${mock_curl}/curl"
  export PATH="${mock_curl}:${PATH}"

  if ! restore_quiesce_violation >/dev/null 2>&1; then
    fail "expected quiesce violation when healthz responds"
  fi
}

test_rejects_stale_dump_before_lastsave_advances
test_copies_dump_after_lastsave_advances
test_restore_quiesce_detects_healthz

if (( failures > 0 )); then
  echo "test_backup_bgsave_wait: $failures failure(s)" >&2
  exit 1
fi

echo "test_backup_bgsave_wait: OK"
