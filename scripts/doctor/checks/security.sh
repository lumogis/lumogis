#!/usr/bin/env bash
# Lumogis doctor — security category (LUM-199). Prints TSV rows; always exits 0.
set -euo pipefail

ROOT="${LUMOGIS_REPO_ROOT:?}"
SEC="${DOCTOR_SECURITY:-0}"
STRICT="${LUMOGIS_DOCTOR_SECURITY_STRICT:-0}"
PYTHON="${PYTHON:-python3}"

_snt() {
  printf '%s' "$1" | tr '\t\r\n' '   '
}

emit() {
  # $1 name $2 status $3 message $4 remediation
  printf 'security\t%s\t%s\t%s\t%s\n' "$1" "$2" "$(_snt "$3")" "$(_snt "$4")"
}

if [ "$SEC" != "1" ]; then
  emit "security-category" "skipped" "Security checks disabled (default)" "Run make doctor ARGS=\"--security\" or set LUMOGIS_DOCTOR_RUN_SECURITY=1 (see scripts/doctor/README.md)."
  exit 0
fi

_audit_timeout_is_error=0
_audit_fail_is_error=0
_bandit_timeout_is_error=0
if [ "$STRICT" = "1" ]; then
  _audit_timeout_is_error=1
  _audit_fail_is_error=1
  _bandit_timeout_is_error=1
fi

cd "$ROOT"

# --- audit-local (make) ---
set +e
timeout 120s make audit-local >/dev/null 2>&1
ec=$?
set -e

if [ "$ec" -eq 124 ]; then
  if [ "$_audit_timeout_is_error" = "1" ]; then
    emit "audit-local" "error" "make audit-local exceeded 120s timeout" "Retry when npm/pip caches are warm; see scripts/doctor/README.md cold-cache note."
  else
    emit "audit-local" "warn" "make audit-local exceeded 120s timeout" "Retry when npm/pip caches are warm; set LUMOGIS_DOCTOR_SECURITY_STRICT=1 to treat as error."
  fi
elif [ "$ec" -ne 0 ]; then
  if [ "$_audit_fail_is_error" = "1" ]; then
    emit "audit-local" "error" "make audit-local exited non-zero" "Inspect scripts/audit_local.sh output locally; fix advisories or defer with explicit policy."
  else
    emit "audit-local" "warn" "make audit-local exited non-zero" "Inspect scripts/audit_local.sh; set LUMOGIS_DOCTOR_SECURITY_STRICT=1 to treat as error."
  fi
else
  emit "audit-local" "ok" "make audit-local completed" ""
fi

# --- bandit direct (same venv bootstrap as Makefile bandit-check) ---
BENV="$ROOT/.venv-bandit-check"
BEXE="$BENV/bin/bandit"
if [ ! -x "$BEXE" ]; then
  "$PYTHON" -m venv "$BENV"
  "$BENV/bin/pip" install -q -r "$ROOT/scripts/requirements-security-audit.txt"
fi

set +e
timeout 120s "$BEXE" -r "$ROOT/orchestrator/" -ll -ii >/dev/null 2>&1
bec=$?
set -e

if [ "$bec" -eq 124 ]; then
  if [ "$_bandit_timeout_is_error" = "1" ]; then
    emit "bandit" "error" "bandit exceeded 120s timeout" "Investigate orchestrator size or rerun; see scripts/doctor/README.md."
  else
    emit "bandit" "warn" "bandit exceeded 120s timeout" "Investigate orchestrator size or rerun; set LUMOGIS_DOCTOR_SECURITY_STRICT=1 to treat as error."
  fi
elif [ "$bec" -ne 0 ]; then
  emit "bandit" "error" "bandit reported findings or failed (non-zero exit)" "Run make bandit-check locally and fix issues (see docs/security-audit/)."
else
  emit "bandit" "ok" "bandit completed with no high-severity findings (flags -ll -ii)" ""
fi

exit 0
