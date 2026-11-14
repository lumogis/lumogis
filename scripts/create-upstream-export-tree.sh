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

# --- Strip lists (LUM-898) ---
# The main list ships in the public tree. The local list, when present, holds the
# maintainer-only entries; it is on the main list, so it never ships itself. Both are
# read once, main first, into STRIP_ENTRIES, before anything is written. Fail closed:
# a missing main list dies, and so does a missing local list when the source checkout
# (working tree or HEAD) holds a tree the local list exists to strip. That tree's names
# are assembled from parts so this script never carries the literals.
# Keep in step with load_strip_lists in check-public-export.sh.
STRIP_LIST="$SCRIPT_DIR/public-export-strip-list.txt"
STRIP_LIST_LOCAL="$SCRIPT_DIR/public-export-strip-list.local.txt"
STRIP_ENTRIES=()

_strip_list_lines() { # <file>: print each entry, comments and blank lines dropped
  local raw_line line
  while IFS= read -r raw_line || [[ -n "${raw_line}" ]]; do
    line="${raw_line%%#*}"
    line="$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    [[ -z "$line" ]] && continue
    printf '%s\n' "$line"
  done <"$1"
}

load_strip_lists() {
  local srv mk line
  [[ -f "$STRIP_LIST" ]] || die "missing strip list: $STRIP_LIST"
  while IFS= read -r line; do STRIP_ENTRIES+=("$line"); done < <(_strip_list_lines "$STRIP_LIST")
  if [[ -f "$STRIP_LIST_LOCAL" ]]; then
    while IFS= read -r line; do STRIP_ENTRIES+=("$line"); done < <(_strip_list_lines "$STRIP_LIST_LOCAL")
    return 0
  fi
  srv="apps/lumogis-""server"
  mk="Makefile.""server"".mk"
  if [[ -e "$ROOT/$srv" || -e "$ROOT/$mk" ]] \
    || git -C "$ROOT" cat-file -e "HEAD:$srv" 2>/dev/null \
    || git -C "$ROOT" cat-file -e "HEAD:$mk" 2>/dev/null; then
    die "source checkout has maintainer-only paths but no local strip list: $STRIP_LIST_LOCAL (refusing to export with the main list alone)"
  fi
}

load_strip_lists

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

# Public-shaped paths — the entries loaded above; check-public-export.sh reads the same lists
for line in ${STRIP_ENTRIES[@]+"${STRIP_ENTRIES[@]}"}; do
  target="${OUT}/${line}"
  # -e alone is false for a dangling symlink, which would leave a stripped path
  # in the export tree (e.g. .cursor -> a devtools checkout absent on this host).
  if [[ -e "$target" || -L "$target" ]]; then
    rm -rf "$target"
  fi
done

# Public CI runners — private CI runs on a maintainer-hosted runner
# (`runs-on: [self-hosted, <label>]`). That label must NEVER reach the public
# repo: the runner is not attached there (every job would queue forever), and a
# self-hosted runner reachable from a public repo lets any fork PR execute code on
# the maintainer's machine. Rewrite to the GitHub-hosted runner on export.
# `check-public-export.sh` asserts the result fail-closed.
WORKFLOW_DIR="$OUT/.github/workflows"
if [[ -d "$WORKFLOW_DIR" ]]; then
  while IFS= read -r -d '' wf; do
    python3 - "$wf" <<'PY'
import re, sys
from pathlib import Path
p = Path(sys.argv[1])
text = p.read_text(encoding="utf-8")
# Matches `runs-on: [self-hosted, <label>]`, `runs-on: [ self-hosted ]`, and the
# block-sequence form. Any runs-on naming self-hosted collapses to ubuntu-latest.
new = re.sub(
    r"(?mi)^([ \t]*)runs-on:[ \t]*\[[^\]]*self-hosted[^\]]*\][ \t]*(#.*)?$",
    r"\1runs-on: ubuntu-latest",
    text,
)
# block sequence: `runs-on:` followed by `- <label>` items at ANY indentation
# (items at the SAME indent as the key are valid YAML and were missed before).
new = re.sub(
    r"(?mi)^([ \t]*)runs-on:[ \t]*(#.*)?\n((?:[ \t]*-[ \t]*[^\n]*\n)+)",
    lambda m: (m.group(1) + "runs-on: ubuntu-latest\n") if "self-hosted" in m.group(3).lower() else m.group(0),
    new,
)
# scalar form: `runs-on: self-hosted` (any case, optional quotes/comment)
new = re.sub(
    r"(?mi)^([ \t]*)runs-on:[ \t]*[\"\']?self-hosted[\"\']?[ \t]*(#.*)?$",
    r"\1runs-on: ubuntu-latest",
    new,
)
new = re.sub(
    r"(?ms)^([ \t]*)runs-on:[ \t]*\n(?:[ \t]*-[ \t]*[^\n]*\n)*?[ \t]*-[ \t]*self-hosted[ \t]*\n(?:[ \t]*-[ \t]*[^\n]*\n)*",
    r"\1runs-on: ubuntu-latest\n",
    new,
)
if new != text:
    p.write_text(new, encoding="utf-8")
    print(f"create-upstream-export-tree: rewrote self-hosted runner in {p.name}")
PY
  done < <(find "$WORKFLOW_DIR" -type f \( -name '*.yml' -o -name '*.yaml' \) -print0)
fi

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
