#!/usr/bin/env bash
# Local dependency vulnerability audit (npm audit + pip-audit). No paid services.
# Requires: python3, npm when auditing JS (Node ≥ 20 per web client). Network for advisory DBs.
#
# Optional env:
#   AUDIT_SKIP_NPM=1 — pip-audit only.
#   AUDIT_SKIP_PIP=1 — npm audit only.
#   PIP_AUDIT_VENV — override bootstrap venv when pip-audit is not on PATH (default: .venv-audit/).
#
# Both halves always run (LUM-873): a failing npm half does not hide the pip half, and a failing
# requirements file does not hide the next one. Each half's result is printed in a summary, and
# the script exits 1 at the end if any half failed.
#
# pip-audit is bootstrapped from the hashed lock scripts/requirements-security-audit.txt with
# --require-hashes (ADR-097 convention; inputs and the recompile command are in
# scripts/requirements-security-audit.in). The venv is rebuilt when the lock changes.
#
# npm allowlist (narrow, fail-closed, expiring): the table lives in scripts/npm_audit_allowlist.py
# (ALLOWLIST). Currently one entry:
#   GHSA-gcqx-w48m-w2h4 — deepmerge <= 4.3.1 (no fixed release exists). Build-time only, dev-only,
#   reached through @rollup/plugin-node-resolve; never imported from clients/lumogis-web/src.
#   review_by 2027-02-04: past that date the gate fails until the entry is re-checked and edited.
#   Remove it once a deepmerge > 4.3.1 is published or the rollup plugin drops deepmerge.
# Every entry must carry review_by; do not add inline gating code to this script.
#
# npm overrides in clients/lumogis-web/package.json (JSON cannot hold comments, so the removal
# conditions are recorded here). Each is a scoped caret range; never add a top-level "glob" or
# "minimatch" override (it breaks filelist / glob@7 and empties the PWA precache, workbox#3594).
# When removing one, also drop its stale nested package-lock.json entries and re-check the precache.
#   js-yaml            — keep >= 4.3.2 (fix for GHSA-2883-xcg3-v3hh); remove when no dependency pins older.
#   workbox-build>glob — remove when workbox-build ships with glob >= 14 (workbox #3599).
#   typescript-estree>minimatch — remove when typescript-estree declares minimatch ^11
#     (typescript-eslint #13145).
#   eslint>minimatch, @eslint/config-array>minimatch — remove when they declare minimatch ^11
#     (ESLint #21463).
# react-router 8 migration needs Node >= 22.22, tracked with the Node-baseline follow-up.
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
  else
    require_cmd python3
    local lock="$ROOT/scripts/requirements-security-audit.txt"
    local stamp="$PIP_AUDIT_VENV/.lock-sha256"
    local want
    [[ -f "$lock" ]] || die "audit_local: missing $lock"
    want="$(sha256sum "$lock" | cut -d' ' -f1)"
    if [[ ! -x "$PIP_AUDIT_VENV/bin/pip-audit" ]] || [[ "$(cat "$stamp" 2>/dev/null)" != "$want" ]]; then
      printf 'audit_local: bootstrapping pip-audit into %s from the hashed lock\n' "$PIP_AUDIT_VENV"
      rm -rf "$PIP_AUDIT_VENV"
      python3 -m venv "$PIP_AUDIT_VENV"
      # --require-hashes: every package, transitive ones included, must match the lock.
      "$PIP_AUDIT_VENV/bin/pip" install -q --require-hashes -r "$lock"
      printf '%s\n' "$want" >"$stamp"
    fi
    PIP_AUDIT_BIN=("$PIP_AUDIT_VENV/bin/pip-audit")
  fi
  printf 'audit_local: using %s (%s)\n' "${PIP_AUDIT_BIN[0]}" "$("${PIP_AUDIT_BIN[@]}" --version 2>&1)"
}

# Returns 0 if npm audit JSON has no remaining vulnerabilities after the allowlist.
# All gating logic lives in scripts/npm_audit_allowlist.py (notes go to stderr).
apply_npm_audit_allowlist() {
  local dir="$1"
  local json_file="$2"
  require_cmd python3
  python3 "$ROOT/scripts/npm_audit_allowlist.py" "$dir" "$json_file"
}

run_npm_audit() {
  local dir="$1"
  local label="$2"
  local json_file audit_rc
  [[ -f "$dir/package.json" ]] || return 0
  if [[ ! -f "$dir/package-lock.json" ]]; then
    printf 'audit_local: skip npm (%s): no package-lock.json\n' "$label" >&2
    return 0
  fi
  printf '\n=== npm audit: %s ===\n' "$label"
  json_file="$(mktemp)"
  # shellcheck disable=SC2064
  trap "rm -f '$json_file'" RETURN

  set +e
  (cd "$dir" && npm audit --json >"$json_file")
  audit_rc=$?
  # Human-readable report for operators/CI logs (not the gate).
  (cd "$dir" && npm audit)
  set -e

  if [[ "$audit_rc" -eq 0 ]]; then
    return 0
  fi

  apply_npm_audit_allowlist "$dir" "$json_file"
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
  local req rel failed=()
  for req in "${files[@]}"; do
    rel="${req#"$ROOT"/}"
    printf '\n=== pip-audit: %s ===\n' "$rel"
    if ! "${PIP_AUDIT_BIN[@]}" --progress-spinner off -r "$req"; then
      failed+=("$rel")
    fi
  done
  if [[ "${#failed[@]}" -gt 0 ]]; then
    printf 'audit_local: pip-audit failed for: %s\n' "${failed[*]}" >&2
    return 1
  fi
}

npm_half() {
  require_cmd npm
  run_npm_audit "$ROOT/clients/lumogis-web" 'clients/lumogis-web'
}

# Runs one half in a subshell with errexit on, so a failure inside it ends that half only.
# Sets HALF_RESULT to OK, FAIL (exit N) or SKIPPED.
run_half() {
  local name="$1" skip_var="$2" fn="$3" rc
  if [[ "${!skip_var:-}" == "1" ]]; then
    printf 'audit_local: %s=1 — skipping %s\n' "$skip_var" "$name"
    HALF_RESULT=SKIPPED
    return
  fi
  set +e
  (set -e; "$fn")
  rc=$?
  set -e
  if [[ "$rc" -eq 0 ]]; then
    HALF_RESULT=OK
  else
    HALF_RESULT="FAIL (exit $rc)"
  fi
}

main() {
  local npm_result pip_result
  printf 'audit_local: repo root %s\n' "$ROOT"

  run_half 'npm audit' AUDIT_SKIP_NPM npm_half
  npm_result="$HALF_RESULT"
  run_half 'pip-audit' AUDIT_SKIP_PIP run_pip_audit_files
  pip_result="$HALF_RESULT"

  printf '\n=== audit_local summary ===\n'
  printf 'npm audit: %s\n' "$npm_result"
  printf 'pip-audit: %s\n' "$pip_result"

  if [[ "$npm_result" == FAIL* || "$pip_result" == FAIL* ]]; then
    printf '\naudit_local: FAILED\n' >&2
    exit 1
  fi
  printf '\naudit_local: OK (no failures)\n'
}

main "$@"
