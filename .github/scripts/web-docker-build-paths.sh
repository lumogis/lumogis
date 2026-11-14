#!/usr/bin/env bash
# LUM-254 — decide whether PR CI should run a cold `docker compose build lumogis-web`.
#
# Positive match (should_build=true on pull_request):
#   - any change under clients/lumogis-web/ (includes scripts/codegen.mjs in that tree)
#   - docker-compose.yml at repo root
#
# Not matched (examples → should_build=false): docs-only, orchestrator-only,
# docker-compose.ghcr.yml only, Makefile-only, .github/workflows-only — those
# do not appear in the positive list above (ghcr overlay is not used for default
# compose build from repo root without COMPOSE_FILE).
#
# Environment: GITHUB_EVENT_NAME, GITHUB_OUTPUT (required). For pull_request:
# BASE_SHA, HEAD_SHA (PR base and head commits). No network.
set -euo pipefail

: "${GITHUB_OUTPUT:?GITHUB_OUTPUT must be set}"
event="${GITHUB_EVENT_NAME:?GITHUB_EVENT_NAME must be set}"

if [[ "$event" == "push" ]]; then
  echo "should_build=true" >>"$GITHUB_OUTPUT"
  exit 0
fi

if [[ "$event" != "pull_request" ]]; then
  echo "web-docker-build-paths: unsupported GITHUB_EVENT_NAME=$event" >&2
  exit 1
fi

: "${BASE_SHA:?BASE_SHA must be set for pull_request}"
: "${HEAD_SHA:?HEAD_SHA must be set for pull_request}"

if ! git rev-parse --verify "$BASE_SHA^{commit}" >/dev/null 2>&1; then
  echo "web-docker-build-paths: invalid or missing BASE_SHA" >&2
  exit 1
fi
if ! git rev-parse --verify "$HEAD_SHA^{commit}" >/dev/null 2>&1; then
  echo "web-docker-build-paths: invalid or missing HEAD_SHA" >&2
  exit 1
fi

should=false
# Use process substitution so a broken pipe from `while read` does not mask
# `git diff` failure under pipefail.
while IFS= read -r file || [[ -n "${file:-}" ]]; do
  [[ -z "${file}" ]] && continue
  if [[ "$file" == clients/lumogis-web/* ]] || [[ "$file" == "docker-compose.yml" ]]; then
    should=true
    break
  fi
done < <(git diff --name-only "$BASE_SHA" "$HEAD_SHA")

if [[ "$should" == true ]]; then
  echo "should_build=true" >>"$GITHUB_OUTPUT"
else
  echo "should_build=false" >>"$GITHUB_OUTPUT"
fi
