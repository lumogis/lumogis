#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Restore DR snapshot — Postgres, Qdrant, optional FalkorDB (LUM-185).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

POSTGRES_HOST="${POSTGRES_HOST:-postgres}"
POSTGRES_USER="${POSTGRES_USER:-lumogis}"
POSTGRES_DB="${POSTGRES_DB:-lumogis}"
QDRANT_URL="${QDRANT_URL:-http://qdrant:6333}"

usage() {
  echo "Usage: restore.sh <snapshot-dir> [--yes]" >&2
  echo "Non-interactive: RESTORE_CONFIRM=1 RESTORE_SNAPSHOT=<id> restore.sh snapshots/<id> --yes" >&2
  exit 2
}

snapshot_arg=""
confirmed=false
for arg in "$@"; do
  case "$arg" in
    --yes) confirmed=true ;;
    -h | --help) usage ;;
    *)
      if [[ -z "$snapshot_arg" ]]; then
        snapshot_arg="$arg"
      else
        usage
      fi
      ;;
  esac
done

[[ -n "$snapshot_arg" ]] || usage

if [[ "$confirmed" != "true" ]]; then
  if [[ "${RESTORE_CONFIRM:-}" != "1" || -z "${RESTORE_SNAPSHOT:-}" ]]; then
    log_error "restore requires --yes or RESTORE_CONFIRM=1 + RESTORE_SNAPSHOT"
    usage
  fi
fi

# Resolve snapshot dir under backups root
snap_root="$(snapshots_dir)"
if [[ -d "${snap_root}/${snapshot_arg}" ]]; then
  snapshot_dir="${snap_root}/${snapshot_arg}"
elif [[ -d "$snapshot_arg" ]]; then
  snapshot_dir="$snapshot_arg"
else
  log_error "snapshot not found: $snapshot_arg"
  exit 1
fi

validate_snapshot_path "$snapshot_dir"

manifest="${snapshot_dir%/}/manifest.json"
[[ -f "$manifest" ]] || { log_error "manifest.json missing"; exit 1; }

if [[ "${RESTORE_SKIP_QUIESCE:-}" != "1" ]]; then
  if restore_quiesce_violation; then
    exit 2
  fi
fi

export PGPASSWORD="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD required}"
if ! pg_restore --clean --if-exists -h "$POSTGRES_HOST" -U "$POSTGRES_USER" -d "$POSTGRES_DB" "${snapshot_dir}/postgres.dump"; then
  unset PGPASSWORD
  log_error "postgres restore failed"
  exit 1
fi
unset PGPASSWORD

postgres_ok=true
qdrant_ok=true
falkor_ok=true

collections="$(jq -r '.stores.qdrant.collections[]? // empty' "$manifest")"
while IFS= read -r coll; do
  [[ -z "$coll" ]] && continue
  rel="$(jq -r --arg c "$coll" '.stores.qdrant.files[$c]' "$manifest")"
  snap_file="${snapshot_dir}/${rel}"

  curl -sf -X DELETE "${QDRANT_URL}/collections/${coll}" >/dev/null 2>&1 || true

  if ! curl -sf -X POST "${QDRANT_URL}/collections/${coll}/snapshots/upload?priority=snapshot" \
    -F "snapshot=@${snap_file}" >/dev/null; then
    log_error "qdrant restore failed for ${coll}"
    qdrant_ok=false
    break
  fi
done <<<"$collections"

if [[ "$qdrant_ok" != "true" ]]; then
  log_error "partial restore: postgres succeeded; qdrant failed — fix qdrant then re-run restore before starting orchestrator"
  exit 1
fi

falkor_skipped="$(jq -r '.stores.falkordb.skipped // false' "$manifest")"
if [[ "$falkor_skipped" != "true" ]]; then
  rel="$(jq -r '.stores.falkordb.file' "$manifest")"
  src="${snapshot_dir}/${rel}"
  dest_dir="$(falkordb_data_dir)"
  dump_dest="${dest_dir}/dump.rdb"
  if [[ -d "$dest_dir" || -d /falkordb-data ]]; then
    mkdir -p "$dest_dir"
    cp "$src" "$dump_dest"
    log_info "falkordb dump copied to ${dump_dest} — restart falkordb to load"
  elif ! "${SCRIPT_DIR}/falkordb-dump-restore.sh" import "$src"; then
    log_error "partial restore: postgres+qdrant ok; falkordb failed — restart falkordb after fixing dump"
    exit 1
  fi
fi

log_info "restore complete from $(basename "$snapshot_dir")"
log_info "post-restore: start orchestrator + lumogis-web, check /healthz, run sample search/graph ping"
exit 0
