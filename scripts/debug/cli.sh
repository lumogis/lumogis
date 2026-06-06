#!/usr/bin/env bash
# Debug test runner dispatcher (LUM-377).
set -euo pipefail
DEBUG_DIR="$(cd "$(dirname "$0")" && pwd)"
# shellcheck source=scripts/debug/_common.sh
source "${DEBUG_DIR}/_common.sh"

INVENTORY="${DEBUG_DIR}/inventory.tsv"

lumogis_debug_list() {
  echo "| id | make_target | wrapper | prereqs | stage | heavy | private_tree_only |"
  echo "| --- | --- | --- | --- | --- | --- | --- |"
  local line id target wrapper prereqs stage heavy private
  while IFS=$'\t' read -r id target wrapper prereqs stage heavy private; do
    [[ "$id" == "id" || -z "$id" ]] && continue
    if [[ "$private" == "1" ]]; then
      continue
    fi
    echo "| ${id} | ${target} | ${wrapper} | ${prereqs} | ${stage} | ${heavy} | ${private} |"
  done <"$INVENTORY"
}

lumogis_debug_run_stage() {
  local script="$1"
  shift
  local extra=("$@")
  local rc=0
  set +e
  "${DEBUG_DIR}/${script}" "${extra[@]}"
  rc=$?
  set -e
  return "$rc"
}

lumogis_debug_chain() {
  local fail_fast=1
  [[ "${LUMOGIS_DEBUG_FAIL_FAST:-}" == "0" ]] && fail_fast=0
  local aggregate=0
  local args=()
  [[ "${VERBOSE:-0}" -eq 1 ]] && args+=(--verbose)

  local stages=(
    "unit.sh:${args[*]}"
    "lint.sh:${args[*]}"
    "web.sh:unit ${args[*]}"
    "rust.sh:${args[*]}"
  )
  local entry script extra rc
  for entry in "${stages[@]}"; do
    script="${entry%%:*}"
    extra="${entry#*:}"
    # shellcheck disable=SC2086
    lumogis_debug_run_stage "$script" $extra
    rc=$?
    if [[ "$rc" -ne 0 ]]; then
      aggregate="$rc"
      if [[ "$fail_fast" -eq 1 ]]; then
        return "$rc"
      fi
    fi
  done
  return "$aggregate"
}

VERBOSE=0
HEAVY=0
ARGS=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --verbose) VERBOSE=1; shift ;;
    --heavy) HEAVY=1; shift ;;
    *) ARGS+=("$1"); shift ;;
  esac
done
[[ "$HEAVY" -eq 1 ]] && export LUMOGIS_DEBUG_HEAVY=1

CMD="${ARGS[0]:-debug}"
shift_args=("${ARGS[@]:1}")

case "$CMD" in
  list)
    lumogis_debug_list
    ;;
  debug)
    lumogis_debug_chain
    ;;
  unit)
    extra=()
    [[ "$VERBOSE" -eq 1 ]] && extra+=(--verbose)
    exec "${DEBUG_DIR}/unit.sh" "${extra[@]}" "${shift_args[@]}"
    ;;
  lint)
    extra=()
    [[ "$VERBOSE" -eq 1 ]] && extra+=(--verbose)
    exec "${DEBUG_DIR}/lint.sh" "${extra[@]}" "${shift_args[@]}"
    ;;
  web|rust|integration)
    sub="${shift_args[0]:-}"
    rest=("${shift_args[@]:1}")
    if [[ -z "$sub" ]]; then
      echo "lumogis debug: $CMD requires a subcommand (see: ./scripts/debug/cli.sh list)" >&2
      exit 2
    fi
    extra=("$sub")
    [[ "$VERBOSE" -eq 1 ]] && extra+=(--verbose)
    [[ "$HEAVY" -eq 1 ]] && extra+=(--heavy)
    exec "${DEBUG_DIR}/${CMD}.sh" "${extra[@]}" "${rest[@]}"
    ;;
  *)
    echo "lumogis debug: unknown command: $CMD (try: list, debug, unit, lint, web, rust, integration)" >&2
    exit 2
    ;;
esac
