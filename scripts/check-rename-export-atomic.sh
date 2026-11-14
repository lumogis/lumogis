#!/usr/bin/env bash
# LUM-491 — fail if a rename under apps/ lands without a matching export strip-list
# update in the same diff (export leak risk). The strip lists are
# scripts/public-export-strip-list.txt and, when present, the local list beside it
# (LUM-898); an apps/<name>/ entry may sit in either.
# Generic over apps/<name>/ so this script, which ships in the public tree, names no
# specific tree (LUM-898).
set -euo pipefail

die() { echo "check-rename-export-atomic: FAIL: $*" >&2; exit 1; }

BASE_REF="${BASE_REF:-origin/dev}"
STRIP_LIST="scripts/public-export-strip-list.txt"
STRIP_LIST_LOCAL="scripts/public-export-strip-list.local.txt"
STRIP_LISTS=("${STRIP_LIST}")
[[ -f "${STRIP_LIST_LOCAL}" ]] && STRIP_LISTS+=("${STRIP_LIST_LOCAL}")

if ! git rev-parse --verify "${BASE_REF}" >/dev/null 2>&1; then
  die "BASE_REF ${BASE_REF} is not a valid ref"
fi

# Staged + unstaged diff vs base (implementation branch before squash-merge).
diff_names="$(git diff --name-only "${BASE_REF}"...HEAD 2>/dev/null || git diff --name-only "${BASE_REF}")"
if [[ -z "${diff_names}" ]]; then
  diff_names="$(git diff --name-only "${BASE_REF}")"
fi

has_apps_tree=0
has_strip_change=0

while IFS= read -r path; do
  [[ -z "${path}" ]] && continue
  case "${path}" in
    apps/*)
      has_apps_tree=1
      ;;
    "${STRIP_LIST}"|"${STRIP_LIST_LOCAL}")
      has_strip_change=1
      ;;
  esac
done <<<"${diff_names}"

if [[ "${has_apps_tree}" -eq 1 ]]; then
  if [[ "${has_strip_change}" -ne 1 ]]; then
    die "diff touches apps/ but no export strip list changed in diff vs ${BASE_REF}"
  fi
  # Every top-level apps/<name>/ at HEAD must be on the strip list.
  while IFS= read -r tree; do
    [[ -z "${tree}" ]] && continue
    if ! grep -qxF "${tree}/" "${STRIP_LISTS[@]}"; then
      die "an export strip list must list ${tree}/"
    fi
  done < <(git ls-tree -d --name-only HEAD apps/)
  # No top-level apps/<name>/ strip entry may point at a tree that is gone (stale pre-rename name).
  while IFS= read -r entry; do
    if ! git ls-tree -d --name-only HEAD "${entry%/}" | grep -qxF "${entry%/}"; then
      die "an export strip list still lists ${entry} after rename — repoint it to the new tree"
    fi
  done < <(grep -hE '^apps/[^/#[:space:]]+/[[:space:]]*$' "${STRIP_LISTS[@]}" | sed 's/[[:space:]]*$//')
fi

echo "check-rename-export-atomic: OK (BASE_REF=${BASE_REF})"
