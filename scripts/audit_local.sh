#!/usr/bin/env bash
# Local dependency vulnerability audit (npm audit + pip-audit). No paid services.
# Requires: python3, npm when auditing JS (Node ≥ 20 per web client). Network for advisory DBs.
#
# Optional env:
#   AUDIT_SKIP_NPM=1 — pip-audit only.
#   AUDIT_SKIP_PIP=1 — npm audit only.
#   PIP_AUDIT_VENV — override bootstrap venv when pip-audit is not on PATH (default: .venv-audit/).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

PIP_AUDIT_VENV="${PIP_AUDIT_VENV:-$ROOT/.venv-audit}"

die() {
  printf '%s\n' "$*" >&2
  exit 1
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "audit_local: missing required command: $1"
}

ensure_pip_audit() {
  if command -v pip-audit >/dev/null 2>&1; then
    PIP_AUDIT_BIN=(pip-audit)
    return
  fi
  require_cmd python3
  if [[ ! -x "$PIP_AUDIT_VENV/bin/pip-audit" ]]; then
    python3 -m venv "$PIP_AUDIT_VENV"
    "$PIP_AUDIT_VENV/bin/pip" install -q pip-audit
  fi
  PIP_AUDIT_BIN=("$PIP_AUDIT_VENV/bin/pip-audit")
}

run_npm_audit() {
  local dir="$1"
  local label="$2"
  [[ -f "$dir/package.json" ]] || return 0
  if [[ ! -f "$dir/package-lock.json" ]]; then
    printf 'audit_local: skip npm (%s): no package-lock.json\n' "$label" >&2
    return 0
  fi
  printf '\n=== npm audit: %s ===\n' "$label"
  (cd "$dir" && npm audit)
}

run_pip_audit_files() {
  ensure_pip_audit
  local files=()
  while IFS= read -r -d '' f; do
    files+=("$f")
  done < <(find "$ROOT" \
    \( -name '.venv-audit' -o -name '.venv' -o -name 'venv' \
       -o -name 'node_modules' -o -name '__pycache__' \) -type d -prune -o \
    -type f -name 'requirements*.txt' -print0)
  if [[ "${#files[@]}" -eq 0 ]]; then
    die 'audit_local: no requirements*.txt files found'
  fi
  local req
  for req in "${files[@]}"; do
    printf '\n=== pip-audit: %s ===\n' "${req#$ROOT/}"
    "${PIP_AUDIT_BIN[@]}" --progress-spinner off -r "$req"
  done
}

main() {
  printf 'audit_local: repo root %s\n' "$ROOT"

  if [[ "${AUDIT_SKIP_NPM:-}" != "1" ]]; then
    require_cmd npm
    run_npm_audit "$ROOT/clients/lumogis-web" 'clients/lumogis-web'
  else
    printf 'audit_local: AUDIT_SKIP_NPM=1 — skipping npm audit\n'
  fi

  if [[ "${AUDIT_SKIP_PIP:-}" != "1" ]]; then
    run_pip_audit_files
  else
    printf 'audit_local: AUDIT_SKIP_PIP=1 — skipping pip-audit\n'
  fi

  printf '\naudit_local: OK (no failures)\n'
}

main "$@"
