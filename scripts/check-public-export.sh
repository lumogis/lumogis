#!/usr/bin/env bash
# Verify a directory tree before it becomes a public Lumogis release.
# Run against the exact export root (no private .git history required).
#
# Usage: scripts/check-public-export.sh [DIR]
#        Default DIR is the current working directory.
#
set -euo pipefail

die() { echo "check-public-export: FAIL: $*" >&2; exit 1; }

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# --- Strip lists (LUM-898) ---
# The main list ships in the public tree. The local list, when present, holds the
# maintainer-only entries; it is on the main list, so it never ships itself. Both are
# read once, main first, into STRIP_ENTRIES; LOCAL_STRIP_ENTRIES keeps the local part.
# Fail closed: a missing main list dies, and so does a missing local list when the
# source checkout holds a tree the local list exists to strip. That tree's names are
# assembled from parts, as in gate 3, so this script never carries the literals.
STRIP_LIST="$SCRIPT_DIR/public-export-strip-list.txt"
STRIP_LIST_LOCAL="$SCRIPT_DIR/public-export-strip-list.local.txt"
STRIP_ENTRIES=()
LOCAL_STRIP_ENTRIES=()

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
  local src_root srv line
  [[ -f "$STRIP_LIST" ]] || die "missing public strip list: $STRIP_LIST"
  while IFS= read -r line; do STRIP_ENTRIES+=("$line"); done < <(_strip_list_lines "$STRIP_LIST")
  src_root="$(cd "$SCRIPT_DIR/.." && pwd)"
  srv="lumogis-""server"
  if [[ -f "$STRIP_LIST_LOCAL" ]]; then
    while IFS= read -r line; do LOCAL_STRIP_ENTRIES+=("$line"); done < <(_strip_list_lines "$STRIP_LIST_LOCAL")
    STRIP_ENTRIES+=(${LOCAL_STRIP_ENTRIES[@]+"${LOCAL_STRIP_ENTRIES[@]}"})
  elif [[ -e "$src_root/apps/$srv" || -e "$src_root/Makefile.""server"".mk" ]]; then
    die "source checkout has maintainer-only paths but no local strip list: $STRIP_LIST_LOCAL (refusing to check with the main list alone)"
  fi
}

load_strip_lists

assert_strip_list_paths_absent() {
  local root="$1" line
  for line in ${STRIP_ENTRIES[@]+"${STRIP_ENTRIES[@]}"}; do
    if [[ -e "$root/$line" || -L "$root/$line" ]]; then
      die "forbidden path in export candidate: ${line} (see the export strip lists in scripts/)"
    fi
  done
}
dotenv_basename_forbidden() {
  local base
  base="$(basename "$1")"
  case "$base" in
    .env.example)
      return 1
      ;;
    .env|.env.*)
      return 0
      ;;
    *)
      return 1
      ;;
  esac
}

TARGET="${1:-.}"
TARGET="$(cd "$TARGET" && pwd)"

assert_strip_list_paths_absent "$TARGET"

assert_export_has_no_apps_subtree() {
  local root="$1"
  local apps_dir="$root/apps"
  if [[ -d "$apps_dir" ]]; then
  if find "$apps_dir" -mindepth 1 -maxdepth 1 -print -quit 2>/dev/null | grep -q .; then
      die "export tree must not contain an apps/ subtree: $apps_dir"
    fi
  fi
}

assert_export_has_no_apps_subtree "$TARGET"

check_license_file() {
  local lic="$1"
  [[ -f "$lic" ]] || die "LICENSE missing at $lic"
  local head
  head="$(head -n 40 "$lic")"
  if ! echo "$head" | grep -qiE 'AGPL-3\.0-only'; then
    die "LICENSE first ~40 lines must mention AGPL-3.0-only (project identifier)"
  fi
  if grep -qE 'SPDX-License-Identifier:[[:space:]]*AGPL-3\.0-or-later|SPDX-License-Identifier:[[:space:]]*GPL-3\.0-or-later' "$lic"; then
    die "LICENSE must not contain SPDX ...-or-later for the project"
  fi
  if head -n 15 "$lic" | grep -qi 'any later version'; then
    die "LICENSE project header (first 15 lines) must not say 'any later version' — use v3.0-only wording"
  fi
}

check_license_file "$TARGET/LICENSE"

# --- No stale *-or-later in machine-readable SPDX headers (line-anchored;
# avoids prose in docs that mention "AGPL-3.0-or-later" in backticks).
SPDX_OR_LATER='SPDX-License-Identifier:[[:space:]]*(AGPL|GPL)-3\.0-or-later'
if grep -rInE "^[[:space:]]*(#[[:space:]]*|//[[:space:]]*|--[[:space:]]*)${SPDX_OR_LATER}" "$TARGET" \
  --include='*.py' --include='*.sh' --include='*.sql' --include='*.ts' --include='*.tsx' \
  --include='*.js' --include='*.mjs' --include='*.cjs' --include='Dockerfile' --include='*.containerfile' \
  --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=__pycache__ \
  --exclude-dir=.pytest_cache --exclude-dir=venv --exclude-dir=.venv \
  --exclude-dir=lumogis-data 2>/dev/null | grep -q .; then
  grep -rInE "^[[:space:]]*(#[[:space:]]*|//[[:space:]]*|--[[:space:]]*)${SPDX_OR_LATER}" "$TARGET" \
    --include='*.py' --include='*.sh' --include='*.sql' --include='*.ts' --include='*.tsx' \
    --include='*.js' --include='*.mjs' --include='*.cjs' --include='Dockerfile' --include='*.containerfile' \
    --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=__pycache__ \
    --exclude-dir=.pytest_cache --exclude-dir=venv --exclude-dir=.venv \
    --exclude-dir=lumogis-data 2>/dev/null || true
  die "export tree has SPDX *-or-later comment headers — use AGPL-3.0-only"
fi
if grep -rInE '"license"[[:space:]]*:[[:space:]]*"(AGPL|GPL)-3\.0-or-later"' "$TARGET" \
  --include='package.json' --include='package-lock.json' \
  --exclude-dir=.git 2>/dev/null | grep -q .; then
  grep -rInE '"license"[[:space:]]*:[[:space:]]*"(AGPL|GPL)-3\.0-or-later"' "$TARGET" \
    --include='package.json' --include='package-lock.json' \
    --exclude-dir=.git 2>/dev/null || true
  die "export tree package metadata uses *-or-later — use AGPL-3.0-only"
fi
if grep -rInE '^[[:space:]]*license[[:space:]]*=[[:space:]]*"?(AGPL|GPL)-3\.0-or-later"?[[:space:]]*$' "$TARGET" \
  --include='*.toml' --exclude-dir=.git 2>/dev/null | grep -q .; then
  grep -rInE '^[[:space:]]*license[[:space:]]*=[[:space:]]*"?(AGPL|GPL)-3\.0-or-later"?[[:space:]]*$' "$TARGET" \
    --include='*.toml' --exclude-dir=.git 2>/dev/null || true
  die "export tree TOML licence field uses *-or-later — use AGPL-3.0-only"
fi

# --- Personal-data denylist + runner allowlist (both in Python: see note) ---
# These two gates were hand-rolled in bash/grep twice and failed open twice:
#   1. `grep | head -5` returned 141 (SIGPIPE) under pipefail, silently skipping tokens.
#   2. A hand-rolled YAML walk missed a same-indentation block sequence
#      (`runs-on:` then `- "self-hosted"` at the SAME indent) — valid YAML, full bypass.
# Both now run in Python: tokens are matched with re.escape (so regex metacharacters in a
# token are literal), and workflows are parsed with a real YAML parser and walked
# recursively, so every spelling — flow, block at any indent, anchors/aliases, multi-doc,
# nested — reaches the same check. Missing PyYAML is a hard failure, not a skip.
python3 - "$TARGET" "$SCRIPT_DIR/public-export-personal-tokens.txt" <<'PYGATES' || die "export gate failed (personal tokens, runs-on or appliance path) — see output above"
import re, sys, pathlib

target = pathlib.Path(sys.argv[1])
token_list = pathlib.Path(sys.argv[2])
failures = []

# ---------- gate 1: personal-token content scan ----------
SCAN_SUFFIXES = {".jsonl", ".json", ".md", ".html", ".py", ".yml", ".yaml"}
SKIP_DIRS = {".git", "node_modules", "__pycache__", ".pytest_cache", "venv", ".venv"}

tokens = []
if token_list.is_file():
    for raw in token_list.read_text(encoding="utf-8-sig").splitlines():
        tok = raw.split("#", 1)[0].strip()
        if tok:
            tokens.append(tok)

if tokens:
    # re.escape makes metacharacters literal: a token like "project (aurora)" matches text,
    # not a regex group. Word boundaries are applied only where the token edge is a word
    # character, so a token ending in ")" still matches.
    pats = []
    for t in tokens:
        esc = re.escape(t)
        left = r"\b" if t[:1].isalnum() or t[:1] == "_" else ""
        right = r"\b" if t[-1:].isalnum() or t[-1:] == "_" else ""
        pats.append((t, re.compile(left + esc + right, re.IGNORECASE)))
    for f in target.rglob("*"):
        if not f.is_file() or f.is_symlink():
            continue
        if any(part in SKIP_DIRS for part in f.parts):
            continue
        if f.suffix.lower() not in SCAN_SUFFIXES:
            continue
        try:
            text = f.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for tok, pat in pats:
            m = pat.search(text)
            if m:
                line = text[: m.start()].count("\n") + 1
                failures.append(f"personal token {tok!r} in {f}:{line}")

# ---------- gate 2: runs-on allowlist ----------
ALLOWED = {
    "ubuntu-latest", "ubuntu-24.04", "ubuntu-22.04", "ubuntu-20.04",
    # GitHub-hosted arm64 Linux (free on public repos); the release smoke runs on it (LUM-997).
    "ubuntu-24.04-arm", "ubuntu-22.04-arm",
    "windows-latest", "windows-2025", "windows-2022", "windows-2019",
    "macos-latest", "macos-15", "macos-14", "macos-13",
}
gh = target / ".github"
if gh.is_dir():
    try:
        import yaml
    except ImportError:
        failures.append("PyYAML unavailable — cannot verify runs-on labels; refusing to pass")
        yaml = None
    if yaml is not None:
        def labels_of(node):
            """Yield every scalar label under a runs-on value, any shape."""
            if isinstance(node, str):
                yield node
            elif isinstance(node, list):
                for v in node:
                    yield from labels_of(v)
            elif isinstance(node, dict):
                for k, v in node.items():
                    if k == "labels":
                        yield from labels_of(v)
                    elif k == "group":
                        # A runner group implies a self-hosted or larger-runner pool,
                        # never a plain GitHub-hosted label. Surface it as a failure.
                        yield "__runner_group__"

        def walk(node, scope, path):
            # `scope` is the nearest enclosing mapping that carries the runs-on — the job.
            # Matrix resolution is scoped to it, never the whole document: round 5
            # reproduced a bypass where an unrelated job's static `os:` list masked a
            # `fromJSON` matrix in the job actually being checked.
            if isinstance(node, dict):
                if "runs-on" in node:
                    scope = node
                for k, v in node.items():
                    if k == "runs-on":
                        for lab in labels_of(v):
                            lab = str(lab).strip()
                            if lab.startswith("${{"):
                                mk = re.match(r"\$\{\{\s*matrix\.([\w-]+)\s*\}\}$", lab)
                                vals = resolve_matrix(scope, mk.group(1)) if mk else None
                                if not vals:
                                    failures.append(f"{path}: runs-on {lab!r} is not statically resolvable")
                                else:
                                    for v2 in vals:
                                        if str(v2).strip().lower() not in ALLOWED:
                                            failures.append(f"{path}: runs-on matrix value {v2!r} is not a known GitHub-hosted runner")
                            elif lab == "__runner_group__":
                                failures.append(f"{path}: runs-on names a runner `group:` — runner groups are self-hosted or larger-runner pools, never GitHub-hosted")
                            elif lab.lower() not in ALLOWED:
                                failures.append(f"{path}: runs-on label {lab!r} is not a known GitHub-hosted runner")
                    else:
                        walk(v, scope, path)
            elif isinstance(node, list):
                for v in node:
                    walk(v, scope, path)

        def resolve_matrix(doc, key):
            """Collect static values assigned to `key` under any strategy.matrix in the doc."""
            found = []
            def rec(n):
                if isinstance(n, dict):
                    mx = n.get("strategy", {}).get("matrix") if isinstance(n.get("strategy"), dict) else None
                    if isinstance(mx, dict):
                        if key in mx and isinstance(mx[key], list):
                            found.extend(mx[key])
                        inc = mx.get("include")
                        if isinstance(inc, list):
                            for item in inc:
                                if isinstance(item, dict) and key in item:
                                    found.append(item[key])
                    for v in n.values():
                        rec(v)
                elif isinstance(n, list):
                    for v in n:
                        rec(v)
            rec(doc)
            return [f for f in found if isinstance(f, (str, int, float))]

        for f in sorted(list(gh.rglob("*.yml")) + list(gh.rglob("*.yaml"))):
            try:
                docs = list(yaml.safe_load_all(f.read_text(encoding="utf-8")))
            except Exception as exc:
                failures.append(f"{f}: could not parse as YAML ({exc.__class__.__name__}) — refusing to pass")
                continue
            for doc in docs:
                walk(doc, doc, f)

# ---------- gate 3: appliance path scan, names and content (LUM-898) ----------
# Public-shipping files must never name the appliance source tree or its make include,
# in their relative path or in their content. No file is exempt: the main strip list
# names nothing maintainer-only, and the local list is stripped from every export.
# The needles are assembled from parts so this script and its tests, which ship in the
# public tree, never carry the literals themselves. Every file is scanned as bytes, any
# suffix, so a comment, a fixture or a built artefact counts the same as prose.
SRV = "lumogis-" + "server"
NEEDLES = (("apps/" + SRV).encode(), ("Makefile." + "server" + ".mk").encode())
named_paths = []
for f in sorted(target.rglob("*")):
    parts = f.relative_to(target).parts
    if any(part in SKIP_DIRS for part in parts):
        continue
    rel = f.relative_to(target).as_posix()
    # Path check: files, directories and symlinks alike. Report the top-most match only,
    # so one leaked tree is one failure rather than one per file inside it.
    if any(n in rel.encode() for n in NEEDLES):
        if not any(rel.startswith(p + "/") for p in named_paths):
            named_paths.append(rel)
            failures.append(f"appliance path in file name {rel} (LUM-898: public files must not name it)")
        continue
    if not f.is_file() or f.is_symlink():
        continue
    try:
        data = f.read_bytes()
    except OSError:
        continue
    for needle in NEEDLES:
        at = data.find(needle)
        if at != -1:
            line = data.count(b"\n", 0, at) + 1
            failures.append(f"appliance path named in {rel}:{line} (LUM-898: public files must not name it)")
            break

if failures:
    print("check-public-export: export gate failures:", file=sys.stderr)
    for x in failures[:40]:
        print("  " + x, file=sys.stderr)
    if len(failures) > 40:
        print(f"  … and {len(failures) - 40} more", file=sys.stderr)
    sys.exit(1)
PYGATES

# --- Forbidden paths inside the export (files and directories)
bad=0
while IFS= read -r -d '' f; do
  rel="${f#$TARGET/}"
  rel="${rel#/}"
  case "$rel" in
    .git/*|.git|*/.git/*|*/.git)
      continue
      ;;
  esac
  case "$rel" in
    .cursor|.cursor/*|*/.cursor|*/.cursor/*)
      echo "check-public-export: forbidden path: $rel" >&2
      bad=1
      ;;
    .claude|.claude/*|*/.claude|*/.claude/*)
      echo "check-public-export: forbidden path: $rel" >&2
      bad=1
      ;;
    docs/private/*|*/docs/private/*)
      echo "check-public-export: forbidden path: $rel" >&2
      bad=1
      ;;
    docs/release/*|docs/release|*/docs/release/*|*/docs/release)
      echo "check-public-export: forbidden path: $rel" >&2
      bad=1
      ;;
    docs/_librarian/*|docs/_librarian|*/docs/_librarian/*|*/docs/_librarian)
      echo "check-public-export: forbidden path: $rel" >&2
      bad=1
      ;;
    docs/development/local-ai-devtools.md|*/docs/development/local-ai-devtools.md)
      echo "check-public-export: forbidden path: $rel" >&2
      bad=1
      ;;
  esac
  base="$(basename "$rel")"
  case "$base" in
    id_rsa|id_ed25519|id_ecdsa|.DS_Store)
      echo "check-public-export: forbidden file: $rel" >&2
      bad=1
      ;;
  esac
  if dotenv_basename_forbidden "$rel"; then
    echo "check-public-export: forbidden dotenv file: $rel (allow only .env.example)" >&2
    bad=1
  fi
  case "$rel" in
    *__pycache__*|*.pyc|.pytest_cache/*|*node_modules*)
      echo "check-public-export: generated or dependency path in export: $rel" >&2
      bad=1
      ;;
  esac
done < <(find "$TARGET" \
  \( -name .git -o -name node_modules -o -name __pycache__ -o -name .pytest_cache \
     -o -name venv -o -name .venv -o -name lumogis-data \) -prune -o -print0)

if [[ "$bad" -ne 0 ]]; then
  die "export tree contains forbidden paths — fix staging directory"
fi

# --- Likely secrets (text-like extensions only; markdown excluded by --include)
scan_export_secret() {
  local pattern="$1"
  shift
  local out
  out="$(grep -rInE --binary-files=without-match "$pattern" "$@" 2>/dev/null || true)"
  [[ -z "$out" ]] && return 0
  echo "$out" >&2
  return 1
}

secret_hit=0
EXC=(
  --exclude-dir=.git --exclude-dir=node_modules --exclude-dir=__pycache__
  --exclude-dir=.pytest_cache --exclude-dir=venv --exclude-dir=.venv
  --exclude-dir=lumogis-data
)
if ! scan_export_secret 'AKIA[0-9A-Z]{16}' "$TARGET" \
  --include='*.py' --include='*.toml' --include='*.yml' --include='*.yaml' \
  --include='*.json' --include='*.ts' --include='*.tsx' --include='*.js' \
  --include='*.mjs' --include='*.sh' --include='Dockerfile' --include='*.containerfile' \
  "${EXC[@]}"; then
  secret_hit=1
fi
if ! scan_export_secret 'BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY' "$TARGET" \
  --include='*.py' --include='*.pem' --include='*.key' --include='*.crt' \
  "${EXC[@]}"; then
  secret_hit=1
fi
if ! scan_export_secret 'ghp_[A-Za-z0-9]{36}|xox[baprs]-[A-Za-z0-9-]{10,}' "$TARGET" \
  --include='*.py' --include='*.yml' --include='*.yaml' --include='*.json' --include='*.sh' \
  "${EXC[@]}"; then
  secret_hit=1
fi

if [[ "$secret_hit" -ne 0 ]]; then
  die "possible secrets matched in export tree (see output above)"
fi

# --- Required presence (LUM-303) ---
# Canonical OpenAPI CI export contract paths (keep in sync with
# orchestrator/tests/test_check_public_export_script.py and CONTRIBUTING.md):
#   1.  .github/workflows/ci.yml
#   2.  .github/scripts/openapi-check-paths.sh
#   3.  .github/scripts/openapi-breaking-check.sh
#   4.  Makefile
#   5.  orchestrator/scripts/dump_openapi.py
#   6.  clients/lumogis-web/openapi.snapshot.json
#   7.  clients/lumogis-web/scripts/codegen.mjs
#   8.  clients/lumogis-web/package.json
#   9.  clients/lumogis-web/package-lock.json
#   10. scripts/fixtures/openapi-breaking-check/base.json
#   11. scripts/fixtures/openapi-breaking-check/after-compatible.json
#   12. scripts/fixtures/openapi-breaking-check/after-breaking.json
assert_openapi_ci_export_contract() {
  local root="$1"
  local -a lum303_paths=(
    ".github/workflows/ci.yml"
    ".github/scripts/openapi-check-paths.sh"
    ".github/scripts/openapi-breaking-check.sh"
    "Makefile"
    "orchestrator/scripts/dump_openapi.py"
    "clients/lumogis-web/openapi.snapshot.json"
    "clients/lumogis-web/scripts/codegen.mjs"
    "clients/lumogis-web/package.json"
    "clients/lumogis-web/package-lock.json"
    "scripts/fixtures/openapi-breaking-check/base.json"
    "scripts/fixtures/openapi-breaking-check/after-compatible.json"
    "scripts/fixtures/openapi-breaking-check/after-breaking.json"
  )

  local line p
  for line in ${STRIP_ENTRIES[@]+"${STRIP_ENTRIES[@]}"}; do
    for p in "${lum303_paths[@]}"; do
      if [[ "$line" == "$p" ]]; then
        echo "openapi-ci-export-contract LUM-303: strip list entry intersects required OpenAPI CI path: $line" >&2
        die "openapi-ci-export-contract (LUM-303): strip list intersects openapi-ci export contract paths"
      fi
    done
  done

  for p in "${lum303_paths[@]}"; do
    if [[ ! -f "$root/$p" ]]; then
      echo "openapi-ci-export-contract LUM-303: missing required path in export tree: $p" >&2
      die "openapi-ci-export-contract (LUM-303): missing file $p"
    fi
  done

  local ciyml="$root/.github/workflows/ci.yml"
  if ! grep -qE '^  openapi-check:[[:space:]]*$' "$ciyml"; then
    echo "openapi-ci-export-contract LUM-303: .github/workflows/ci.yml must contain top-level job line '  openapi-check:'" >&2
    die "openapi-ci-export-contract (LUM-303): missing openapi-check job key in ci.yml"
  fi
}

# --- Required presence (LUM-433) ---
# Canonical Search overlay CI export contract path (keep in sync with
# orchestrator/tests/test_check_public_export_script.py and CONTRIBUTING.md):
#   1.  .github/workflows/search-overlay-build.yml
assert_search_overlay_ci_export_contract() {
  local root="$1"
  local workflow_path=".github/workflows/search-overlay-build.yml"

  if [[ ! -f "$root/$workflow_path" ]]; then
    echo "search-overlay-ci-export-contract LUM-433: missing required path in export tree: $workflow_path" >&2
    die "search-overlay-ci-export-contract (LUM-433): missing file $workflow_path"
  fi

  local line
  for line in ${STRIP_ENTRIES[@]+"${STRIP_ENTRIES[@]}"}; do
    if [[ "$line" == "$workflow_path" ]]; then
      echo "search-overlay-ci-export-contract LUM-433: strip list entry intersects required Search overlay CI path: $line" >&2
      die "search-overlay-ci-export-contract (LUM-433): strip list intersects search-overlay export contract path"
    fi
  done

  # The appliance name is assembled from parts so this script, which ships in the public
  # tree, never carries the literal that the LUM-898 content gate rejects.
  local srv="lumogis-""server"
  local -a forbidden=(apps/lumogis-hub "apps/${srv}" hub-build.yml lumogis-hub "${srv}")
  local pat
  for pat in "${forbidden[@]}"; do
    if grep -q "$pat" "$root/$workflow_path"; then
      echo "search-overlay-ci-export-contract LUM-433: forbidden reference in $workflow_path: $pat" >&2
      die "search-overlay-ci-export-contract (LUM-433): workflow must not reference maintainer-only paths"
    fi
  done
}

# --- Required absence: local strip list (LUM-492, LUM-898) ---
# When the local strip list is present, every path on it must be absent from the export
# tree, dangling symlinks included. assert_strip_list_paths_absent covers the same paths
# first; this contract keeps the local list's guarantee explicit and on its own. Keep in
# sync with orchestrator/tests/test_check_public_export_script.py.
assert_local_strip_list_contract() {
  local root="$1" p
  [[ -f "$STRIP_LIST_LOCAL" ]] || return 0
  for p in ${LOCAL_STRIP_ENTRIES[@]+"${LOCAL_STRIP_ENTRIES[@]}"}; do
    if [[ -e "$root/$p" || -L "$root/$p" ]]; then
      echo "local-strip-list-contract LUM-898: listed path present in export tree: $p" >&2
      die "local-strip-list-contract (LUM-898): a local strip-list path is in the export tree"
    fi
  done
}

assert_openapi_ci_export_contract "$TARGET"
assert_search_overlay_ci_export_contract "$TARGET"
assert_local_strip_list_contract "$TARGET"

# --- Public export doc hygiene (LUM-376 / LUM-378) ---
PUBLIC_EXPORT_FORBIDDEN_PATTERNS=(
  'lumogis-app'
  'lumogis-devtools'
  'LUMOGIS_CONTEXT_PACK'
  'linear\.app'
  '/linear-update'
  '/update-context-pack'
  '/navigator'
  '\.cursor/skills'
  'Product OS'
  'githubusercontent'
  'raw\.githubusercontent\.com'
)

_scan_public_export_forbidden_patterns() {
  local content="$1"
  local label="$2"
  local pat
  for pat in "${PUBLIC_EXPORT_FORBIDDEN_PATTERNS[@]}"; do
    if echo "$content" | grep -qiE "$pat"; then
      echo "check-public-export: forbidden pattern in ${label}: ${pat}" >&2
      return 1
    fi
  done
  return 0
}

assert_public_agent_orientation() {
  local root="$1"
  local -a required=(
    "AGENTS.md"
    "docs/LUMOGIS_AGENT_ORIENTATION.md"
  )
  local p
  for p in "${required[@]}"; do
    if [[ ! -f "$root/$p" ]]; then
      die "public agent orientation (LUM-376): missing required path: $p"
    fi
  done
  if [[ -e "$root/docs/LUMOGIS_CONTEXT_PACK.md" ]]; then
    die "public agent orientation (LUM-376): private context pack must not ship: docs/LUMOGIS_CONTEXT_PACK.md"
  fi
  if [[ -e "$root/docs/public-export" ]]; then
    die "public agent orientation (LUM-376): maintainer templates must not ship: docs/public-export"
  fi

  local combined
  combined="$(cat "$root/AGENTS.md" "$root/docs/LUMOGIS_AGENT_ORIENTATION.md")"
  if ! _scan_public_export_forbidden_patterns "$combined" "public agent docs (LUM-376)"; then
    die "public agent orientation (LUM-376): private maintainer leakage in AGENTS.md or LUMOGIS_AGENT_ORIENTATION.md"
  fi
}

# --- Required presence (LUM-378) ---
# Canonical beginners doc export contract (keep in sync with
# orchestrator/tests/test_check_public_export_script.py and CONTRIBUTING.md):
#   1.  CONTRIBUTING-BEGINNERS.md

assert_public_beginners_doc() {
  local root="$1"
  local beginners="$root/CONTRIBUTING-BEGINNERS.md"
  if [[ ! -f "$beginners" ]]; then
    die "public beginners doc (LUM-378): missing required path: CONTRIBUTING-BEGINNERS.md"
  fi
  local content
  content="$(cat "$beginners")"
  if ! _scan_public_export_forbidden_patterns "$content" "CONTRIBUTING-BEGINNERS.md (LUM-378)"; then
    die "public beginners doc (LUM-378): private maintainer leakage in CONTRIBUTING-BEGINNERS.md"
  fi
}

# --- Required presence (LUM-363) ---
# Canonical operator evaluation doc export contract (keep in sync with
# orchestrator/tests/test_check_public_export_script.py and README.md):
#   1.  EVALUATION.md

assert_public_evaluation_doc() {
  local root="$1"
  local evaluation="$root/EVALUATION.md"
  if [[ ! -f "$evaluation" ]]; then
    die "public evaluation doc (LUM-363): missing required path: EVALUATION.md"
  fi
  local content
  content="$(cat "$evaluation")"
  if ! _scan_public_export_forbidden_patterns "$content" "EVALUATION.md (LUM-363)"; then
    die "public evaluation doc (LUM-363): private maintainer leakage in EVALUATION.md"
  fi
}

assert_public_agent_orientation "$TARGET"
assert_public_beginners_doc "$TARGET"
assert_public_evaluation_doc "$TARGET"

echo "check-public-export.sh: OK ($TARGET)"
