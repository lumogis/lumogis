#!/usr/bin/env bash
# Copy canonical release documentation from private main onto the integration branch.
#
# Run after /publish-private-main-to-public (or when check-dev-release-doc-sync fails).
# Does not merge code — only the release-doc path set shared with check-dev-release-doc-sync.sh.
#
# Usage (from repo root):
#   git fetch origin
#   git checkout dev
#   scripts/sync-release-docs-to-dev.sh          # stage only; review then commit
#   scripts/sync-release-docs-to-dev.sh --commit # stage + commit with default message
#
# Environment:
#   MAIN_REF   default origin/main
#   COMMIT_MSG override default commit message when using --commit
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

die() { echo "sync-release-docs-to-dev: FAIL: $*" >&2; exit 1; }

MAIN_REF="${MAIN_REF:-origin/main}"
DO_COMMIT=0
for arg in "$@"; do
  case "$arg" in
    --commit) DO_COMMIT=1 ;;
    -h|--help)
      sed -n '2,14p' "$0"
      exit 0
      ;;
    *) die "unknown argument: $arg (use --commit or --help)" ;;
  esac
done

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  die "not a git repository"
fi

if ! git rev-parse --verify --quiet "$MAIN_REF" >/dev/null; then
  die "ref not found: $MAIN_REF (run git fetch origin)"
fi

current_branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
if [[ "$current_branch" == "HEAD" ]]; then
  die "detached HEAD — checkout dev before syncing release docs"
fi
if [[ "$current_branch" != "dev" ]]; then
  echo "sync-release-docs-to-dev: warning: branch is '$current_branch', not dev (continuing)." >&2
fi

SYNC_PATHS=(
  CHANGELOG.md
  docs/capabilities.md
  docs/public-export/AGENTS.md
  docs/public-export/LUMOGIS_AGENT_ORIENTATION.md
  docs/public-export/contributing-ai-agents.md
  docs/public-export/CONTRIBUTING-BEGINNERS.md
)

for path in "${SYNC_PATHS[@]}"; do
  if ! git cat-file -e "$MAIN_REF:$path" 2>/dev/null; then
    die "missing on $MAIN_REF: $path"
  fi
done

echo "sync-release-docs-to-dev: copying from $MAIN_REF onto $current_branch"
git checkout "$MAIN_REF" -- "${SYNC_PATHS[@]}"

if git diff --cached --quiet; then
  echo "sync-release-docs-to-dev: already in sync — nothing to stage"
  exit 0
fi

echo "sync-release-docs-to-dev: staged changes:"
git diff --cached --stat

if [[ "$DO_COMMIT" -eq 0 ]]; then
  echo "sync-release-docs-to-dev: review with 'git diff --cached', then commit on $current_branch"
  echo "sync-release-docs-to-dev: or re-run with --commit"
  exit 0
fi

main_short="$(git rev-parse --short "$MAIN_REF")"
default_msg="chore(release): sync CHANGELOG, capabilities, and public-export from main @ ${main_short}"
COMMIT_MSG="${COMMIT_MSG:-$default_msg}"

git commit -m "$COMMIT_MSG"
echo "sync-release-docs-to-dev: committed on $current_branch"
