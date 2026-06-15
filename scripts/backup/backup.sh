#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Instance-scoped DR backup — Postgres, Qdrant, optional FalkorDB (LUM-185).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

POSTGRES_HOST="${POSTGRES_HOST:-postgres}"
POSTGRES_USER="${POSTGRES_USER:-lumogis}"
POSTGRES_DB="${POSTGRES_DB:-lumogis}"
QDRANT_URL="${QDRANT_URL:-http://qdrant:6333}"

cmd="${1:-run}"

run_backup() {
  if [[ "$(env_default BACKUP_ENABLED true)" == "false" ]]; then
    log_info "BACKUP_ENABLED=false — skipping run"
    exit 0
  fi

  if ! acquire_backup_lock; then
    exit 0
  fi

  local started
  started="$(date +%s)"
  local snap_id
  snap_id="$(snapshot_id_now)"
  local tmp_dir final_dir
  tmp_dir="$(snapshot_tmp_dir)/${snap_id}"
  final_dir="$(snapshots_dir)/${snap_id}"
  mkdir -p "$tmp_dir"

  cleanup_tmp() {
    rm -rf "$tmp_dir"
  }
  trap cleanup_tmp ERR

  if ! preflight_disk_headroom "$(estimate_data_usage_bytes)"; then
    cleanup_tmp
    release_backup_lock
    exit 1
  fi

  export PGPASSWORD="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD required}"
  if ! pg_dump -Fc -h "$POSTGRES_HOST" -U "$POSTGRES_USER" "$POSTGRES_DB" >"${tmp_dir}/postgres.dump"; then
    log_error "pg_dump failed"
    cleanup_tmp
    release_backup_lock
    exit 1
  fi
  unset PGPASSWORD

  local qdrant_json='{"collections":[],"collections_meta":{},"files":{}}'
  local collections
  collections="$(curl -sf "${QDRANT_URL}/collections" | jq -r '.result.collections[].name' 2>/dev/null || true)"
  if [[ -n "$collections" ]]; then
    mkdir -p "${tmp_dir}/qdrant"
    local coll meta_json files_json
    meta_json='{}'
    files_json='{}'
    while IFS= read -r coll; do
      [[ -z "$coll" ]] && continue
      local cfg size distance
      cfg="$(curl -sf "${QDRANT_URL}/collections/${coll}")"
      size="$(echo "$cfg" | jq -r '.result.config.params.vectors.size // .result.config.params.vectors.default.size // empty')"
      distance="$(echo "$cfg" | jq -r '.result.config.params.vectors.distance // .result.config.params.vectors.default.distance // "Cosine"')"
      meta_json="$(echo "$meta_json" | jq --arg c "$coll" --argjson sz "${size:-384}" --arg dist "${distance:-Cosine}" \
        '. + {($c): {vector_size: ($sz|tonumber), distance: $dist}}')"

      curl -sf -X POST "${QDRANT_URL}/collections/${coll}/snapshots" >/dev/null
      local snap_name
      snap_name="$(curl -sf "${QDRANT_URL}/collections/${coll}/snapshots" | jq -r '.result[-1].name // empty')"
      if [[ -z "$snap_name" ]]; then
        log_error "no qdrant snapshot for ${coll}"
        cleanup_tmp
        release_backup_lock
        exit 1
      fi
      local out_file="qdrant/${coll}.snapshot"
      curl -sf "${QDRANT_URL}/collections/${coll}/snapshots/${snap_name}" -o "${tmp_dir}/${out_file}"
      files_json="$(echo "$files_json" | jq --arg c "$coll" --arg f "$out_file" '. + {($c): $f}')"
      curl -sf -X DELETE "${QDRANT_URL}/collections/${coll}/snapshots/${snap_name}" >/dev/null 2>&1 || true
    done <<<"$collections"
    qdrant_json="$(jq -n \
      --argjson cols "$(echo "$collections" | jq -R . | jq -s .)" \
      --argjson meta "$meta_json" \
      --argjson files "$files_json" \
      '{collections: $cols, collections_meta: $meta, files: $files}')"
  fi

  local include_falkor method falkor_skipped falkor_file falkor_engine
  include_falkor="$(resolve_backup_include_falkordb)"
  method="bgsave"
  falkor_skipped="false"
  falkor_file="falkordb/dump.rdb"
  falkor_engine=""
  if [[ "$include_falkor" == "true" ]]; then
    mkdir -p "${tmp_dir}/falkordb"
    if redis-cli -h "${FALKORDB_HOST:-falkordb}" -p "${FALKORDB_PORT:-6379}" \
      --rdb "${tmp_dir}/${falkor_file}" >/dev/null 2>&1 \
      && [[ -s "${tmp_dir}/${falkor_file}" ]]; then
      method="redis_rdb"
      falkor_engine="$(redis-cli -h "${FALKORDB_HOST:-falkordb}" -p "${FALKORDB_PORT:-6379}" INFO server 2>/dev/null | awk -F: '/redis_version/ {gsub(/\r/,"",$2); print $2; exit}')"
    elif [[ -d "$(falkordb_data_dir)" || -d /falkordb-data ]]; then
      redis-cli -h "${FALKORDB_HOST:-falkordb}" -p "${FALKORDB_PORT:-6379}" BGSAVE >/dev/null || true
      tries=0
      last="$(redis-cli -h "${FALKORDB_HOST:-falkordb}" -p "${FALKORDB_PORT:-6379}" LASTSAVE)"
      while (( tries < 45 )); do
        dump_src="$(falkordb_dump_rdb_path || true)"
        if [[ -n "$dump_src" ]]; then
          break
        fi
        sleep 1
        last2="$(redis-cli -h "${FALKORDB_HOST:-falkordb}" -p "${FALKORDB_PORT:-6379}" LASTSAVE)"
        if [[ "$last2" != "$last" ]]; then
          dump_src="$(falkordb_dump_rdb_path || true)"
          if [[ -n "$dump_src" ]]; then
            break
          fi
        fi
        tries=$((tries + 1))
      done
      dump_src="$(falkordb_dump_rdb_path || true)"
      if [[ -n "$dump_src" ]]; then
        cp "$dump_src" "${tmp_dir}/${falkor_file}"
        falkor_engine="$(redis-cli -h "${FALKORDB_HOST:-falkordb}" -p "${FALKORDB_PORT:-6379}" INFO server 2>/dev/null | awk -F: '/redis_version/ {gsub(/\r/,"",$2); print $2; exit}')"
      elif ! "${SCRIPT_DIR}/falkordb-dump-restore.sh" export "${tmp_dir}/${falkor_file}"; then
        log_error "falkordb backup failed after BGSAVE"
        cleanup_tmp
        release_backup_lock
        exit 1
      else
        method="dump_restore"
      fi
    elif ! "${SCRIPT_DIR}/falkordb-dump-restore.sh" export "${tmp_dir}/${falkor_file}"; then
      log_error "falkordb backup failed (no ro mount and redis export failed)"
      cleanup_tmp
      release_backup_lock
      exit 1
    else
      method="dump_restore"
    fi
  else
    falkor_skipped="true"
  fi

  local pg_sha pg_bytes pg_engine qdrant_engine
  pg_sha="$(sha256_file "${tmp_dir}/postgres.dump")"
  pg_bytes="$(file_bytes "${tmp_dir}/postgres.dump")"
  pg_engine="$(pg_dump --version | awk '{print $3}')"
  qdrant_engine="$(curl -sf "${QDRANT_URL}/" | jq -r '.version // empty' 2>/dev/null || echo "")"

  local falkor_manifest
  if [[ "$falkor_skipped" == "true" ]]; then
    falkor_manifest='{"file":"falkordb/dump.rdb","method":"bgsave","skipped":true,"engine_version":null}'
  else
    falkor_manifest="$(jq -n \
      --arg file "$falkor_file" \
      --arg method "$method" \
      --arg eng "${falkor_engine:-}" \
      --arg sha "$(sha256_file "${tmp_dir}/${falkor_file}")" \
      --argjson bytes "$(file_bytes "${tmp_dir}/${falkor_file}")" \
      '{file:$file, method:$method, skipped:false, engine_version:($eng|if .=="" then null else . end), sha256:$sha, bytes:$bytes}')"
  fi

  local manifest
  manifest="$(jq -n \
    --arg created "$(iso_now_utc)" \
    --arg version "$(env_default LUMOGIS_VERSION dev)" \
    --arg compose "$(env_default COMPOSE_PROJECT_NAME lumogis)" \
    --arg graph "$(env_default GRAPH_MODE disabled)" \
    --arg pg_sha "$pg_sha" \
    --argjson pg_bytes "$pg_bytes" \
    --arg pg_eng "$pg_engine" \
    --arg q_eng "$qdrant_engine" \
    --argjson qdrant "$qdrant_json" \
    --argjson falkor "$falkor_manifest" \
    '{
      schema_version: 1,
      created_at: $created,
      verified_at: null,
      lumogis_version: $version,
      stores: {
        postgres: {file: "postgres.dump", sha256: $pg_sha, bytes: $pg_bytes, engine_version: $pg_eng},
        qdrant: ($qdrant + {engine_version: ($q_eng|if .=="" then null else . end)}),
        falkordb: $falkor
      },
      compose_project: $compose,
      graph_mode: $graph,
      verify_status: "unknown",
      verify_errors: []
    }')"

  write_manifest_json "${tmp_dir}/manifest.json" "$manifest"

  if ! BACKUP_DIR="$(backup_root_dir)" "${SCRIPT_DIR}/verify.sh" "$tmp_dir"; then
    log_error "verify failed on staged snapshot"
    cleanup_tmp
    release_backup_lock
    exit 1
  fi

  mkdir -p "$(snapshots_dir)"
  mv "$tmp_dir" "$final_dir"
  trap - ERR

  BACKUP_DIR="$(backup_root_dir)" "${SCRIPT_DIR}/verify.sh" "$final_dir" --rewrite-manifest

  local elapsed=$(( $(date +%s) - started ))
  log_info "backup complete: ${snap_id} (${elapsed}s)"
  if (( elapsed > 900 )); then
    log_warn "backup took ${elapsed}s (>15 min)"
  fi

  release_backup_lock
}

case "$cmd" in
  run) run_backup ;;
  prune) prune_snapshots ;;
  *)
    echo "Usage: backup.sh run|prune" >&2
    exit 2
    ;;
esac
