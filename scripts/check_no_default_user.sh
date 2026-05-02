#!/usr/bin/env bash
# Phase 3 grep gate.
#
# Lumogis is moving from single-user-with-"default" to per-user
# isolation. Hot-path code must never silently fall back to the literal
# user_id "default" — every call must thread a real user_id all the way
# through. This script searches for the patterns that would re-introduce
# the bug and exits non-zero if any are found in code paths we have
# already migrated.
#
# We deliberately do NOT scan the entire repo: the database migrations
# still use 'default' as the legacy column default, the bootstrap admin
# row may carry it, and many tests rely on the string. Instead we scope
# the gate to the orchestrator + KG service code surface that Phase 3
# touches.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Patterns we forbid in production code under the scoped paths.
# We use POSIX ERE so this stays portable to mac/linux without a
# ripgrep dependency. Patterns are a superset to catch both kwarg
# defaults and runtime fallbacks.
PATTERNS=(
  'user_id[[:space:]]*=[[:space:]]*"default"'
  "user_id[[:space:]]*=[[:space:]]*'default'"
  'user_id:[[:space:]]*str[[:space:]]*=[[:space:]]*"default"'
  "user_id:[[:space:]]*str[[:space:]]*=[[:space:]]*'default'"
  'get\([[:space:]]*"user_id"[[:space:]]*,[[:space:]]*"default"[[:space:]]*\)'
)

# Scoped paths: only the modules Phase 3 has migrated. Adding more
# paths here as later phases land is the intended way to grow the
# gate.
PATHS=(
  "orchestrator/loop.py"
  "orchestrator/permissions.py"
  "orchestrator/mcp_server.py"
  "orchestrator/routes/chat.py"
  "orchestrator/routes/data.py"
  "orchestrator/routes/events.py"
  "orchestrator/routes/signals.py"
  "orchestrator/routes/actions.py"
  "orchestrator/services/tools.py"
  "orchestrator/services/ingest.py"
  "orchestrator/services/feedback.py"
  "orchestrator/services/routines.py"
  "orchestrator/actions/executor.py"
  "orchestrator/actions/audit.py"
  "orchestrator/actions/reversibility.py"
)

failed=0
for path in "${PATHS[@]}"; do
  if [ ! -e "$path" ]; then
    continue
  fi
  for pat in "${PATTERNS[@]}"; do
    if grep -nE "$pat" "$path"; then
      echo "FAIL: forbidden 'default' user_id fallback in $path" >&2
      failed=1
    fi
  done
done

if [ "$failed" -ne 0 ]; then
  echo
  echo "Phase 3 grep gate failed. Hot-path code must require user_id" >&2
  echo "explicitly (keyword-only, fail loud on missing)." >&2
  exit 1
fi

echo "check_no_default_user.sh: OK"
