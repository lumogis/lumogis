#!/usr/bin/env bash
# LUM-789 — report files the private release line changed that the integration line lacks.
#
# WHY THIS EXISTS
#
# Getting a promotion green takes fixes made *on the RC or on `main`*: CI workflow
# edits, lockfile regeneration, formatting, flaky-test hardening, security bumps.
# Nothing carries them back to `dev`. After the 2026-08-02 scope-D promotion, eight
# such commits sat on `main` and none reached `dev`, which was then failing
# `ruff format --check` on a gate `main` had already passed. The next promotion would
# have rediscovered every one of them.
#
# `/prepare-private-release-from-dev` Step 10 documents the back-port. A documented
# step can be skipped, so this is the ratchet that notices when it was.
#
# WHAT THIS IS AND IS NOT
#
# It is a REVIEW LIST, not a correctness oracle. It answers "which files did `main`
# change that `dev` does not have the change for" — and for a file both branches
# edited, no automated answer is possible without reading them. Measured against the
# 2026-08-02 incident it reported 17 candidates for 15 genuine back-ports: full
# recall, two false positives (files where `dev` was simply ahead). That ratio is the
# point — a gate that misses drift is worthless, one that over-reports is merely
# annoying, and the baseline below is how the annoyance is retired.
#
# Two designs were tried and rejected, recorded so nobody re-walks them:
#   * Per-commit `git apply --check --reverse` over `dev..main` — 57 candidates for 15
#     real ones. Commit diffs stop reverse-applying as soon as surrounding context
#     drifts, and squash/promotion commits never reverse-apply at all.
#   * Three-way classification with no path filter — the 15 real files land in a
#     114-entry "both changed" bucket, swamped by `docs/private/` and
#     `docs/_librarian/` churn.
# Path exclusion is what makes the signal legible; it is not a detail.
#
# USAGE
#
#   scripts/check-main-dev-drift.sh [MAIN_REF] [DEV_REF]
#
# Defaults: origin/main, HEAD. Exit 0 = nothing to review, 1 = candidates found.
# Retire a known-clear candidate by adding its path to the baseline file below.

set -euo pipefail

MAIN_REF="${1:-origin/main}"
DEV_REF="${2:-HEAD}"
REPO_ROOT="$(git rev-parse --show-toplevel)"
BASELINE="$REPO_ROOT/scripts/main-dev-drift-baseline.txt"

# Paths that live on the release line by design and must NEVER flow back to `dev`.
# Keep in sync with `/prepare-private-release-from-dev` Hard rules 3 and 5 and the
# Step 10 exclusion table.
#   CHANGELOG / capabilities / public-export : authored on `main` at publish time
#   docs/release/                            : publish evidence, release-line only
#   docs/private/, docs/_librarian/          : stripped from `main`; live on `dev`
#   .cursor, .claude                         : maintainer trees, never on `main`
RELEASE_ONLY_PATHS=(
  "CHANGELOG.md"
  "docs/capabilities.md"
  "docs/public-export"
  "docs/release"
  "docs/private"
  "docs/_librarian"
  ".cursor"
  ".claude"
)

die() { echo "check-main-dev-drift: $*" >&2; exit 2; }

for ref in "$MAIN_REF" "$DEV_REF"; do
  git rev-parse --verify --quiet "$ref" >/dev/null \
    || die "cannot resolve '$ref' — run 'git fetch origin' first"
done

# A shallow clone silently breaks this: merge-base cannot see past a graft boundary,
# so the comparison base comes out wrong and the branches look unrelated. Refuse
# rather than report a confident falsehood — that exact trap produced a wrong root
# cause on LUM-789 before it was caught.
[ "$(git rev-parse --is-shallow-repository)" = "false" ] \
  || die "shallow clone — run 'git fetch --unshallow' first"

BASE="$(git merge-base "$MAIN_REF" "$DEV_REF")" || die "no merge base for $MAIN_REF and $DEV_REF"

is_release_only() {
  local f="$1" p
  for p in "${RELEASE_ONLY_PATHS[@]}"; do
    case "$f" in "$p"|"$p"/*) return 0 ;; esac
  done
  return 1
}

is_baselined() {
  [ -f "$BASELINE" ] || return 1
  grep -qxF -- "$1" <(grep -v '^\s*#' "$BASELINE" | sed '/^\s*$/d') 2>/dev/null
}

# `--verify` is load-bearing: plain `git rev-parse <ref>:<missing-path>` echoes its
# argument back and exits 0 (it is a general argument parser, not a lookup), so a file
# absent on one side would compare unequal to itself and report as drift forever.
blob_at() { git rev-parse --verify --quiet "$1:$2" 2>/dev/null || echo ABSENT; }

candidates=()
baselined=0
excluded=0

while IFS= read -r f; do
  if is_release_only "$f"; then excluded=$((excluded + 1)); continue; fi

  m="$(blob_at "$MAIN_REF" "$f")"
  d="$(blob_at "$DEV_REF"  "$f")"
  b="$(blob_at "$BASE"     "$f")"

  [ "$m" = "$d" ] && continue          # identical content — nothing to carry
  [ "$m" = "$b" ] && continue          # `main` never touched it; `dev` is just ahead

  if is_baselined "$f"; then baselined=$((baselined + 1)); continue; fi
  candidates+=("$f")
done < <(git diff --name-only "$DEV_REF" "$MAIN_REF")

echo "check-main-dev-drift: $MAIN_REF vs $DEV_REF (base $(git rev-parse --short "$BASE"))"
echo "  release-line-only paths excluded : $excluded"
echo "  baselined (reviewed, not drift)  : $baselined"
echo "  TO REVIEW                        : ${#candidates[@]}"

if [ "${#candidates[@]}" -eq 0 ]; then
  echo "check-main-dev-drift: OK — no unreviewed release-line changes are missing here"
  exit 0
fi

echo
echo "Changed on $MAIN_REF, and $DEV_REF does not have that change:"
printf '  %s\n' "${candidates[@]}"
cat <<EOF

Review each. Back-port per /prepare-private-release-from-dev Step 10 — path-scoped
(\`git checkout $MAIN_REF -- <path>\`). If a file is here because \`dev\` is simply
ahead of \`main\` on it, add the path to:
  ${BASELINE#"$REPO_ROOT"/}

Do NOT \`git merge $MAIN_REF\` into dev to clear this: the branches share history, so
the merge is legal, but \`main\` has deleted the private-only trees the release line
strips and the merge would delete them here too (Hard rule 7).
EOF
exit 1
