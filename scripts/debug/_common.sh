# Lumogis debug test runners — shared helpers (LUM-377).
# Sourced by scripts/debug/*.sh; do not execute directly.

[[ -n "${LUMOGIS_DEBUG_COMMON_LOADED:-}" ]] && return 0
LUMOGIS_DEBUG_COMMON_LOADED=1

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
LOG_DIR=""

lumogis_debug_realpath() {
  if command -v realpath >/dev/null 2>&1; then
    realpath -m "$1"
  else
    readlink -f "$1"
  fi
}

lumogis_debug_validate_log_dir() {
  local requested="${LUMOGIS_DEBUG_LOG_DIR:-$REPO_ROOT/target/debug-logs}"
  if [[ "$requested" == *".."* ]]; then
    echo "lumogis debug: invalid log directory (.. not allowed): $requested" >&2
    exit 1
  fi
  local canon
  canon="$(lumogis_debug_realpath "$requested")" || {
    echo "lumogis debug: cannot resolve log directory: $requested" >&2
    exit 1
  }
  case "$canon" in
    "$REPO_ROOT"/* | "$REPO_ROOT") ;;
    /tmp | /tmp/*) ;;
    *)
      echo "lumogis debug: log directory must be under repo root or /tmp: $canon" >&2
      exit 1
      ;;
  esac
  LOG_DIR="$canon"
  if ! mkdir -p "$LOG_DIR"; then
    echo "lumogis debug: cannot create log directory: $LOG_DIR" >&2
    exit 1
  fi
}

lumogis_debug_init() {
  local suite="$1"
  lumogis_debug_validate_log_dir
  SUITE="$suite"
  TS="$(date +%Y%m%d-%H%M%S)"
  LOG_FILE="${LOG_DIR}/${suite}-${TS}.log"
  DIGEST_FILE="${LOG_DIR}/${suite}-${TS}.digest.md"
  : >"$LOG_FILE"
  lumogis_debug_rotate "${suite}"
}

lumogis_debug_rotate() {
  local prefix="$1"
  local max="${2:-50}"
  local f
  mapfile -t files < <(ls -1t "${LOG_DIR}/${prefix}-"*.log 2>/dev/null || true)
  if ((${#files[@]} > max)); then
    for f in "${files[@]:max}"; do
      rm -f "$f"
    done
  fi
  mapfile -t digests < <(ls -1t "${LOG_DIR}/${prefix}-"*.digest.md 2>/dev/null || true)
  if ((${#digests[@]} > max)); then
    for f in "${digests[@]:max}"; do
      rm -f "$f"
    done
  fi
}

lumogis_debug_agent_digest_supported() {
  python3 -c "import pytest_agent_digest" 2>/dev/null \
    && python3 -m pytest --help 2>/dev/null | grep -q 'agent-digest'
}

lumogis_debug_print_summary() {
  local rc="$1"
  local logfile="$2"
  local digest="${3:-}"
  if [[ -n "$digest" && -f "$digest" ]]; then
    echo "--- summary (digest) ---"
    head -n 20 "$digest"
  else
    echo "--- summary (tail) ---"
    tail -n 15 "$logfile" 2>/dev/null || true
  fi
  echo "--- exit: $rc | log: $logfile ---"
}

lumogis_debug_run_make_logged() {
  local target="$1"
  set +e
  make -C "$REPO_ROOT" "$target" 2>&1 | tee -a "$LOG_FILE"
  local rc=${PIPESTATUS[0]}
  set -e
  echo "$rc"
}

lumogis_debug_run_cmd_logged() {
  set +e
  "$@" 2>&1 | tee -a "$LOG_FILE"
  local rc=${PIPESTATUS[0]}
  set -e
  echo "$rc"
}

lumogis_debug_heavy_allowed() {
  [[ "${LUMOGIS_DEBUG_HEAVY:-}" == "1" ]] && return 0
  local arg
  for arg in "$@"; do
    [[ "$arg" == "--heavy" ]] && return 0
  done
  return 1
}

lumogis_debug_require_heavy() {
  if ! lumogis_debug_heavy_allowed "$@"; then
    echo "lumogis debug: refusing heavy target without --heavy or LUMOGIS_DEBUG_HEAVY=1" >&2
    exit 2
  fi
}
