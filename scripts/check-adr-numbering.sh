#!/usr/bin/env bash
# Guard ADR numbering in docs/decisions/ against *new* duplicate numbers.
#
# Two ADRs both numbered 199 landed on 2026-08-02 from parallel branches that
# each picked "next free number" against a tree that did not yet contain the
# other. Nothing caught it, and both files shipped in the public export tree.
#
# Auditing the tree afterwards showed this was not a one-off: numbers 034
# through 156 carry long-standing duplicates. Renumbering those would churn
# inbound links across docs and ADR bodies for no behavioural gain, so they are
# recorded in a baseline file and this check acts as a ratchet:
#
#   - a duplicate number NOT in the baseline fails the check
#   - a baseline entry that no longer duplicates is reported so the baseline
#     shrinks as history gets cleaned up (never grows silently)
#
# Heading numbers are checked only when the heading actually carries one; a
# plain `# ADR: Title` is an older convention, not a defect.

set -euo pipefail

ADR_DIR="${1:-docs/decisions}"
BASELINE="${2:-scripts/adr-duplicate-baseline.txt}"

if [[ ! -d "$ADR_DIR" ]]; then
  echo "check-adr-numbering: not a directory: $ADR_DIR" >&2
  exit 1
fi

declare -A baselined=()
if [[ -f "$BASELINE" ]]; then
  while read -r line; do
    line="${line%%#*}"
    line="${line//[[:space:]]/}"
    [[ -n "$line" ]] && baselined["$line"]=1
  done < "$BASELINE"
fi

status=0
declare -A first_seen=()
declare -A duplicated=()

shopt -s nullglob
for path in "$ADR_DIR"/*.md; do
  file="$(basename "$path")"
  [[ "$file" =~ ^([0-9]+)- ]] || continue
  number="${BASH_REMATCH[1]}"

  if [[ -n "${first_seen[$number]:-}" ]]; then
    duplicated[$number]=1
    if [[ -z "${baselined[$number]:-}" ]]; then
      echo "check-adr-numbering: NEW duplicate ADR number $number:" >&2
      echo "  ${first_seen[$number]}" >&2
      echo "  $file" >&2
      echo "  -> renumber the ADR merged later and update its inbound links" >&2
      status=1
    fi
  else
    first_seen[$number]="$file"
  fi

  heading="$(head -1 "$path")"
  if [[ "$heading" =~ ^#[[:space:]]*ADR[-[:space:]]+([0-9]+) ]]; then
    heading_number="${BASH_REMATCH[1]}"
    if (( 10#$heading_number != 10#$number )); then
      echo "check-adr-numbering: heading/filename mismatch in $file" >&2
      echo "  filename says $number, heading says $heading_number" >&2
      status=1
    fi
  fi
done
shopt -u nullglob

for number in "${!baselined[@]}"; do
  if [[ -z "${duplicated[$number]:-}" ]]; then
    echo "check-adr-numbering: baseline entry $number no longer duplicates —" \
         "drop it from $BASELINE" >&2
    status=1
  fi
done

if (( status == 0 )); then
  echo "check-adr-numbering: OK (${#first_seen[@]} numbered ADRs," \
       "${#baselined[@]} baselined historical duplicates)"
fi

exit "$status"
