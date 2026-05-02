#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
#
# Release hygiene: required maintainer files exist + export tree passes Option B checks.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"

die() { echo "check-protected-release-files: FAIL: $*" >&2; exit 1; }

FILES=(
  LICENSE
  scripts/check-main-hygiene.sh
  scripts/create-upstream-export-tree.sh
  scripts/check-public-export.sh
  .github/workflows/ci.yml
  docs/release/public-release-log.md
  docs/release/rc-dev-clean-snapshot-plan.md
)

for f in "${FILES[@]}"; do
  [[ -f "$ROOT/$f" ]] || die "missing required file: $f"
done

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

bash "$ROOT/scripts/create-upstream-export-tree.sh" "$TMP/export-root"
bash "$ROOT/scripts/check-public-export.sh" "$TMP/export-root"

echo "check-protected-release-files: OK"
