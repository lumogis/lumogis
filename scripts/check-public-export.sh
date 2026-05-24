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

assert_strip_list_paths_absent() {
  local root="$1"
  local listf="$SCRIPT_DIR/public-export-strip-list.txt"
  [[ -f "$listf" ]] || die "missing public strip list: $listf"
  while IFS= read -r raw_line || [[ -n "${raw_line}" ]]; do
    local line="${raw_line%%#*}"
    line="$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    [[ -z "$line" ]] && continue
    if [[ -e "$root/$line" ]]; then
      die "forbidden path in export candidate: ${line} (see scripts/public-export-strip-list.txt)"
    fi
  done <"$listf"
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
  local listf="$SCRIPT_DIR/public-export-strip-list.txt"
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

  [[ -f "$listf" ]] || die "openapi-ci-export-contract (LUM-303): missing strip list: $listf"
  while IFS= read -r raw_line || [[ -n "${raw_line}" ]]; do
    local line="${raw_line%%#*}"
    line="$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    [[ -z "$line" ]] && continue
    local p
    for p in "${lum303_paths[@]}"; do
      if [[ "$line" == "$p" ]]; then
        echo "openapi-ci-export-contract LUM-303: strip list entry intersects required OpenAPI CI path: $line" >&2
        die "openapi-ci-export-contract (LUM-303): strip list intersects openapi-ci export contract paths"
      fi
    done
  done <"$listf"

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

assert_openapi_ci_export_contract "$TARGET"

echo "check-public-export.sh: OK ($TARGET)"
