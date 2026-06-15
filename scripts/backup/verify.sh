#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

usage() {
  echo "Usage: verify.sh <snapshot-dir> [--rewrite-manifest]" >&2
  exit 2
}

rewrite=false
snapshot_dir=""
for arg in "$@"; do
  case "$arg" in
    --rewrite-manifest) rewrite=true ;;
    -h | --help) usage ;;
    *)
      if [[ -z "$snapshot_dir" ]]; then
        snapshot_dir="$arg"
      else
        usage
      fi
      ;;
  esac
done

[[ -n "$snapshot_dir" ]] || usage
validate_snapshot_path "$snapshot_dir"

manifest="${snapshot_dir%/}/manifest.json"
errors=()

if [[ ! -f "$manifest" ]]; then
  errors+=("manifest.json missing")
else
  schema="$(read_manifest_field "$manifest" '.schema_version')"
  if [[ "$schema" != "1" ]]; then
    errors+=("unsupported schema_version: ${schema:-null}")
  fi
fi

pg_dump="${snapshot_dir%/}/postgres.dump"
if [[ ! -s "$pg_dump" ]]; then
  errors+=("postgres.dump missing or empty")
else
  if ! pg_restore --list "$pg_dump" >/dev/null 2>&1; then
    errors+=("pg_restore --list failed on postgres.dump")
  fi
fi

include_falkor="$(resolve_backup_include_falkordb)"
falkor_skipped="$(read_manifest_field "$manifest" '.stores.falkordb.skipped')"
falkor_file="${snapshot_dir%/}/$(read_manifest_field "$manifest" '.stores.falkordb.file')"
if [[ "$include_falkor" == "true" && "$falkor_skipped" != "true" ]]; then
  if [[ ! -s "$falkor_file" ]]; then
    errors+=("falkordb artefact missing or empty")
  elif falkordb_redis_check_rdb_ready; then
    check_out=""
    check_rc=0
    check_out="$(falkordb_redis_check_rdb "$falkor_file" 2>&1)" || check_rc=$?
    if (( check_rc != 0 )); then
      if grep -qiE 'not found|No such file|error while loading shared libraries|GLIBC_|GLIBCXX_' <<<"$check_out"; then
        log_warn "redis-check-rdb-v13 toolchain could not run; skipping falkordb RDB envelope check"
      else
        errors+=("redis-check-rdb-v13 failed on falkordb artefact")
      fi
    fi
  elif [[ -x "$(falkordb_redis_check_rdb_bin)" ]]; then
    log_warn "redis-check-rdb-v13 present but loader or libdir missing; skipping falkordb RDB envelope check"
  else
    log_warn "redis-check-rdb-v13 missing or not executable; skipping falkordb RDB envelope check"
  fi
fi

qdrant_files="$(read_manifest_field "$manifest" '.stores.qdrant.files | keys[]' 2>/dev/null || true)"
if [[ -n "$qdrant_files" ]]; then
  while IFS= read -r coll; do
    [[ -z "$coll" ]] && continue
    rel="$(jq -r --arg c "$coll" '.stores.qdrant.files[$c]' "$manifest")"
    path="${snapshot_dir%/}/${rel}"
    if [[ ! -s "$path" ]]; then
      errors+=("qdrant snapshot missing for ${coll}")
    fi
  done <<<"$qdrant_files"
fi

status="ok"
if ((${#errors[@]} > 0)); then
  status="failed"
fi

if [[ "$rewrite" == "true" && -f "$manifest" ]]; then
  tmp="$(mktemp)"
  jq --arg st "$status" \
    --arg at "$(iso_now_utc)" \
    --argjson errs "$(printf '%s\n' "${errors[@]}" | jq -R . | jq -s .)" \
    '.verify_status = $st | .verified_at = $at | .verify_errors = $errs' \
    "$manifest" >"$tmp"
  mv "$tmp" "$manifest"
fi

if ((${#errors[@]} > 0)); then
  printf '%s\n' "${errors[@]}" >&2
  exit 1
fi

exit 0
