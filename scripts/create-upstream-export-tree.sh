#!/usr/bin/env bash
# Build a public-shaped directory tree from HEAD for lumogis/lumogis (upstream/main).
# Uses a temporary index so the repo's index and working tree are not modified.
#
# Usage: scripts/create-upstream-export-tree.sh [OUTPUT_DIR]
#        Default OUTPUT_DIR: /tmp/lumogis-upstream-export
#
set -euo pipefail

die() { echo "create-upstream-export-tree: FAIL: $*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

OUT_ARG="${1:-/tmp/lumogis-upstream-export}"
if [[ "$OUT_ARG" == /* ]]; then
  OUT="$OUT_ARG"
else
  OUT="$(pwd)/$OUT_ARG"
fi

cd "$ROOT"

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  die "not a git repository ($ROOT)"
fi

TMP_INDEX="$(mktemp "${TMPDIR:-/tmp}/lumogis-upstream-export-index.XXXXXX")"
cleanup() { rm -f "$TMP_INDEX" "${TMP_INDEX}.lock"; }
trap cleanup EXIT

rm -rf "$OUT"
mkdir -p "$OUT"

GIT_INDEX_FILE="$TMP_INDEX" git read-tree HEAD
GIT_INDEX_FILE="$TMP_INDEX" git checkout-index -a -f --prefix="${OUT}/"

# Public-shaped paths — scripts/public-export-strip-list.txt must match check-public-export.sh
STRIP_LIST="$SCRIPT_DIR/public-export-strip-list.txt"
[[ -f "$STRIP_LIST" ]] || die "missing strip list: $STRIP_LIST"
while IFS= read -r raw_line || [[ -n "${raw_line}" ]]; do
  line="${raw_line%%#*}"
  line="$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
  [[ -z "$line" ]] && continue
  target="${OUT}/${line}"
  if [[ -e "$target" ]]; then
    rm -rf "$target"
  fi
done <"$STRIP_LIST"

echo "create-upstream-export-tree: $OUT"
echo "create-upstream-export-tree: top-level:"
ls -la "$OUT"
