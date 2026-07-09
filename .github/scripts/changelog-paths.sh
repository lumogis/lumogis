#!/usr/bin/env bash
# LUM-258 — decide whether the changelog gate should enforce on this PR.
#
# Moves changelog.yml's workflow-level `paths:` filter to a JOB-level gate so the
# workflow ALWAYS runs on pull_request (the required status check always reports a
# conclusion, even on docs-only PRs). The match logic mirrors changelog.yml's
# include list + the negated mock-capability rule, sourced from the single
# canonical list scripts/changelog-gate-paths.txt (same file used by the local
# `make changelog-check` / scripts/check-changelog-touched.sh).
#
# Positive match (should_run=true) if any changed path matches an include glob and
# the triggering set is not exclusively under services/lumogis-mock-capability/.
#
# Environment: GITHUB_EVENT_NAME, GITHUB_OUTPUT (required). For pull_request:
# BASE_SHA, HEAD_SHA (PR base and head commits). No network.
set -euo pipefail

: "${GITHUB_OUTPUT:?GITHUB_OUTPUT must be set}"
event="${GITHUB_EVENT_NAME:?GITHUB_EVENT_NAME must be set}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
PATHS_FILE="${REPO_ROOT}/scripts/changelog-gate-paths.txt"

if [[ ! -f "$PATHS_FILE" ]]; then
  echo "changelog-paths: missing ${PATHS_FILE}" >&2
  exit 1
fi

if [[ "$event" == "push" ]]; then
  echo "should_run=true" >>"$GITHUB_OUTPUT"
  exit 0
fi

if [[ "$event" != "pull_request" ]]; then
  echo "changelog-paths: unsupported GITHUB_EVENT_NAME=$event" >&2
  exit 1
fi

: "${BASE_SHA:?BASE_SHA must be set for pull_request}"
: "${HEAD_SHA:?HEAD_SHA must be set for pull_request}"

if ! git rev-parse --verify "$BASE_SHA^{commit}" >/dev/null 2>&1; then
  echo "changelog-paths: invalid or missing BASE_SHA" >&2
  exit 1
fi
if ! git rev-parse --verify "$HEAD_SHA^{commit}" >/dev/null 2>&1; then
  echo "changelog-paths: invalid or missing HEAD_SHA" >&2
  exit 1
fi

mapfile -t CHANGED < <(git diff --name-only "$BASE_SHA" "$HEAD_SHA" || true)

# Reuse the exact include-glob + mock-capability negation semantics from
# scripts/check-changelog-touched.sh so CI and local `make changelog-check` agree.
should="$(
  python3 - "$PATHS_FILE" "${CHANGED[@]}" <<'PY'
import fnmatch
import os
import sys

paths_file = sys.argv[1]
changed = sys.argv[2:]

MOCK_PREFIX = "services/lumogis-mock-capability/"


def load_patterns(path: str) -> list[str]:
    patterns: list[str] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            patterns.append(line)
    return patterns


def matches_include(path: str, patterns: list[str]) -> bool:
    for raw in patterns:
        if raw.endswith("/**"):
            prefix = raw[:-3].rstrip("/")
            if path == prefix or path.startswith(prefix + "/"):
                return True
            continue
        if "/" not in raw and raw.startswith("docker-compose") and raw.endswith(".yml"):
            if "/" in path:
                continue
            if fnmatch.fnmatch(os.path.basename(path), raw):
                return True
            continue
        if fnmatch.fnmatch(path, raw):
            return True
    return False


patterns = load_patterns(paths_file)
triggering = [p for p in changed if matches_include(p, patterns)]
if not triggering:
    print("false")
elif all(p.startswith(MOCK_PREFIX) for p in triggering):
    print("false")
else:
    print("true")
PY
)"

if [[ "$should" == "true" ]]; then
  echo "should_run=true" >>"$GITHUB_OUTPUT"
else
  echo "should_run=false" >>"$GITHUB_OUTPUT"
fi
