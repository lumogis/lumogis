#!/usr/bin/env bash
# LUM-302 — offline breaking-change gate on committed OpenAPI snapshot (oasdiff).
# Compares clients/lumogis-web/openapi.snapshot.json at HEAD to the same path at a
# resolved base revision (PR merge-base, push HEAD~1, or OPENAPI_BREAKING_BASE_REF).
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd "${SCRIPT_DIR}/../.." && pwd)
SNAPSHOT_PATH="clients/lumogis-web/openapi.snapshot.json"
HEAD_FILE="${REPO_ROOT}/${SNAPSHOT_PATH}"

fail_on="${OPENAPI_BREAKING_FAIL_ON:-ERR}"
case "$fail_on" in
  ERR | WARN | INFO | off) ;;
  *)
    echo "openapi-breaking-check: OPENAPI_BREAKING_FAIL_ON must be ERR, WARN, INFO, or off (got: ${fail_on})" >&2
    exit 2
    ;;
esac

if [[ "$fail_on" == "off" ]]; then
  echo "::warning::OpenAPI breaking gate bypassed (OPENAPI_BREAKING_FAIL_ON=off)"
  exit 0
fi

if ! command -v oasdiff >/dev/null 2>&1; then
  echo "openapi-breaking-check: oasdiff not on PATH; install Go 1.26+ then: go install github.com/oasdiff/oasdiff@v1.15.2" >&2
  exit 2
fi

cd "$REPO_ROOT"

if [[ ! -f "$HEAD_FILE" ]]; then
  echo "openapi-breaking-check: missing working-tree snapshot at ${SNAPSHOT_PATH}" >&2
  exit 2
fi

resolve_base_rev() {
  if [[ -n "${OPENAPI_BREAKING_BASE_REF:-}" ]]; then
    printf '%s' "${OPENAPI_BREAKING_BASE_REF}"
    return
  fi
  if [[ "${GITHUB_EVENT_NAME:-}" == "pull_request" && -n "${BASE_SHA:-}" && -n "${HEAD_SHA:-}" ]]; then
    local merge_base
    merge_base=$(git merge-base "$BASE_SHA" "$HEAD_SHA" 2>/dev/null || true)
    if [[ -n "$merge_base" ]]; then
      printf '%s' "$merge_base"
    else
      printf '%s' "$BASE_SHA"
    fi
    return
  fi
  printf 'HEAD~1'
}

rev=$(resolve_base_rev)

if ! git rev-parse --verify "${rev}^{commit}" >/dev/null 2>&1; then
  echo "openapi-breaking-check: invalid or unfetched git revision '${rev}' (check shallow clone / fetch-depth)" >&2
  exit 2
fi

if ! git cat-file -e "${rev}:${SNAPSHOT_PATH}" 2>/dev/null; then
  echo "::notice::OpenAPI breaking check: snapshot not present at base revision ${rev}; skipping compare (e.g. first introduction of the file)."
  exit 0
fi

base_tmp=$(mktemp)
cleanup() { rm -f "$base_tmp"; }
trap cleanup EXIT

if ! git show "${rev}:${SNAPSHOT_PATH}" >"$base_tmp" 2>/dev/null; then
  echo "openapi-breaking-check: unexpected git failure reading ${SNAPSHOT_PATH} at ${rev}" >&2
  exit 2
fi

set +e
oasdiff breaking "$base_tmp" "$HEAD_FILE" --format githubactions --fail-on "$fail_on"
rc=$?
set -e

if [[ "$rc" -eq 1 ]]; then
  exit 1
fi
if [[ "$rc" -ne 0 ]]; then
  echo "openapi-breaking-check: oasdiff exited with code ${rc}" >&2
  exit 2
fi
exit 0
