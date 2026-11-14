#!/usr/bin/env bash
# Verify release-line documentation on origin/main matches origin/dev.
#
# After publish, CHANGELOG, capabilities, and public-export templates are
# canonical on private main. Integration dev must mirror them so promotion,
# /update-public-export, and audits do not read stale release evidence.
#
# Usage (from repo root):
#   scripts/check-dev-release-doc-sync.sh
#   MAIN_REF=origin/main DEV_REF=origin/dev scripts/check-dev-release-doc-sync.sh
#
# Exit 0 when all tracked paths match; exit 1 when any differ or refs are missing.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

die() { echo "check-dev-release-doc-sync: FAIL: $*" >&2; exit 1; }
warn() { echo "check-dev-release-doc-sync: warning: $*" >&2; }

MAIN_REF="${MAIN_REF:-origin/main}"
DEV_REF="${DEV_REF:-origin/dev}"

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  die "not a git repository"
fi

for ref in "$MAIN_REF" "$DEV_REF"; do
  if ! git rev-parse --verify --quiet "$ref" >/dev/null; then
    die "ref not found: $ref (run git fetch origin)"
  fi
done

SYNC_PATHS=(
  CHANGELOG.md
  docs/capabilities.md
  docs/public-export/AGENTS.md
  docs/public-export/LUMOGIS_AGENT_ORIENTATION.md
  docs/public-export/contributing-ai-agents.md
  docs/public-export/CONTRIBUTING-BEGINNERS.md
)

missing=()
for path in "${SYNC_PATHS[@]}"; do
  if ! git cat-file -e "$MAIN_REF:$path" 2>/dev/null; then
    missing+=("$path (missing on $MAIN_REF)")
  elif ! git cat-file -e "$DEV_REF:$path" 2>/dev/null; then
    missing+=("$path (missing on $DEV_REF)")
  fi
done

if [[ "${#missing[@]}" -gt 0 ]]; then
  die "missing tracked path(s): ${missing[*]}"
fi

drift=0
for path in "${SYNC_PATHS[@]}"; do
  if ! git diff --quiet "$MAIN_REF" "$DEV_REF" -- "$path"; then
    echo "check-dev-release-doc-sync: drift: $path" >&2
    git diff --stat "$MAIN_REF" "$DEV_REF" -- "$path" >&2 || true
    drift=1
  fi
done

if [[ "$drift" -ne 0 ]]; then
  echo "check-dev-release-doc-sync: main ($MAIN_REF) and dev ($DEV_REF) differ on release docs." >&2
  echo "check-dev-release-doc-sync: run scripts/sync-release-docs-to-dev.sh after publish, then commit on dev." >&2
  exit 1
fi

echo "check-dev-release-doc-sync.sh: OK ($MAIN_REF vs $DEV_REF)"
