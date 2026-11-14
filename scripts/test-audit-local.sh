#!/usr/bin/env bash
# LUM-873 — contract tests for audit_local.sh (stub npm + pip-audit on PATH, no network).
#
# The case that matters is red npm + red pip: before LUM-873 the script exited on the
# first failing half under set -e, so a red pip half went unseen for three months.
# Every case checks the exit code and that each half's result is reported.
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
PASSED=0

fail() {
  printf 'test-audit-local: FAIL: %s\n' "$*" >&2
  exit 1
}

# Builds a synthetic repo around a copy of audit_local.sh and runs it.
#   $1 expected exit code
#   $2 label
# Env knobs consumed by the stubs:
#   NPM_RED=1  — stub `npm audit --json` reports a non-allowlisted advisory (real allowlist gates it)
#   PIP_RED    — space-separated requirements basenames the stub pip-audit fails on
# Remaining args are passed to audit_local.sh's environment as NAME=VALUE.
# Leaves the combined output in $OUT.
run_case() {
  local expect="$1" label="$2"
  shift 2
  local tmp got
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' RETURN

  mkdir -p "$tmp/repo/scripts" "$tmp/repo/clients/lumogis-web" "$tmp/repo/svc" "$tmp/bin"
  cp "$SRC/audit_local.sh" "$SRC/npm_audit_allowlist.py" "$tmp/repo/scripts/"
  echo '{"name":"stub","version":"0.0.0"}' >"$tmp/repo/clients/lumogis-web/package.json"
  echo '{"lockfileVersion":3,"packages":{}}' >"$tmp/repo/clients/lumogis-web/package-lock.json"
  echo 'example==1.0' >"$tmp/repo/requirements-a.txt"
  echo 'example==1.0' >"$tmp/repo/svc/requirements-b.txt"

  cat >"$tmp/bin/npm" <<'EOF'
#!/usr/bin/env bash
[[ "$1" == "audit" ]] || { echo "stub npm: unexpected args $*" >&2; exit 2; }
if [[ "${2:-}" == "--json" ]]; then
  if [[ "${NPM_RED:-}" == "1" ]]; then
    echo '{"auditReportVersion":2,"vulnerabilities":{"evil":{"name":"evil","via":[{"source":1,"name":"evil","dependency":"evil","title":"t","url":"https://github.com/advisories/GHSA-xxxx-yyyy-zzzz","severity":"high"}]}}}'
    exit 1
  fi
  echo '{"auditReportVersion":2,"vulnerabilities":{}}'
  exit 0
fi
echo "stub npm audit report"
[[ "${NPM_RED:-}" == "1" ]] && exit 1
exit 0
EOF

  cat >"$tmp/bin/pip-audit" <<'EOF'
#!/usr/bin/env bash
if [[ "${1:-}" == "--version" ]]; then echo "pip-audit 0.0.0-stub"; exit 0; fi
req=""
while [[ $# -gt 0 ]]; do
  [[ "$1" == "-r" ]] && req="$2"
  shift
done
name="$(basename "$req")"
echo "stub pip-audit: $name"
for red in ${PIP_RED:-}; do
  if [[ "$red" == "$name" ]]; then
    echo "stub pip-audit: vulnerability found in $name"
    exit 1
  fi
done
echo "No known vulnerabilities found"
EOF
  chmod +x "$tmp/bin/npm" "$tmp/bin/pip-audit"

  set +e
  OUT="$(env PATH="$tmp/bin:$PATH" "$@" bash "$tmp/repo/scripts/audit_local.sh" 2>&1)"
  got=$?
  set -e

  [[ "$got" -eq "$expect" ]] || fail "$label: expected exit $expect, got $got. Output:
$OUT"
  PASSED=$((PASSED + 1))
  printf 'test-audit-local: ok — %s (exit %s)\n' "$label" "$got"
}

expect_line() {
  local label="$1" pattern="$2"
  grep -qE "$pattern" <<<"$OUT" || fail "$label: output lacks /$pattern/. Output:
$OUT"
}

run_case 0 "green npm + green pip"
expect_line "green/green" '^npm audit: OK$'
expect_line "green/green" '^pip-audit: OK$'
expect_line "green/green" 'audit_local: OK \(no failures\)'

run_case 1 "red npm + green pip" NPM_RED=1
expect_line "red/green" '^npm audit: FAIL'
expect_line "red/green" '^pip-audit: OK$'
expect_line "red/green" 'stub pip-audit: requirements-a.txt'
expect_line "red/green" 'audit_local: FAILED'

run_case 1 "green npm + red pip" PIP_RED=requirements-a.txt
expect_line "green/red" '^npm audit: OK$'
expect_line "green/red" '^pip-audit: FAIL'
expect_line "green/red" 'audit_local: FAILED'

# The LUM-873 regression: both halves red, both reported.
run_case 1 "red npm + red pip" NPM_RED=1 PIP_RED=requirements-a.txt
expect_line "red/red" 'still reports vulnerabilities after allowlist: evil'
expect_line "red/red" '=== pip-audit: requirements-a.txt ==='
expect_line "red/red" 'stub pip-audit: vulnerability found in requirements-a.txt'
expect_line "red/red" '^npm audit: FAIL'
expect_line "red/red" '^pip-audit: FAIL'
expect_line "red/red" 'audit_local: FAILED'

# A red requirements file does not stop the next one from being audited.
run_case 1 "red pip file still audits the rest" PIP_RED="requirements-a.txt requirements-b.txt"
expect_line "pip/all-files" '=== pip-audit: svc/requirements-b.txt ==='
expect_line "pip/all-files" 'pip-audit failed for: .*requirements-a.txt.*'
expect_line "pip/all-files" 'pip-audit failed for: .*requirements-b.txt'

run_case 0 "skipped npm + green pip" AUDIT_SKIP_NPM=1
expect_line "skip-npm" '^npm audit: SKIPPED$'
expect_line "skip-npm" '^pip-audit: OK$'

run_case 1 "skipped npm + red pip" AUDIT_SKIP_NPM=1 PIP_RED=requirements-b.txt
expect_line "skip-npm/red-pip" '^pip-audit: FAIL'

run_case 1 "red npm + skipped pip" NPM_RED=1 AUDIT_SKIP_PIP=1
expect_line "red-npm/skip-pip" '^npm audit: FAIL'
expect_line "red-npm/skip-pip" '^pip-audit: SKIPPED$'

printf 'test-audit-local: OK (%s cases)\n' "$PASSED"
