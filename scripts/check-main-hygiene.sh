#!/usr/bin/env bash
# Verify private `main` is suitable as a release-candidate before exporting
# a public AGPL snapshot. Intended for a checkout of the private repository.
#
# Usage: from repo root (or anywhere): scripts/check-main-hygiene.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

die() { echo "check-main-hygiene: FAIL: $*" >&2; exit 1; }

# Basename `.env.example` (any directory) is the only tracked dotenv we allow.
# Forbid `.env`, `.env.*`, `*/.env`, `*/.env.*` otherwise.
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

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  die "not a git repository"
fi

current_branch="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || true)"
if [[ "$current_branch" != "main" ]]; then
  echo "check-main-hygiene: warning: branch is '$current_branch', not 'main' (checks still run)." >&2
fi

# --- Project license header (AGPL-3.0-only; no project SPDX or-later)
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

check_license_file "$ROOT/LICENSE"

# --- Stale SPDX *-or-later in source headers / package metadata (line-anchored)
SPDX_OR_LATER='SPDX-License-Identifier:[[:space:]]*(AGPL|GPL)-3\.0-or-later'
stale_spdx=0
while IFS= read -r op; do
  out="$(git grep -nE "$op" -- ':*.py' ':*.sh' ':*.sql' ':Dockerfile' ':*.containerfile' 2>/dev/null || true)"
  [[ -n "$out" ]] && echo "$out" >&2 && stale_spdx=1
done <<'PAT'
^[[:space:]]*#[[:space:]]*SPDX-License-Identifier:[[:space:]]*(AGPL|GPL)-3\.0-or-later
PAT
while IFS= read -r op; do
  out="$(git grep -nE "$op" -- ':*.ts' ':*.tsx' ':*.js' ':*.mjs' ':*.cjs' 2>/dev/null || true)"
  [[ -n "$out" ]] && echo "$out" >&2 && stale_spdx=1
done <<'PAT'
^[[:space:]]*//[[:space:]]*SPDX-License-Identifier:[[:space:]]*(AGPL|GPL)-3\.0-or-later
PAT
out="$(git grep -nE '^[[:space:]]*--[[:space:]]*'"$SPDX_OR_LATER" -- ':*.sql' 2>/dev/null || true)"
[[ -n "$out" ]] && echo "$out" >&2 && stale_spdx=1
while IFS= read -r f; do
  o="$(grep -nE '"license"[[:space:]]*:[[:space:]]*"(AGPL|GPL)-3\.0-or-later"' "$f" 2>/dev/null || true)"
  if [[ -n "$o" ]]; then
    echo "$o" | sed "s|^|$f:|" >&2
    stale_spdx=1
  fi
done < <(git ls-files | grep -E '(^|/)package(-lock)?\.json$')
out="$(git grep -nE '^[[:space:]]*license[[:space:]]*=[[:space:]]*"?(AGPL|GPL)-3\.0-or-later"?[[:space:]]*$' -- ':*.toml' 2>/dev/null || true)"
[[ -n "$out" ]] && echo "$out" >&2 && stale_spdx=1

if [[ "$stale_spdx" -ne 0 ]]; then
  die "stale SPDX *-or-later in source or package metadata — use AGPL-3.0-only before export"
fi

# --- Paths that must not be tracked on release-candidate main
bad_paths=0
while IFS= read -r f; do
  [[ -z "$f" ]] && continue
  case "$f" in
    .cursor/*|.cursor)
      echo "check-main-hygiene: tracked forbidden path: $f" >&2
      bad_paths=1
      ;;
    .claude/*|.claude)
      echo "check-main-hygiene: tracked forbidden path: $f" >&2
      bad_paths=1
      ;;
    docs/private/*)
      case "$f" in
        docs/private/roadmap/*) ;; # tracked maintainer roadmap notes; omitted from public export
        *)
          echo "check-main-hygiene: tracked forbidden path: $f" >&2
          bad_paths=1
          ;;
      esac
      ;;
    *__pycache__/*|__pycache__/*|.pytest_cache/*|node_modules/*|*.pyc)
      echo "check-main-hygiene: tracked generated or dependency path: $f" >&2
      bad_paths=1
      ;;
  esac
  if dotenv_basename_forbidden "$f"; then
    echo "check-main-hygiene: tracked forbidden dotenv file: $f (allow only .env.example)" >&2
    bad_paths=1
  fi
done < <(git ls-files)

if [[ "$bad_paths" -ne 0 ]]; then
  die "forbidden or generated paths are tracked — remove from main before public export"
fi

# --- High-confidence secret patterns in tracked content (narrow file types)
# Exclude markdown via pathspec; long-form examples sometimes appear in docs.
secret_hit=0
SECRET_PATHS=(
  ':*.py' ':*.toml' ':*.cfg' ':*.ini'
  ':*.yml' ':*.yaml' ':*.json'
  ':*.ts' ':*.tsx' ':*.js' ':*.mjs' ':*.sh'
  ':Dockerfile' ':*.containerfile'
)
scan_secret() {
  local pattern="$1"
  local out
  out="$(git grep -nE "$pattern" -- "${SECRET_PATHS[@]}" ':(exclude)*.md' 2>/dev/null || true)"
  out="$(echo "$out" | grep -v '^Binary file' || true)"
  [[ -z "$out" ]] && return 0
  echo "$out" >&2
  return 1
}

if ! scan_secret 'AKIA[0-9A-Z]{16}'; then secret_hit=1; fi
if ! scan_secret 'BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY'; then secret_hit=1; fi
if ! scan_secret 'ghp_[A-Za-z0-9]{36}|xox[baprs]-[A-Za-z0-9-]{10,}'; then secret_hit=1; fi

if [[ "$secret_hit" -ne 0 ]]; then
  die "possible secrets matched in tracked files (see output above)"
fi

echo "check-main-hygiene.sh: OK"
