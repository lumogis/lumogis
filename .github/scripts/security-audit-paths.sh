#!/usr/bin/env bash
# LUM-190 — decide whether PR CI should run `make audit-local` + advisory Bandit.
#
# On pull_request: should_run=true if any changed path matches the contract below.
# On push: should_run=true unconditionally (branch filter lives in ci.yml on:).
# Other GITHUB_EVENT_NAME values: exit non-zero (OpenAPI path-gate parity).
#
# Environment: GITHUB_OUTPUT (required), GITHUB_EVENT_NAME, and for pull_request:
# BASE_SHA, HEAD_SHA. No network.
set -euo pipefail

: "${GITHUB_OUTPUT:?GITHUB_OUTPUT must be set}"
event="${GITHUB_EVENT_NAME:?GITHUB_EVENT_NAME must be set}"

if [[ "$event" == "push" ]]; then
  echo "should_run=true" >>"$GITHUB_OUTPUT"
  exit 0
fi

if [[ "$event" != "pull_request" ]]; then
  echo "security-audit-paths: unsupported GITHUB_EVENT_NAME=$event" >&2
  exit 1
fi

: "${BASE_SHA:?BASE_SHA must be set for pull_request}"
: "${HEAD_SHA:?HEAD_SHA must be set for pull_request}"

if ! git rev-parse --verify "$BASE_SHA^{commit}" >/dev/null 2>&1; then
  echo "security-audit-paths: invalid or missing BASE_SHA" >&2
  exit 1
fi
if ! git rev-parse --verify "$HEAD_SHA^{commit}" >/dev/null 2>&1; then
  echo "security-audit-paths: invalid or missing HEAD_SHA" >&2
  exit 1
fi

matches_path() {
  local file="$1"
  if [[ "$file" == orchestrator/* ]]; then
    return 0
  fi
  if [[ "$file" == services/* ]]; then
    return 0
  fi
  if [[ "$file" == requirements*.txt ]] || [[ "$file" == */requirements*.txt ]]; then
    return 0
  fi
  if [[ "$file" == "clients/lumogis-web/package.json" ]] || [[ "$file" == "clients/lumogis-web/package-lock.json" ]]; then
    return 0
  fi
  if [[ "$file" == "scripts/audit_local.sh" ]] || [[ "$file" == "scripts/requirements-security-audit.txt" ]]; then
    return 0
  fi
  if [[ "$file" == "Makefile" ]] || [[ "$file" == ".github/workflows/ci.yml" ]] || [[ "$file" == ".github/scripts/security-audit-paths.sh" ]]; then
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
