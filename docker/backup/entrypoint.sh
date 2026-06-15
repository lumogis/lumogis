#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Backup sidecar entrypoint — supercronic scheduler or one-shot exec (LUM-185).
set -euo pipefail

CRON_FILE="/etc/cron.d/lumogis-backup"
SCRIPTS="/scripts/backup"

render_crontab() {
  local schedule enabled
  schedule="${BACKUP_SCHEDULE_CRON:-0 3 * * *}"
  enabled="${BACKUP_ENABLED:-true}"
  if [[ "${enabled,,}" == "false" ]]; then
    echo "# BACKUP_ENABLED=false — no scheduled backups" >"$CRON_FILE"
  else
    {
      echo "SHELL=/bin/bash"
      echo "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
      echo "${schedule} root ${SCRIPTS}/backup.sh run && ${SCRIPTS}/backup.sh prune"
    } >"$CRON_FILE"
  fi
  chmod 0644 "$CRON_FILE"
}

if [[ $# -gt 0 ]]; then
  exec "$@"
fi

render_crontab
exec supercronic -passthrough-logs "$CRON_FILE"
