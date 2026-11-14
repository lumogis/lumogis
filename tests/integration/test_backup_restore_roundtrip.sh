#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Integration smoke: backup sidecar can run and verify against live postgres+qdrant.
# Full volume wipe/restore round-trip: scripts/integration-backup-roundtrip.sh (LUM-484;
# invoked by make compose-test-backup).
set -euo pipefail

echo "[test_backup_restore_roundtrip] running one-shot backup"
/scripts/backup/backup.sh run

latest="$(ls -1t /backups/snapshots 2>/dev/null | grep -v '^\.tmp$' | head -1)"
test -n "$latest"
echo "[test_backup_restore_roundtrip] verifying ${latest}"
/scripts/backup/verify.sh "/backups/snapshots/${latest}" --rewrite-manifest
echo "[test_backup_restore_roundtrip] ok"
