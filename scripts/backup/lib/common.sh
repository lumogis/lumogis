#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Shared helpers for Lumogis DR backup scripts (LUM-185).
set -euo pipefail

BACKUP_LOG_PREFIX="${BACKUP_LOG_PREFIX:-[backup]}"

log_info() {
  echo "${BACKUP_LOG_PREFIX} $*" >&2
}

log_warn() {
  echo "${BACKUP_LOG_PREFIX} WARN: $*" >&2
}

log_error() {
  echo "${BACKUP_LOG_PREFIX} ERROR: $*" >&2
}

backup_root_dir() {
  echo "${BACKUP_DIR:-/backups}"
}

snapshots_dir() {
  echo "$(backup_root_dir)/snapshots"
}

snapshot_tmp_dir() {
  echo "$(snapshots_dir)/.tmp"
}

lock_file_path() {
  echo "$(backup_root_dir)/.backup.lock"
}

env_default() {
  local name="$1"
  local default="$2"
  if [[ -n "${!name:-}" ]]; then
    printf '%s' "${!name}"
  else
    printf '%s' "$default"
  fi
}

resolve_backup_include_falkordb() {
  local explicit="${BACKUP_INCLUDE_FALKORDB:-}"
  if [[ -n "$explicit" ]]; then
    case "${explicit,,}" in
      1 | true | yes | on) echo "true" ;;
      *) echo "false" ;;
    esac
    return
  fi
  local graph_mode
  graph_mode="$(env_default GRAPH_MODE disabled)"
  local falkor_url
  falkor_url="$(env_default FALKORDB_URL "")"
  if [[ "${graph_mode,,}" != "disabled" && -n "$falkor_url" ]]; then
    echo "true"
  else
    echo "false"
  fi
}

acquire_backup_lock() {
  local lock
  lock="$(lock_file_path)"
  mkdir -p "$(backup_root_dir)"
  exec 9>"$lock"
  if ! flock -n 9; then
    log_warn "backup lock held at $lock — skipping"
    return 1
  fi
  return 0
}

release_backup_lock() {
  flock -u 9 2>/dev/null || true
}

sha256_file() {
  sha256sum "$1" | awk '{print $1}'
}

file_bytes() {
  stat -c '%s' "$1"
}

iso_now_utc() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

snapshot_id_now() {
  date -u +"%Y%m%d-%H%M%S"
}

read_manifest_field() {
  local manifest="$1"
  local field="$2"
  jq -r "$field // empty" "$manifest" 2>/dev/null || true
}

write_manifest_json() {
  local path="$1"
  local json="$2"
  printf '%s\n' "$json" >"$path"
}

preflight_disk_headroom() {
  local required_raw="${1:-0}"
  local required
  required="$(printf '%.0f' "$required_raw" 2>/dev/null || echo 0)"
  local backup_root
  backup_root="$(backup_root_dir)"
  mkdir -p "$backup_root"
  local avail
  avail="$(df -Pk "$backup_root" | awk 'NR==2 {printf "%.0f", $4 * 1024}')"
  if [[ -z "$avail" ]] || (( avail < required )); then
    log_error "insufficient disk: need ${required} bytes free at ${backup_root}, have ${avail:-0}"
    return 1
  fi
  return 0
}

estimate_data_usage_bytes() {
  local total=0
  local pg_path="${POSTGRES_DATA_PATH:-/var/lib/postgresql/data}"
  local qdrant_path="${QDRANT_DATA_PATH:-/qdrant/storage}"
  if [[ -d "$pg_path" ]]; then
    pg_used="$(du -sb "$pg_path" 2>/dev/null | awk '{printf "%.0f", $1}' || echo 0)"
    total=$((total + pg_used))
  fi
  if [[ -d "$qdrant_path" ]]; then
    q_used="$(du -sb "$qdrant_path" 2>/dev/null | awk '{printf "%.0f", $1}' || echo 0)"
    if (( q_used > total )); then
      total=$q_used
    fi
  fi
  if (( total == 0 )); then
    total=$((256 * 1024 * 1024))
  fi
  echo $((total + total / 4))
}

validate_snapshot_path() {
  local candidate="$1"
  local root
  root="$(realpath "$(snapshots_dir)")"
  local resolved
  resolved="$(realpath "$candidate")"
  case "$resolved" in
    "$root"/*) return 0 ;;
    *)
      log_error "snapshot path outside backups root: $resolved"
      return 1
      ;;
  esac
}

# Given successful snapshot ids (YYYYMMDD-HHMMSS), print ids to retain (newest-first order).
# Args: keep_daily keep_weekly id [id ...]
compute_protected_snapshot_ids() {
  local keep_daily="${1:-7}"
  local keep_weekly="${2:-4}"
  shift 2 || true
  local -a successful_sorted=()
  if (( $# > 0 )); then
    IFS=$'\n' successful_sorted=($(printf '%s\n' "$@" | LC_ALL=C sort -r))
    unset IFS
  fi

  declare -A protected=()
  local i=0
  for id in "${successful_sorted[@]}"; do
    [[ -z "$id" ]] && continue
    if (( i < keep_daily )); then
      protected["$id"]=1
      i=$((i + 1))
    fi
  done

  if (( ${#successful_sorted[@]} > 0 )); then
    local oldest_daily="${successful_sorted[$((keep_daily - 1))]:-${successful_sorted[-1]}}"
    local cutoff_week
    cutoff_week="$(date -u -d "${oldest_daily:0:8} 00:00:00" +%G-W%V 2>/dev/null || echo "")"
    declare -A week_pick=()
    for id in "${successful_sorted[@]}"; do
      [[ -z "$id" ]] && continue
      [[ -n "${protected[$id]:-}" ]] && continue
      local wk
      wk="$(date -u -d "${id:0:8} 00:00:00" +%G-W%V 2>/dev/null || echo "")"
      [[ -z "$wk" || "$wk" == "$cutoff_week" ]] && continue
      if [[ -z "${week_pick[$wk]:-}" ]]; then
        week_pick["$wk"]="$id"
      fi
    done
    local wcount=0
    for wk in $(printf '%s\n' "${!week_pick[@]}" | LC_ALL=C sort -r); do
      if (( wcount >= keep_weekly )); then
        break
      fi
      protected["${week_pick[$wk]}"]=1
      wcount=$((wcount + 1))
    done
  fi

  for id in "${successful_sorted[@]}"; do
    [[ -z "$id" ]] && continue
    if [[ -n "${protected[$id]:-}" ]]; then
      printf '%s\n' "$id"
    fi
  done
}

prune_snapshots() {
  local keep_daily keep_weekly max_failed
  keep_daily="$(env_default BACKUP_KEEP_DAILY 7)"
  keep_weekly="$(env_default BACKUP_KEEP_WEEKLY 4)"
  max_failed="$(env_default BACKUP_MAX_FAILED_SNAPSHOTS 3)"

  local snap_root tmp_root
  snap_root="$(snapshots_dir)"
  tmp_root="$(snapshot_tmp_dir)"

  if [[ -d "$tmp_root" ]] && [[ -n "$(ls -A "$tmp_root" 2>/dev/null || true)" ]]; then
    log_warn "prune skipped: in-progress backup under $tmp_root"
    return 0
  fi

  if ! acquire_backup_lock; then
    log_warn "prune skipped: backup lock held"
    return 0
  fi

  local -a successful=()
  local -a failed=()
  local -a orphan=()

  shopt -s nullglob
  for dir in "$snap_root"/*/; do
    [[ -d "$dir" ]] || continue
    local base
    base="$(basename "$dir")"
    [[ "$base" == ".tmp" ]] && continue
    local manifest="${dir%/}/manifest.json"
    if [[ ! -f "$manifest" ]]; then
      orphan+=("$base")
      continue
    fi
    local status
    status="$(read_manifest_field "$manifest" '.verify_status')"
    if [[ "$status" == "ok" ]]; then
      successful+=("$base")
    else
      failed+=("$base")
    fi
  done
  shopt -u nullglob

  local failed_count=$(( ${#failed[@]} + ${#orphan[@]} ))
  if (( failed_count > max_failed )); then
    log_warn "${failed_count} failed/orphan snapshot dirs exceed BACKUP_MAX_FAILED_SNAPSHOTS=${max_failed}"
  fi

  declare -A protected=()
  local id
  while IFS= read -r id; do
    [[ -z "$id" ]] && continue
    protected["$id"]=1
  done < <(compute_protected_snapshot_ids "$keep_daily" "$keep_weekly" "${successful[@]}")

  IFS=$'\n' successful_sorted=($(printf '%s\n' "${successful[@]}" | LC_ALL=C sort -r))
  unset IFS

  for id in "${successful_sorted[@]}"; do
    [[ -z "$id" ]] && continue
    if [[ -z "${protected[$id]:-}" ]]; then
      log_info "prune removing snapshot $id"
      rm -rf "${snap_root}/${id}"
    fi
  done

  release_backup_lock
}

falkordb_data_dir() {
  echo "${FALKORDB_DATA_DIR:-/var/lib/falkordb/data}"
}

falkordb_dump_rdb_path() {
  local dir dump
  dir="$(falkordb_data_dir)"
  dump="${dir}/dump.rdb"
  if [[ -s "$dump" ]]; then
    echo "$dump"
    return 0
  fi
  if [[ -s /falkordb-data/dump.rdb ]]; then
    echo /falkordb-data/dump.rdb
    return 0
  fi
  dump="$(find "$dir" /falkordb-data -maxdepth 2 -name dump.rdb -size +0c 2>/dev/null | head -1 || true)"
  if [[ -n "$dump" ]]; then
    echo "$dump"
    return 0
  fi
  return 1
}

falkordb_redis_check_rdb_bin() {
  echo "${FALKORDB_REDIS_CHECK_RDB_BIN:-/usr/local/bin/redis-check-rdb-v13}"
}

falkordb_redis_check_rdb_loader() {
  echo "${FALKORDB_REDIS_CHECK_RDB_LOADER:-/opt/falkordb-redis-check/ld-linux-x86-64.so.2}"
}

falkordb_redis_check_rdb_libdir() {
  echo "${FALKORDB_REDIS_CHECK_RDB_LIBDIR:-/opt/falkordb-redis-check/lib}"
}

falkordb_redis_check_rdb_ready() {
  local bin loader libdir
  bin="$(falkordb_redis_check_rdb_bin)"
  loader="$(falkordb_redis_check_rdb_loader)"
  libdir="$(falkordb_redis_check_rdb_libdir)"
  [[ -x "$bin" && -x "$loader" && -d "$libdir" ]]
}

falkordb_redis_check_rdb() {
  local file="$1"
  local bin loader libdir
  bin="$(falkordb_redis_check_rdb_bin)"
  loader="$(falkordb_redis_check_rdb_loader)"
  libdir="$(falkordb_redis_check_rdb_libdir)"
  if falkordb_redis_check_rdb_ready; then
    "$loader" --library-path "$libdir" "$bin" "$file"
  else
    "$bin" "$file"
  fi
}
