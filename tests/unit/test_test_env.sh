#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
# LUM-894 — scripts/test-env.sh: `auto` tier resolution, image-tag determinism,
# and refusal of unimplemented tiers. Needs neither Docker nor a network:
# LUMOGIS_TEST_ENV_CHANGED_FILES stands in for the git diff.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TE="${REPO_ROOT}/scripts/test-env.sh"

failures=0

assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$expected" != "$actual" ]]; then
    echo "FAIL: $label (expected '$expected', got '$actual')" >&2
    failures=$((failures + 1))
  fi
}

tier_for() {
  # $1 = changed files (newline-separated); rest = command
  local files="$1"
  shift
  LUMOGIS_TEST_ENV_CHANGED_FILES="$files" "$TE" tier -- "$@" | cut -d' ' -f1
}

assert_eq "integration command picks tier 2" 2 \
  "$(tier_for "README.md" python -m pytest tests/integration -q)"
assert_eq "make test-integration picks tier 2" 2 \
  "$(tier_for "" make test-integration)"
assert_eq "make test picks tier 2 (it collects the integration directory)" 2 \
  "$(tier_for "" make test)"
assert_eq "make -k test picks tier 2" 2 \
  "$(tier_for "" make -k test)"
assert_eq "make test-kg picks tier 1" 1 \
  "$(tier_for "" make test-kg)"
assert_eq "migration change picks tier 2" 2 \
  "$(tier_for "postgres/migrations/099-x.sql" make lint)"
assert_eq "adapter change picks tier 2" 2 \
  "$(tier_for $'docs/a.md\norchestrator/adapters/qdrant_store.py' make lint)"
assert_eq "visibility change picks tier 2" 2 \
  "$(tier_for "orchestrator/visibility.py" make lint)"
assert_eq "web-only change picks tier 1" 1 \
  "$(tier_for "clients/lumogis-web/src/App.tsx" make web-test)"
assert_eq "empty diff picks tier 1" 1 \
  "$(tier_for "" make lint)"
assert_eq "prefix match is anchored" 1 \
  "$(tier_for "docs/postgres/notes.md" make lint)"

# A base that cannot be resolved falls towards MORE coverage, and says so.
out="$(env -u LUMOGIS_TEST_ENV_CHANGED_FILES "$TE" tier --base refs/heads/does-not-exist-lum894 -- make lint)"
assert_eq "unresolvable base picks tier 2" 2 "${out%% *}"
[[ "$out" == *"cannot diff"* ]] || { echo "FAIL: unresolvable base gives no reason: $out" >&2; failures=$((failures + 1)); }

# The tag is a pure function of the image inputs.
t1="$("$TE" tag)"
t2="$("$TE" tag)"
assert_eq "tag is deterministic" "$t1" "$t2"
[[ "$t1" =~ ^lumogis-test-runner:[0-9a-f]{16}$ ]] || { echo "FAIL: tag shape: $t1" >&2; failures=$((failures + 1)); }

# Tiers 3 and 4 are not built yet; asking for one must fail loudly, before Docker.
set +e
err="$("$TE" run --tier 3 -- true 2>&1)"
rc=$?
set -e
assert_eq "tier 3 exits 2" 2 "$rc"
[[ "$err" == *"not implemented"* ]] || { echo "FAIL: tier 3 message: $err" >&2; failures=$((failures + 1)); }

if [[ "$failures" -gt 0 ]]; then
  echo "test_test_env: $failures failure(s)" >&2
  exit 1
fi
echo "test_test_env: ok"
