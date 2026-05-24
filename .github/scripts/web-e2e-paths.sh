#!/usr/bin/env bash
# LUM-60 — decide whether web-e2e CI should run stack + Playwright (path gate).
#
# On pull_request: should_run=true if any changed path matches the contract below.
# On workflow_dispatch / schedule: should_run=true unconditionally.
# On push / other events: exit non-zero (parity with security-audit-paths).
#
# Environment: GITHUB_OUTPUT (required), GITHUB_EVENT_NAME, and for pull_request:
# BASE_SHA, HEAD_SHA. No network.
set -euo pipefail

: "${GITHUB_OUTPUT:?GITHUB_OUTPUT must be set}"
event="${GITHUB_EVENT_NAME:?GITHUB_EVENT_NAME must be set}"

if [[ "$event" == "workflow_dispatch" ]] || [[ "$event" == "schedule" ]]; then
  echo "should_run=true" >>"$GITHUB_OUTPUT"
  exit 0
fi

if [[ "$event" != "pull_request" ]]; then
  echo "web-e2e-paths: unsupported GITHUB_EVENT_NAME=$event" >&2
  exit 1
fi

: "${BASE_SHA:?BASE_SHA must be set for pull_request}"
: "${HEAD_SHA:?HEAD_SHA must be set for pull_request}"

if ! git rev-parse --verify "$BASE_SHA^{commit}" >/dev/null 2>&1; then
  echo "web-e2e-paths: invalid or missing BASE_SHA" >&2
  exit 1
fi
if ! git rev-parse --verify "$HEAD_SHA^{commit}" >/dev/null 2>&1; then
  echo "web-e2e-paths: invalid or missing HEAD_SHA" >&2
  exit 1
fi

matches_path() {
  local file="$1"
  if [[ "$file" == clients/lumogis-web/* ]]; then
    return 0
  fi
  if [[ "$file" == caddy/* ]] || [[ "$file" == docker/caddy/* ]]; then
    return 0
  fi
  if [[ "$file" == "docker-compose.yml" ]] \
    || [[ "$file" == "docker-compose.test.yml" ]] \
    || [[ "$file" == "docker-compose.web-e2e-ci.yml" ]]; then
    return 0
  fi
  if [[ "$file" == clients/lumogis-web/tests/e2e/* ]]; then
    return 0
  fi
  if [[ "$file" == ".github/workflows/web-e2e.yml" ]] \
    || [[ "$file" == ".github/scripts/web-e2e-paths.sh" ]]; then
    return 0
  fi
  if [[ "$file" == "Makefile" ]]; then
    return 0
  fi
  return 1
}

should=false
while IFS= read -r file || [[ -n "${file:-}" ]]; do
  [[ -z "${file}" ]] && continue
  if matches_path "$file"; then
    should=true
    break
  fi
done < <(git diff --name-only "$BASE_SHA" "$HEAD_SHA")

if [[ "$should" == true ]]; then
  echo "should_run=true" >>"$GITHUB_OUTPUT"
else
  echo "should_run=false" >>"$GITHUB_OUTPUT"
fi
