#!/usr/bin/env bash
# LUM-486 — contract tests for backup-integration-paths.sh (synthetic git repos).
# shellcheck disable=SC2030,SC2031
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT_SRC="$REPO/.github/scripts/backup-integration-paths.sh"

fail() {
  printf 'test-backup-integration-paths: FAIL: %s\n' "$*" >&2
  exit 1
}

run_pr_case() {
  local expect="$1"
  shift
  local tmp out got
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' RETURN
  cp "$SCRIPT_SRC" "$tmp/gate.sh"
  cd "$tmp"
  git init -q
  git config user.email "lum486-test@example.invalid"
  git config user.name "lum486-test"
  echo base > README.md
  git add README.md
  git commit -q -m base
  local base_sha
  base_sha="$(git rev-parse HEAD)"
  for path in "$@"; do
    mkdir -p "$(dirname "$path")"
    echo x >"$path"
  done
  git add -A
  git commit -q -m head
  local head_sha
  head_sha="$(git rev-parse HEAD)"
  out="$(mktemp)"
  (
    export GITHUB_OUTPUT="$out"
    export GITHUB_EVENT_NAME=pull_request
    export BASE_SHA="$base_sha"
    export HEAD_SHA="$head_sha"
    bash gate.sh
  )
  got="$(grep -E '^should_run=' "$out" | tail -n1)"
  [[ "$got" == "should_run=$expect" ]] || fail "case paths=$* expected should_run=$expect, got ${got:-<empty>}"
}

echo "test-backup-integration-paths: scripts/backup/verify.sh -> true"
run_pr_case true scripts/backup/verify.sh

echo "test-backup-integration-paths: docs/foo.md -> false"
run_pr_case false docs/foo.md

echo "test-backup-integration-paths: docker-compose.falkordb.yml -> true"
run_pr_case true docker-compose.falkordb.yml

echo "test-backup-integration-paths: push event -> true"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' RETURN
cp "$SCRIPT_SRC" "$tmp/gate.sh"
cd "$tmp"
out="$(mktemp)"
(
  export GITHUB_OUTPUT="$out"
  export GITHUB_EVENT_NAME=push
  bash gate.sh
)
got="$(grep -E '^should_run=' "$out" | tail -n1)"
[[ "$got" == "should_run=true" ]] || fail "push expected should_run=true, got ${got:-<empty>}"

echo "test-backup-integration-paths: OK"
