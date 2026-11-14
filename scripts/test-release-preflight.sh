#!/usr/bin/env bash
# LUM-788 — contract tests for release-preflight.sh (synthetic repo pairs).
#
# The cases that matter are the fail-closed ones. A pre-flight that reports
# "clear" when it could not actually evaluate a gate is worse than no
# pre-flight, because the operator stops looking.
set -euo pipefail

SCRIPT_SRC="$(cd "$(dirname "$0")" && pwd)/release-preflight.sh"
PASSED=0

fail() {
  printf 'test-release-preflight: FAIL: %s\n' "$*" >&2
  exit 1
}

# Builds tmp/lumogis-app (+ sibling devtools) and runs the pre-flight against it.
#   $1 expected exit code
#   $2 label
# Env knobs consumed by the fixture:
#   MAKE_EXIT / MAKE_OUT   — behaviour of the stub `make`
#   GATE_EXIT              — behaviour of the stub pre_main_gate.mjs
#   OMIT_FILE              — an RC support file to leave out
#   NO_DEVTOOLS            — build no sibling devtools repo
#   DIRTY                  — leave an uncommitted change
#   EXTRA_COMMIT           — commit past the source ref so HEAD diverges
run_case() {
  local expect="$1" label="$2"
  local tmp out got
  tmp="$(mktemp -d)"

  local app="$tmp/lumogis-app"
  mkdir -p "$app/scripts" "$app/config" "$tmp/bin"
  cp "$SCRIPT_SRC" "$app/scripts/release-preflight.sh"
  chmod +x "$app/scripts/release-preflight.sh"

  # Stub `make` — the real one needs ruff and a venv.
  cat >"$tmp/bin/make" <<EOF
#!/usr/bin/env bash
printf '%s\n' "${MAKE_OUT:-All checks passed!}"
exit ${MAKE_EXIT:-0}
EOF
  chmod +x "$tmp/bin/make"

  if [[ -z "${NO_DEVTOOLS:-}" ]]; then
    mkdir -p "$tmp/lumogis-devtools/scripts/linear"
    cat >"$tmp/lumogis-devtools/scripts/linear/pre_main_gate.mjs" <<EOF
console.log("stub gate argv: " + process.argv.slice(2).join(" "));
process.exit(${GATE_EXIT:-0});
EOF
  fi

  (
    cd "$app"
    git init -q
    git config user.email "lum788-test@example.invalid"
    git config user.name "lum788-test"
    for path in config/test.env.example docker-compose.public-rc-stack.yml \
                scripts/rc_test_env_defaults.py scripts/integration-public-rc.sh; do
      [[ "$path" == "${OMIT_FILE:-}" ]] && continue
      mkdir -p "$(dirname "$path")"
      echo x >"$path"
    done
    git add -A
    git commit -q -m base
    git branch -f dev HEAD
    [[ -n "${EXTRA_COMMIT:-}" ]] && { echo y >later.txt; git add -A; git commit -q -m later; }
    [[ -n "${DIRTY:-}" ]] && echo uncommitted >>config/test.env.example
    true
  )

  case "${GATED:-}" in
    real)    echo '{"issues":[]}' >"$tmp/gated.json"
             export LUMOGIS_GATED_ISSUES="$tmp/gated.json" ;;
    missing) export LUMOGIS_GATED_ISSUES="$tmp/does-not-exist.json" ;;
    *)       unset LUMOGIS_GATED_ISSUES ;;
  esac

  out="$(mktemp)"
  set +e
  ( cd "$app" && PATH="$tmp/bin:$PATH" \
      bash scripts/release-preflight.sh dev ${GATE_RANGE:+"$GATE_RANGE"} ) >"$out" 2>&1
  got=$?
  set -e

  if [[ "$got" != "$expect" ]]; then
    printf -- '--- output ---\n' >&2
    cat "$out" >&2
    fail "$label: expected exit $expect, got $got"
  fi
  printf 'ok  %s (exit %s)\n' "$label" "$got"
  PASSED=$((PASSED + 1))
  CASE_OUT="$(cat "$out")"
  rm -rf "$tmp" "$out"
}

FULL="origin/main..origin/dev"   # any range string; the stub gate just echoes it

# --- everything green -------------------------------------------------------
run_case 0 "all gates pass (scoped: gate deferred)"
grep -q "pre-flight clear" <<<"$CASE_OUT" || fail "clear run should say so"

# --- a red gate blocks the RC ----------------------------------------------
MAKE_EXIT=1 MAKE_OUT="would reformat foo.py" \
  run_case 1 "make lint red blocks"
grep -q "would reformat foo.py" <<<"$CASE_OUT" \
  || fail "lint failure output should be surfaced, not swallowed"

GATE_RANGE="$FULL" GATE_EXIT=1 run_case 1 "pre-main gate blocker blocks"

OMIT_FILE="scripts/integration-public-rc.sh" \
  run_case 1 "missing RC support file blocks"
grep -q "integration-public-rc.sh" <<<"$CASE_OUT" \
  || fail "the missing path should be named"

DIRTY=1 run_case 1 "dirty working tree blocks"
EXTRA_COMMIT=1 run_case 1 "HEAD ahead of the source ref blocks"

# --- the gate judges the PROMOTION range, not the source branch -------------
# A scoped RC does not exist yet at pre-flight time, so judging
# origin/main..origin/dev would block a three-commit RC on a P1 belonging to one
# of a thousand unrelated commits — red forever, therefore ignored. Deferring is
# correct; deferring SILENTLY is not, so the clear line must say so.
run_case 0 "scoped promotion defers the gate rather than judging the wrong range"
grep -q "DEFER" <<<"$CASE_OUT" || fail "a deferred gate must be shown as deferred"
grep -q "still outstanding" <<<"$CASE_OUT" \
  || fail "a clear pre-flight must still name what it did not judge"
grep -q "stub gate argv" <<<"$CASE_OUT" \
  && fail "the gate must not run at all without an explicit range"

GATE_RANGE="$FULL" GATE_EXIT=1 run_case 1 "an explicit range is forwarded to the gate"
grep -q -- "--range $FULL" <<<"$CASE_OUT" \
  || fail "the range should be forwarded to pre_main_gate.mjs"

GATE_RANGE="$FULL" GATE_EXIT=0 run_case 0 "a gate that actually ran is not left outstanding"
grep -q "still outstanding" <<<"$CASE_OUT" \
  && fail "a gate that ran must not also be reported as outstanding"

# --- fail closed: undetermined is never 'clear' -----------------------------
# Exit 2, not 0. These are the regressions worth a test: each one is a way for
# a gate to quietly not run at all.
GATE_RANGE="$FULL" GATE_EXIT=2 \
  run_case 2 "gate that cannot reach Linear is undetermined, not clear"
GATE_RANGE="$FULL" NO_DEVTOOLS=1 \
  run_case 2 "missing sibling devtools is undetermined, not clear"
MAKE_EXIT=2 MAKE_OUT="make test: pytest not available for python3." \
  run_case 2 "missing pytest is an operator problem, not a red dev branch"

# --- LUMOGIS_GATED_ISSUES pass-through --------------------------------------
# Without this the MCP fallback the release skill documents is unreachable, and
# any session without LINEAR_API_KEY exits 2 forever.
GATE_RANGE="$FULL" GATED=real GATE_EXIT=1 \
  run_case 1 "LUMOGIS_GATED_ISSUES is forwarded to the gate"
grep -q -- "--gated-issues" <<<"$CASE_OUT" \
  || fail "the gated-issues file should be passed through to pre_main_gate.mjs"

GATE_RANGE="$FULL" GATED=missing \
  run_case 2 "LUMOGIS_GATED_ISSUES pointing nowhere is undetermined"
grep -q "does-not-exist.json" <<<"$CASE_OUT" \
  || fail "a set-but-missing gated-issues path should name itself, not report 'no credentials'"

# --- a real failure outranks an undetermined one ----------------------------
# Exit 1 (blocked), not 2 — the operator must not read "fix your venv" when the
# branch itself is red.
MAKE_EXIT=1 GATE_RANGE="$FULL" GATE_EXIT=2 \
  run_case 1 "a hard failure outranks an undetermined gate"

printf 'test-release-preflight: all %d cases passed\n' "$PASSED"
