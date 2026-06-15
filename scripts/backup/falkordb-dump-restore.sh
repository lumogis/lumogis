#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Fallback FalkorDB export/import via redis-cli GRAPH commands (LUM-185).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

FALKOR_HOST="${FALKORDB_HOST:-falkordb}"
FALKOR_PORT="${FALKORDB_PORT:-6379}"
REDIS_CLI=(redis-cli -h "$FALKOR_HOST" -p "$FALKOR_PORT")

cmd="${1:-}"
out="${2:-}"

usage() {
  echo "Usage: falkordb-dump-restore.sh export <output.rdb-path> | import <input.rdb-path>" >&2
  exit 2
}

export_graph() {
  local dest="$1"
  mkdir -p "$(dirname "$dest")"
  "${REDIS_CLI[@]}" BGSAVE >/dev/null
  local last tries=0
  last="$("${REDIS_CLI[@]}" LASTSAVE)"
  while (( tries < 45 )); do
    dump_src="$(falkordb_dump_rdb_path || true)"
    if [[ -n "$dump_src" ]]; then
      cp "$dump_src" "$dest"
      return 0
    fi
    sleep 1
    local last2
    last2="$("${REDIS_CLI[@]}" LASTSAVE)"
    if [[ "$last2" != "$last" ]]; then
      dump_src="$(falkordb_dump_rdb_path || true)"
      if [[ -n "$dump_src" ]]; then
        cp "$dump_src" "$dest"
        return 0
      fi
    fi
    tries=$((tries + 1))
  done
  log_error "DUMP fallback: timed out waiting for dump.rdb"
  return 1
}

import_graph() {
  local src="$1"
  if [[ ! -s "$src" ]]; then
    log_error "import source empty: $src"
    return 1
  fi
  local dest_dir dump_dest
  dest_dir="$(falkordb_data_dir)"
  dump_dest="${dest_dir}/dump.rdb"
  if [[ -d "$dest_dir" || -d /falkordb-data ]]; then
    mkdir -p "$dest_dir"
    cp "$src" "$dump_dest"
    log_info "copied dump.rdb to ${dump_dest} — restart falkordb to load"
    return 0
  fi
  log_error "import fallback requires falkordb_data volume mount at ${dest_dir} or /falkordb-data"
  return 1
}

case "$cmd" in
  export) [[ -n "$out" ]] || usage; export_graph "$out" ;;
  import) [[ -n "$out" ]] || usage; import_graph "$out" ;;
  *) usage ;;
esac
