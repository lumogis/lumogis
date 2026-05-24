#!/usr/bin/env bash
# LUM-190 — contract tests for security-audit-paths.sh (synthetic git repos).
set -euo pipefail

REPO="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT_SRC="$REPO/.github/scripts/security-audit-paths.sh"

fail() {
  printf 'test-security-audit-paths: FAIL: %s\n' "$*" >&2
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
  git config user.email "lum190-test@example.invalid"
  git config user.name "lum190-test"
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

echo "test-security-audit-paths: orchestrator/foo.py -> true"
run_pr_case true orchestrator/foo.py

echo "test-security-audit-paths: docs/foo.md -> false"
run_pr_case false docs/foo.md

echo "test-security-audit-paths: orchestrator/requirements.txt -> true"
run_pr_case true orchestrator/requirements.txt

echo "test-security-audit-paths: services/lumogis-graph/foo.py -> true"
run_pr_case true services/lumogis-graph/foo.py

echo "test-security-audit-paths: unsupported event -> non-zero"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' RETURN
cp "$SCRIPT_SRC" "$tmp/gate.sh"
cd "$tmp"
git init -q
git config user.email "lum190-test@example.invalid"
git config user.name "lum190-test"
echo a > f && git add f && git commit -q -m a
b="$(git rev-parse HEAD)"
echo b > f && git commit -q -am b
h="$(git rev-parse HEAD)"
out="$(mktemp)"
set +e
(
  export GITHUB_OUTPUT="$out"
  export GITHUB_EVENT_NAME=workflow_dispatch
  export BASE_SHA="$b"
  export HEAD_SHA="$h"
  bash gate.sh
) >/dev/null 2>&1
ec=$?
set -e
[[ "$ec" -ne 0 ]] || fail "expected workflow_dispatch to exit non-zero"

echo "test-security-audit-paths: missing BASE_SHA on PR -> non-zero"
out="$(mktemp)"
set +e
(
  export GITHUB_OUTPUT="$out"
  export GITHUB_EVENT_NAME=pull_request
  unset BASE_SHA
  export HEAD_SHA="$h"
  bash "$tmp/gate.sh"
) >/dev/null 2>&1
ec=$?
set -e
[[ "$ec" -ne 0 ]] || fail "expected missing BASE_SHA to exit non-zero"

echo "test-security-audit-paths: OK"
