#!/usr/bin/env bash
# LUM-94 — decide whether PR CI should run `make openapi-check` (OpenAPI snapshot / codegen drift).
#
# Positive match (should_run=true on pull_request) if any changed path is:
#   - under orchestrator/
#   - clients/lumogis-web/openapi.snapshot.json
#   - clients/lumogis-web/scripts/codegen.mjs
#   - clients/lumogis-web/package.json
#   - clients/lumogis-web/package-lock.json
#   - Makefile at repo root
#   - .github/scripts/openapi-breaking-check.sh (LUM-302 breaking gate)
#   - scripts/fixtures/openapi-breaking-check/ (LUM-302 fixture smoke)
#
# Not matched (examples → should_run=false): docs-only, stack-control-only,
# .github/workflows-only without those paths — see list above.
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
  echo "openapi-check-paths: unsupported GITHUB_EVENT_NAME=$event" >&2
  exit 1
fi

: "${BASE_SHA:?BASE_SHA must be set for pull_request}"
: "${HEAD_SHA:?HEAD_SHA must be set for pull_request}"

if ! git rev-parse --verify "$BASE_SHA^{commit}" >/dev/null 2>&1; then
  echo "openapi-check-paths: invalid or missing BASE_SHA" >&2
  exit 1
fi
if ! git rev-parse --verify "$HEAD_SHA^{commit}" >/dev/null 2>&1; then
  echo "openapi-check-paths: invalid or missing HEAD_SHA" >&2
  exit 1
fi

should=false
while IFS= read -r file || [[ -n "${file:-}" ]]; do
  [[ -z "${file}" ]] && continue
  if [[ "$file" == orchestrator/* ]] \
    || [[ "$file" == "clients/lumogis-web/openapi.snapshot.json" ]] \
    || [[ "$file" == "clients/lumogis-web/scripts/codegen.mjs" ]] \
    || [[ "$file" == "clients/lumogis-web/package.json" ]] \
    || [[ "$file" == "clients/lumogis-web/package-lock.json" ]] \
    || [[ "$file" == "Makefile" ]] \
    || [[ "$file" == ".github/scripts/openapi-breaking-check.sh" ]] \
    || [[ "$file" == scripts/fixtures/openapi-breaking-check/* ]]; then
    should=true
    break
  fi
done < <(git diff --name-only "$BASE_SHA" "$HEAD_SHA")

if [[ "$should" == true ]]; then
  echo "should_run=true" >>"$GITHUB_OUTPUT"
else
  echo "should_run=false" >>"$GITHUB_OUTPUT"
fi
