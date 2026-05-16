#!/usr/bin/env bash
# Local helper: mirror changelog gate semantics (see CONTRIBUTING.md and changelog.yml).
# Usage: scripts/check-changelog-touched.sh [BASE_REF]
#   BASE_REF — ref to diff against (default: origin/dev, then origin/main).
# Exit 0 when no product-path obligation applies, when CHANGELOG.md is in the diff,
# or when skip text is present in CHANGELOG_GATE_PR_BODY (same literal as CI: [skip changelog]).
set -euo pipefail

ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -z "$ROOT" ]]; then
  echo "check-changelog-touched: not inside a git repository" >&2
  exit 2
fi
cd "$ROOT"

PATHS_FILE="${ROOT}/scripts/changelog-gate-paths.txt"
if [[ ! -f "$PATHS_FILE" ]]; then
  echo "check-changelog-touched: missing ${PATHS_FILE}" >&2
  exit 2
fi

BASE="${1:-}"
if [[ -z "$BASE" ]]; then
  if git show-ref --verify --quiet refs/remotes/origin/dev; then
    BASE="origin/dev"
  elif git show-ref --verify --quiet refs/remotes/origin/main; then
    BASE="origin/main"
  else
    BASE="HEAD~1"
  fi
fi

if ! git rev-parse --verify --quiet "$BASE" >/dev/null; then
  echo "check-changelog-touched: base ref '${BASE}' does not exist" >&2
  exit 2
fi

HEAD="$(git rev-parse HEAD)"
SKIP_BODY="${CHANGELOG_GATE_PR_BODY-}"

mapfile -t CHANGED < <(git diff --name-only "${BASE}...${HEAD}" || true)

python3 - "$PATHS_FILE" "$SKIP_BODY" "${CHANGED[@]}" <<'PY'
import fnmatch
import os
import sys

paths_file = sys.argv[1]
skip_body = sys.argv[2]
changed = sys.argv[3:]

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


def skip_for_mock_capability_only(triggering: list[str]) -> bool:
    if not triggering:
        return False
    return all(p.startswith(MOCK_PREFIX) for p in triggering)


patterns = load_patterns(paths_file)
if "[skip changelog]" in skip_body.lower():
    sys.exit(0)

triggering = [p for p in changed if matches_include(p, patterns)]
if not triggering:
    sys.exit(0)
if skip_for_mock_capability_only(triggering):
    sys.exit(0)
if "CHANGELOG.md" in changed:
    sys.exit(0)

print(
    "Changelog gate: product paths changed between base and HEAD but CHANGELOG.md is not in the diff.\n"
    "Add an [Unreleased] entry, or use label Skip-Changelog / put [skip changelog] in the PR body on GitHub.\n"
    "For local runs only, export CHANGELOG_GATE_PR_BODY with [skip changelog] to mimic the PR-body bypass.\n"
    "See CONTRIBUTING.md and .github/workflows/changelog.yml.",
    file=sys.stderr,
)
sys.exit(1)
PY
