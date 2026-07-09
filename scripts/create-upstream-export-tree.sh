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

# LUM-376 — substitute public-safe agent orientation (sources live under docs/public-export/)
PUBLIC_EXPORT_SRC="$ROOT/docs/public-export"
[[ -d "$PUBLIC_EXPORT_SRC" ]] || die "missing public export templates: $PUBLIC_EXPORT_SRC"
for required in AGENTS.md LUMOGIS_AGENT_ORIENTATION.md contributing-ai-agents.md CONTRIBUTING-BEGINNERS.md EVALUATION.md; do
  [[ -f "$PUBLIC_EXPORT_SRC/$required" ]] || die "missing $PUBLIC_EXPORT_SRC/$required"
done

cp "$PUBLIC_EXPORT_SRC/AGENTS.md" "$OUT/AGENTS.md"
cp "$PUBLIC_EXPORT_SRC/CONTRIBUTING-BEGINNERS.md" "$OUT/CONTRIBUTING-BEGINNERS.md"
cp "$PUBLIC_EXPORT_SRC/EVALUATION.md" "$OUT/EVALUATION.md"
mkdir -p "$OUT/docs"
cp "$PUBLIC_EXPORT_SRC/LUMOGIS_AGENT_ORIENTATION.md" "$OUT/docs/LUMOGIS_AGENT_ORIENTATION.md"

CONTRIB="$OUT/CONTRIBUTING.md"
[[ -f "$CONTRIB" ]] || die "export tree missing CONTRIBUTING.md"
python3 - "$CONTRIB" "$PUBLIC_EXPORT_SRC/contributing-ai-agents.md" <<'PY'
import sys
from pathlib import Path

contrib = Path(sys.argv[1])
snippet = Path(sys.argv[2]).read_text(encoding="utf-8").strip() + "\n"
text = contrib.read_text(encoding="utf-8")
start_marker = "## AI assistants and IDE agents\n"
end_marker = "## Public CI parity (OpenAPI)\n"
i = text.find(start_marker)
j = text.find(end_marker)
if i == -1 or j == -1 or j <= i:
    sys.exit("CONTRIBUTING.md AI section markers not found for public export patch")
contrib.write_text(text[: i + len(start_marker)] + "\n" + snippet + "\n" + text[j:], encoding="utf-8")
PY

echo "create-upstream-export-tree: $OUT"
echo "create-upstream-export-tree: top-level:"
ls -la "$OUT"
