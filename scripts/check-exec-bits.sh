#!/usr/bin/env bash
# Fail when a committed shell script is not 100755 in the git INDEX (LUM-784).
#
# This repo runs with core.fileMode=false, so git ignores filesystem permission
# bits: a script created locally and committed lands as 100644 even though it
# is executable on the author's disk. Nothing warns, and the failure only shows
# up on a clean checkout (docker/egress-proxy/entrypoint.sh: "permission
# denied" at container start in three compose overlays). Because of that, the
# mode is read from the index (`git ls-files -s`), never from the filesystem.
#
# Rule: every tracked *.sh under the scoped directories must be 100755, except
# files listed in the allowlist (files that are only ever sourced, each with a
# reason). Fix a failure with:  git update-index --chmod=+x <file>
#
# Scope is the directories whose scripts are executed directly by compose,
# Dockerfiles, the Makefile, CI or humans. Other trees (e.g. product clients
# with their own tooling) are out of scope here.
#
# Usage: scripts/check-exec-bits.sh [REPO_ROOT] [ALLOWLIST]

set -euo pipefail

ROOT="${1:-.}"
ALLOWLIST="${2:-$ROOT/scripts/exec-bit-allowlist.txt}"
SCOPE=(docker scripts .github/scripts stack-control tests)

if ! git -C "$ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  echo "check-exec-bits: not a git work tree: $ROOT" >&2
  exit 1
fi

declare -A allowed=()
if [[ -f "$ALLOWLIST" ]]; then
  while read -r line; do
    line="${line%%#*}"
    line="${line//[[:space:]]/}"
    [[ -n "$line" ]] && allowed["$line"]=1
  done < "$ALLOWLIST"
fi

status=0
declare -A seen=()
while read -r mode _ _ path; do
  [[ "$path" == *.sh ]] || continue
  seen["$path"]=1
  [[ "$mode" == "100755" ]] && continue
  [[ -n "${allowed[$path]:-}" ]] && continue
  echo "check-exec-bits: $path is $mode in the index, expected 100755" >&2
  echo "  fix: git update-index --chmod=+x $path   (or allowlist it if it is only sourced)" >&2
  status=1
done < <(git -C "$ROOT" ls-files -s -- "${SCOPE[@]}")

for path in "${!allowed[@]}"; do
  if [[ -z "${seen[$path]:-}" ]]; then
    echo "check-exec-bits: note: allowlist entry no longer matches a tracked script: $path" >&2
  fi
done

if [[ "$status" -eq 0 ]]; then
  echo "check-exec-bits: OK"
fi
exit "$status"
