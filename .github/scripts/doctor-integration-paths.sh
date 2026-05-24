#!/usr/bin/env bash
# LUM-319 — decide whether PR CI should run `make compose-test-doctor` (live lumogis-test + doctor JSON).
#
# Positive match (should_run=true on pull_request) if any changed path is:
#   - scripts/doctor/**
#   - Makefile
#   - docker-compose.yml
#   - docker-compose.test-doctor.yml
#   - config/test.env.example
#   - .github/workflows/ci.yml
#   - .github/scripts/doctor-integration-paths.sh
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
  echo "doctor-integration-paths: unsupported GITHUB_EVENT_NAME=$event" >&2
  exit 1
fi

: "${BASE_SHA:?BASE_SHA must be set for pull_request}"
: "${HEAD_SHA:?HEAD_SHA must be set for pull_request}"

if ! git rev-parse --verify "$BASE_SHA^{commit}" >/dev/null 2>&1; then
  echo "doctor-integration-paths: invalid or missing BASE_SHA" >&2
  exit 1
fi
if ! git rev-parse --verify "$HEAD_SHA^{commit}" >/dev/null 2>&1; then
  echo "doctor-integration-paths: invalid or missing HEAD_SHA" >&2
  exit 1
fi

should=false
while IFS= read -r file || [[ -n "${file:-}" ]]; do
  [[ -z "${file}" ]] && continue
  if [[ "$file" == scripts/doctor/* ]] \
    || [[ "$file" == "Makefile" ]] \
    || [[ "$file" == "docker-compose.yml" ]] \
    || [[ "$file" == "docker-compose.test-doctor.yml" ]] \
    || [[ "$file" == "config/test.env.example" ]] \
    || [[ "$file" == ".github/workflows/ci.yml" ]] \
    || [[ "$file" == ".github/scripts/doctor-integration-paths.sh" ]]; then
    should=true
    break
  fi
done < <(git diff --name-only "$BASE_SHA" "$HEAD_SHA")

if [[ "$should" == true ]]; then
  echo "should_run=true" >>"$GITHUB_OUTPUT"
else
  echo "should_run=false" >>"$GITHUB_OUTPUT"
fi
