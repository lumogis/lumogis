#!/usr/bin/env bash
# LUM-788 — every cheap, local pre-promotion gate, in one command.
#
# `/prepare-private-release-from-dev` runs this before it builds an RC branch.
# It exists because a checklist written in prose is a checklist that gets
# skipped: on 2026-08-05 the promotion ran without its own Step 3, and on
# 2026-08-02 a full dev promotion took nine CI runs and ~330 billable minutes
# to go green on failures that `make lint` catches in two seconds.
#
# Everything here is seconds, local, and free. No Docker, no stack, no billed
# minutes. Anything that needs a live stack belongs in CI, not in this script.
#
# Usage:
#   scripts/release-preflight.sh [SOURCE_REF] [GATE_RANGE]
#
#   SOURCE_REF  the ref being promoted from.       default: origin/dev
#   GATE_RANGE  the range the pre-main gate should judge. Omit for a SCOPED
#               promotion (A/B/C) — the RC does not exist yet, so its range is
#               unknowable here and the gate defers to the pre-merge step.
#               Pass `origin/main..origin/dev` for a FULL DEV promotion (D),
#               where the source range IS the promotion range.
#
# Exit codes:
#   0  every gate passed — safe to build the RC branch
#   1  at least one gate failed — do NOT build the RC branch
#   2  a gate could not be evaluated (fail closed; same convention as
#      pre_main_gate.mjs, where "could not determine" is never "fine")
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE_REF="${1:-origin/dev}"
GATE_RANGE="${2:-}"

# The sibling layout is load-bearing, not the absolute prefix. See
# lumogis-devtools/cursor/rules/lumogis-repo-split.mdc — that file is the
# single source for this.
DEVTOOLS="$(dirname "$REPO")/lumogis-devtools"
GATE="$DEVTOOLS/scripts/linear/pre_main_gate.mjs"

# RC tooling (`make verify-public-rc`) is unrunnable without these, and the
# failure surfaces late — after the RC branch exists.
RC_SUPPORT_FILES=(
  "config/test.env.example"
  "docker-compose.public-rc-stack.yml"
  "scripts/rc_test_env_defaults.py"
  "scripts/integration-public-rc.sh"
)

FAILED=()
UNKNOWN=()
DEFERRED=()

pass() { printf '  \033[32mPASS\033[0m  %s\n' "$1"; }
fail() { printf '  \033[31mFAIL\033[0m  %s\n' "$1"; FAILED+=("$1"); }
unknown() { printf '  \033[33m????\033[0m  %s\n' "$1"; UNKNOWN+=("$1"); }
defer() { printf '  \033[36mDEFER\033[0m %s\n' "$1"; DEFERRED+=("$1"); }
note() { printf '        %s\n' "$1"; }

cd "$REPO"

printf '\nrelease-preflight — source ref: %s\n\n' "$SOURCE_REF"

# ---------------------------------------------------------------------------
# Gate 1 — the working tree IS the thing being promoted.
#
# Every gate below reads the working tree, but the promotion reads SOURCE_REF.
# If they differ, a green preflight is a statement about code that is not being
# shipped. That is not a pedantic distinction: this exact class of mistake —
# computing a result over one range and reporting it for another — has already
# happened once in this workflow.
# ---------------------------------------------------------------------------
if ! git rev-parse --verify --quiet "$SOURCE_REF" >/dev/null; then
  unknown "source ref resolves"
  note "$SOURCE_REF does not resolve in $REPO — run 'git fetch origin' first"
else
  head_sha="$(git rev-parse HEAD)"
  ref_sha="$(git rev-parse "$SOURCE_REF")"
  dirty="$(git status --porcelain)"
  if [[ -n "$dirty" ]]; then
    fail "working tree clean"
    note "uncommitted changes — the gates below would test something other than $SOURCE_REF"
  elif [[ "$head_sha" != "$ref_sha" ]]; then
    fail "HEAD is $SOURCE_REF"
    note "HEAD is ${head_sha:0:8}, $SOURCE_REF is ${ref_sha:0:8}"
    note "checkout the promotion source before pre-flighting it"
  else
    pass "working tree is $SOURCE_REF (${ref_sha:0:8}), clean"
  fi
fi

# ---------------------------------------------------------------------------
# Gate 2 — RC support files exist on the source ref.
# ---------------------------------------------------------------------------
missing=()
for path in "${RC_SUPPORT_FILES[@]}"; do
  git cat-file -e "$SOURCE_REF:$path" 2>/dev/null || missing+=("$path")
done
if [[ ${#missing[@]} -eq 0 ]]; then
  pass "RC support files present on $SOURCE_REF (${#RC_SUPPORT_FILES[@]})"
else
  fail "RC support files present on $SOURCE_REF"
  for path in "${missing[@]}"; do note "missing: $path"; done
  note "bring them over path-scoped: git checkout origin/main -- <path>"
  note "do NOT merge origin/main into dev — it deletes the private-only trees"
fi

# ---------------------------------------------------------------------------
# Gate 3 — make lint.
#
# ruff check + ruff format --check + the refresh-token-jti guard. Two seconds.
# ---------------------------------------------------------------------------
lint_log="$(mktemp)"
trap 'rm -f "$lint_log"' EXIT
if make lint >"$lint_log" 2>&1; then
  pass "make lint"
else
  status=$?
  # Makefile:577 — check-pytest exits 2 when pytest is missing from $(PYTHON).
  # That is a broken local environment, not a red dev branch; the distinction
  # matters because one blocks the promotion and the other blocks the operator.
  if [[ $status -eq 2 ]] && grep -q "pytest not available" "$lint_log"; then
    unknown "make lint"
    note "pytest missing from this interpreter — activate the venv (CONTRIBUTING.md)"
  else
    fail "make lint"
  fi
  sed 's/^/        /' "$lint_log" | tail -n 30
fi

# ---------------------------------------------------------------------------
# Gate 4 — the pre-main promotion gate (ADR-204).
#
# Open P1 gaps carrying `gate:pre-main` block their parent reaching private
# main. Fail closed: a gate that cannot reach Linear is not a gate that passed.
#
# RANGE. This is the subtle part, and running it wrong is worse than not
# running it. The gate asks "does this promotion carry an issue whose P1 gap is
# still open" — a question about the PROMOTION range, not the source branch.
#
# For a scoped RC (A/B/C) those are different, by a lot. `origin/main..origin/dev`
# is ~1000 commits; the RC is three. Judging the wide range would block a
# three-commit CI fix on LUM-310 and LUM-455, whose parents are somewhere in
# those thousand commits and whose code is not in the RC at all. That is not a
# conservative gate, it is a gate that is red forever and therefore ignored.
#
# So: without an explicit GATE_RANGE the gate DEFERS to the pre-merge step,
# which runs on `origin/main..<rc-tip>` once the RC exists. Deferred is not
# passed — the final report has to carry that verdict either way.
#
# Reaching Linear needs LINEAR_API_KEY. A session holding the Linear MCP but no
# key sets LUMOGIS_GATED_ISSUES to a `list_issues(label="gate:pre-main")` dump
# instead; the gate rejects an unfiltered list loudly. Without either, this is
# undetermined, never clear.
# ---------------------------------------------------------------------------
gate_args=(--check)
[[ -n "$GATE_RANGE" ]] && gate_args+=(--range "$GATE_RANGE")
gate_ready=1
if [[ -n "${LUMOGIS_GATED_ISSUES:-}" ]]; then
  if [[ -f "$LUMOGIS_GATED_ISSUES" ]]; then
    gate_args+=(--gated-issues "$LUMOGIS_GATED_ISSUES")
  else
    # Set-but-missing is a typo, and silently falling back to "no credentials"
    # would report the wrong reason for the wrong problem.
    gate_ready=0
  fi
fi

if [[ -z "$GATE_RANGE" ]]; then
  defer "pre-main gate — runs pre-merge, on origin/main..<rc-tip>"
  note "scoped promotion: the RC does not exist yet, so its range is unknowable here"
  note "for a FULL DEV promotion pass: $0 $SOURCE_REF origin/main..origin/dev"
elif [[ ! -f "$GATE" ]]; then
  unknown "pre-main gate (gate:pre-main)"
  note "not found at $GATE"
  note "the sibling layout is load-bearing — see lumogis-repo-split.mdc"
elif [[ $gate_ready -eq 0 ]]; then
  unknown "pre-main gate (gate:pre-main)"
  note "LUMOGIS_GATED_ISSUES=$LUMOGIS_GATED_ISSUES does not exist"
else
  gate_log="$(mktemp)"
  trap 'rm -f "$lint_log" "$gate_log"' EXIT
  set +e
  node "$GATE" "${gate_args[@]}" >"$gate_log" 2>&1
  gate_status=$?
  set -e
  case "$gate_status" in
    0) pass "pre-main gate (no open gate:pre-main blockers)" ;;
    1) fail "pre-main gate"; sed 's/^/        /' "$gate_log" | tail -n 40 ;;
    *) unknown "pre-main gate"
       note "exit $gate_status — could not determine, which is not the same as clear"
       sed 's/^/        /' "$gate_log" | tail -n 20 ;;
  esac
fi

# ---------------------------------------------------------------------------
printf '\n'
if [[ ${#FAILED[@]} -eq 0 && ${#UNKNOWN[@]} -eq 0 ]]; then
  printf '\033[32mpre-flight clear\033[0m — safe to build the RC branch from %s\n' "$SOURCE_REF"
  # Say what was NOT judged, every time. A clear line that quietly omits a
  # deferred gate is how "not checked" becomes "nothing blocking".
  for d in ${DEFERRED[@]+"${DEFERRED[@]}"}; do
    printf '\033[36mstill outstanding\033[0m — %s\n' "$d"
  done
  printf '\n'
  exit 0
fi
if [[ ${#FAILED[@]} -gt 0 ]]; then
  printf '\033[31mpre-flight FAILED\033[0m (%d) — do not build an RC branch:\n' "${#FAILED[@]}"
  for f in "${FAILED[@]}"; do printf '  - %s\n' "$f"; done
fi
if [[ ${#UNKNOWN[@]} -gt 0 ]]; then
  printf '\033[33mpre-flight UNDETERMINED\033[0m (%d) — fix the environment and re-run:\n' "${#UNKNOWN[@]}"
  for u in "${UNKNOWN[@]}"; do printf '  - %s\n' "$u"; done
fi
printf '\n'
[[ ${#FAILED[@]} -gt 0 ]] && exit 1
exit 2
