#!/usr/bin/env bash
# LUM-486 — decide whether PR CI should run `make compose-test-backup` (lumogis-test-backup DR round-trip).
#
# Positive match (should_run=true on pull_request) if any changed path is:
#   - scripts/backup/**
#   - scripts/integration-backup-roundtrip.sh
#   - tests/integration/seed_backup_roundtrip_data.sh
#   - tests/integration/test_backup_restore_roundtrip.sh
#   - docker/backup/**
#   - docker-compose.yml
#   - docker-compose.falkordb.yml
#   - config/test.env.example
#   - Makefile
#   - .github/workflows/ci.yml
#   - .github/scripts/backup-integration-paths.sh
#
# Environment: GITHUB_EVENT_NAME, GITHUB_OUTPUT (required). For pull_request:
# BASE_SHA, HEAD_SHA (PR base and head commits). No network.
set -euo pipefail

: "${GITHUB_OUTPUT:?GITHUB_OUTPUT must be set}"
event="${GITHUB_EVENT_NAME:?GITHUB_EVENT_NAME must be set}"

if [[ "$event" == "push" ]]; then
  echo "should_run=true" >>"$GITHUB_OUTPUT"
  exit 0
fi

if [[ "$event" != "pull_request" ]]; then
  echo "backup-integration-paths: unsupported GITHUB_EVENT_NAME=$event" >&2
  exit 1
fi

: "${BASE_SHA:?BASE_SHA must be set for pull_request}"
: "${HEAD_SHA:?HEAD_SHA must be set for pull_request}"

if ! git rev-parse --verify "$BASE_SHA^{commit}" >/dev/null 2>&1; then
  echo "backup-integration-paths: invalid or missing BASE_SHA" >&2
  exit 1
fi
if ! git rev-parse --verify "$HEAD_SHA^{commit}" >/dev/null 2>&1; then
  echo "backup-integration-paths: invalid or missing HEAD_SHA" >&2
  exit 1
fi

should=false
while IFS= read -r file || [[ -n "${file:-}" ]]; do
  [[ -z "${file}" ]] && continue
  if [[ "$file" == scripts/backup/* ]] \
    || [[ "$file" == "scripts/integration-backup-roundtrip.sh" ]] \
    || [[ "$file" == "tests/integration/seed_backup_roundtrip_data.sh" ]] \
    || [[ "$file" == "tests/integration/test_backup_restore_roundtrip.sh" ]] \
    || [[ "$file" == "tests/unit/test_backup_retention.sh" ]] \
    || [[ "$file" == docker/backup/* ]] \
    || [[ "$file" == "docker-compose.yml" ]] \
    || [[ "$file" == "docker-compose.falkordb.yml" ]] \
    || [[ "$file" == "config/test.env.example" ]] \
    || [[ "$file" == "Makefile" ]] \
    || [[ "$file" == ".github/workflows/ci.yml" ]] \
    || [[ "$file" == ".github/scripts/backup-integration-paths.sh" ]]; then
    should=true
    break
  fi
done < <(git diff --name-only "$BASE_SHA" "$HEAD_SHA")

if [[ "$should" == true ]]; then
  echo "should_run=true" >>"$GITHUB_OUTPUT"
else
  echo "should_run=false" >>"$GITHUB_OUTPUT"
fi
