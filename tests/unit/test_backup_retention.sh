#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# LUM-485 — retention prune: 7 daily + 4 ISO weekly; failed/orphan dirs preserved.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
# shellcheck source=scripts/backup/lib/common.sh
source "${REPO_ROOT}/scripts/backup/lib/common.sh"

failures=0

assert_eq() {
  local label="$1"
  local expected="$2"
  local actual="$3"
  if [[ "$expected" != "$actual" ]]; then
    echo "FAIL: $label (expected '$expected', got '$actual')" >&2
    failures=$((failures + 1))
  fi
}

assert_contains() {
  local label="$1"
  local needle="$2"
  local haystack="$3"
  if ! grep -qxF "$needle" <<<"$haystack"; then
    echo "FAIL: $label (missing '$needle')" >&2
    failures=$((failures + 1))
  fi
}

assert_not_contains() {
  local label="$1"
  local needle="$2"
  local haystack="$3"
  if grep -qxF "$needle" <<<"$haystack"; then
    echo "FAIL: $label (unexpected '$needle')" >&2
    failures=$((failures + 1))
  fi
}

test_retention_keeps_seven_daily_and_four_weekly() {
  local -a ids=(
    20250223-120000
    20250222-120000
    20250221-120000
    20250220-120000
    20250219-120000
    20250218-120000
    20250217-120000
    20250216-120000
    20250203-120000
    20250127-120000
    20250120-120000
    20250113-120000
    20250106-120000
    20241230-120000
    20241223-120000
  )
  local protected
  protected="$(compute_protected_snapshot_ids 7 4 "${ids[@]}")"
  local count
  count="$(printf '%s\n' "$protected" | sed '/^$/d' | wc -l)"
  assert_eq "protected count" "11" "$count"
  assert_contains "daily newest" "20250223-120000" "$protected"
  assert_contains "daily oldest of seven" "20250217-120000" "$protected"
  assert_contains "weekly pick (week before cutoff)" "20250216-120000" "$protected"
  assert_contains "weekly pick" "20250203-120000" "$protected"
  assert_contains "weekly pick" "20250127-120000" "$protected"
  assert_contains "weekly pick" "20250120-120000" "$protected"
  assert_not_contains "pruned older week" "20250113-120000" "$protected"
  assert_not_contains "pruned old week" "20250106-120000" "$protected"
  assert_not_contains "pruned extra old" "20241230-120000" "$protected"
  assert_not_contains "pruned extra old" "20241223-120000" "$protected"
}

write_manifest() {
  local dir="$1"
  local status="$2"
  mkdir -p "$dir"
  printf '{"verify_status":"%s"}\n' "$status" >"${dir}/manifest.json"
}

test_prune_preserves_failed_and_orphan_dirs() {
  local root
  root="$(mktemp -d)"
  trap 'rm -rf "$root"' RETURN
  export BACKUP_DIR="$root"
  local snap_root
  snap_root="$(snapshots_dir)"
  mkdir -p "$snap_root"

  write_manifest "${snap_root}/20250223-120000" ok
  write_manifest "${snap_root}/20250222-120000" ok
  write_manifest "${snap_root}/20250221-120000" ok
  write_manifest "${snap_root}/20250220-120000" ok
  write_manifest "${snap_root}/20250219-120000" ok
  write_manifest "${snap_root}/20250218-120000" ok
  write_manifest "${snap_root}/20250217-120000" ok
  write_manifest "${snap_root}/20250216-120000" ok
  write_manifest "${snap_root}/20250203-120000" ok
  write_manifest "${snap_root}/20250127-120000" ok
  write_manifest "${snap_root}/20250120-120000" ok
  write_manifest "${snap_root}/20250113-120000" ok
  write_manifest "${snap_root}/20250106-120000" ok
  write_manifest "${snap_root}/20241230-120000" ok
  write_manifest "${snap_root}/20241223-120000" ok
  write_manifest "${snap_root}/20241220-999999" failed
  mkdir -p "${snap_root}/20241219-orphan"

  prune_snapshots

  [[ -d "${snap_root}/20241220-999999" ]] || {
    echo "FAIL: failed snapshot dir was removed" >&2
    failures=$((failures + 1))
  }
  [[ -d "${snap_root}/20241219-orphan" ]] || {
    echo "FAIL: orphan snapshot dir was removed" >&2
    failures=$((failures + 1))
  }
  [[ ! -d "${snap_root}/20241223-120000" ]] || {
    echo "FAIL: unprotected successful snapshot was retained" >&2
    failures=$((failures + 1))
  }
  [[ -d "${snap_root}/20250217-120000" ]] || {
    echo "FAIL: protected daily snapshot was removed" >&2
    failures=$((failures + 1))
  }
}

test_retention_keeps_seven_daily_and_four_weekly
test_prune_preserves_failed_and_orphan_dirs

if (( failures > 0 )); then
  echo "test_backup_retention: $failures failure(s)" >&2
  exit 1
fi

echo "test_backup_retention: OK"
