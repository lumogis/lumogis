#!/usr/bin/env bash
# LUM-491 — fail if apps/lumogis-hub → apps/lumogis-server rename lands without a matching
# scripts/public-export-strip-list.txt update in the same diff (silent public AGPL leak risk).
set -euo pipefail

die() { echo "check-rename-export-atomic: FAIL: $*" >&2; exit 1; }

BASE_REF="${BASE_REF:-origin/dev}"
STRIP_LIST="scripts/public-export-strip-list.txt"

if ! git rev-parse --verify "${BASE_REF}" >/dev/null 2>&1; then
  die "BASE_REF ${BASE_REF} is not a valid ref"
fi

# Staged + unstaged diff vs base (implementation branch before squash-merge).
diff_names="$(git diff --name-only "${BASE_REF}"...HEAD 2>/dev/null || git diff --name-only "${BASE_REF}")"
if [[ -z "${diff_names}" ]]; then
  diff_names="$(git diff --name-only "${BASE_REF}")"
fi

has_server_tree=0
has_hub_tree=0
has_strip_change=0

while IFS= read -r path; do
  [[ -z "${path}" ]] && continue
  case "${path}" in
    apps/lumogis-server/*|apps/lumogis-server)
      has_server_tree=1
      ;;
    apps/lumogis-hub/*|apps/lumogis-hub)
      has_hub_tree=1
      ;;
    "${STRIP_LIST}")
      has_strip_change=1
      ;;
  esac
done <<<"${diff_names}"

if [[ "${has_server_tree}" -eq 1 || "${has_hub_tree}" -eq 1 ]]; then
  if [[ "${has_strip_change}" -ne 1 ]]; then
    die "rename touches apps/lumogis-* but ${STRIP_LIST} is unchanged in diff vs ${BASE_REF}"
  fi
  if ! grep -q 'apps/lumogis-server/' "${STRIP_LIST}"; then
    die "${STRIP_LIST} must list apps/lumogis-server/ when the tree is renamed"
  fi
  if grep -qE '^apps/lumogis-hub/' "${STRIP_LIST}"; then
    die "${STRIP_LIST} still lists apps/lumogis-hub/ after rename — repoint to apps/lumogis-server/"
  fi
fi

echo "check-rename-export-atomic: OK (BASE_REF=${BASE_REF})"
