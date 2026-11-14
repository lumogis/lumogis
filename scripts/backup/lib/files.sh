#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Files store of the DR backup (LUM-892): snapshot, verify and restore of the
# workspace tree (uploads, inbox, captures, ...). Sourced after lib/common.sh by
# backup.sh, verify.sh and restore.sh.
#
# Layout inside a snapshot (manifest schema 2):
#   files/workspace/   the tree, hard-linked against the previous snapshot
#   files.list         sha256sum -c format, one line per file
#   files.inodes       "<inode>:<size>:<mtime> <sha256>" -- hash cache, no names
#
# Contract: stdout is reserved for return values (JSON, paths, error lines);
# every log line goes to stderr.

files_workspace_root() {
  printf '%s' "${WORKSPACE_PATH:-/workspace}"
}

# rsync exclude args, one per line. `--warn` also logs the BACKUP_HOST_DIR hint
# (callers that only size the copy stay quiet).
files_exclude_args() {
  local warn=false
  [[ "${1:-}" == "--warn" ]] && warn=true
  local root backup_root
  root="$(files_workspace_root)"
  backup_root="$(backup_root_dir)"
  printf '%s\n' '--exclude=/backups/' '--exclude=/mcp/'
  [[ -d "$root" && -d "$backup_root" ]] || return 0
  local d rel
  while IFS= read -r -d '' d; do
    [[ "$d" -ef "$backup_root" ]] || continue
    rel="${d#"$root"/}"
    case "$rel" in
      backups | mcp) continue ;;
    esac
    printf '%s\n' "--exclude=/${rel}/"
    if [[ "$warn" == "true" ]]; then
      log_warn "BACKUP_HOST_DIR is inside the workspace at ${rel}/ — the orchestrator's ownership repair walks it on every boot; move it outside the workspace or back to the default"
    fi
  # Depth 3 covers the realistic placements (ai-workspace/<a>/<b>/backups); a backup
  # root nested deeper would be copied into itself, and the guide says not to.
  done < <(find "$root" -mindepth 1 -maxdepth 3 -type d -print0 2>/dev/null)
  return 0
}

# Newest finalised snapshot that holds a file tree AND passed its final verify.
# Linking against a snapshot that failed verification would carry its bytes
# forward: rsync's quick check (size + mtime) cannot see bit rot, so a corrupt
# inode would be linked into the new snapshot and then blessed by a fresh
# files.list. With no ok snapshot the next one is a full copy from the workspace.
files_previous_snapshot() {
  local snap_root id dir
  snap_root="$(snapshots_dir)"
  [[ -d "$snap_root" ]] || return 0
  while IFS= read -r id; do
    dir="${snap_root}/${id}"
    [[ -f "${dir}/manifest.json" && -d "${dir}/files/workspace" ]] || continue
    if [[ -n "$(read_manifest_field "${dir}/manifest.json" '.stores.files.dir')" \
      && "$(read_manifest_field "${dir}/manifest.json" '.verify_status')" == "ok" ]]; then
      printf '%s\n' "$dir"
      return 0
    fi
  done < <(find "$snap_root" -mindepth 1 -maxdepth 1 -type d ! -name '.tmp' -printf '%f\n' | LC_ALL=C sort -r)
  return 0
}

# rsync owner/permission args, one per line.
#   snapshot: root-owned, read-only files. rsync ignores owner changes for
#             non-root, so --chown is left out rather than relied on.
#   fallback: for backup disks that refuse ownership and mode changes
#             (CIFS/NTFS, some Docker Desktop binds).
# Group/other write is stripped from directories too (a 0777 source directory
# would otherwise stay writable by the orchestrator uid inside a snapshot), and
# setuid/setgid bits are dropped from files. Root keeps write, so rsync and prune
# work unchanged.
files_owner_args() {
  case "${1:-snapshot}" in
    snapshot)
      if [[ "$(id -u)" == "0" ]]; then
        printf '%s\n' '--chown=0:0'
      fi
      printf '%s\n' '--chmod=Fa-w,Fa-s,Dgo-w'
      ;;
    fallback)
      printf '%s\n' '--no-owner' '--no-group' '--no-perms'
      ;;
    *)
      return 1
      ;;
  esac
}

# Bytes a snapshot would add (rsync's "Total transferred file size").
# Prints 0 when the workspace is absent or rsync is missing; files_snapshot
# fails loudly in those cases.
files_estimate_bytes() {
  local prev="${1:-}" root
  root="$(files_workspace_root)"
  if [[ ! -d "$root" ]] || ! command -v rsync >/dev/null 2>&1; then
    echo 0
    return 0
  fi
  local -a owner=() excl=() link=()
  mapfile -t owner < <(files_owner_args snapshot)
  mapfile -t excl < <(files_exclude_args)
  [[ -n "$prev" ]] && link=("--link-dest=${prev}/files/workspace")
  local scratch out n
  scratch="$(mktemp -d)"
  out="$(rsync -an --stats --no-human-readable --numeric-ids --hard-links \
    "${owner[@]}" ${link[@]+"${link[@]}"} "${excl[@]}" "${root}/" "${scratch}/" 2>/dev/null)" || true
  rm -rf "$scratch"
  n="$(printf '%s\n' "$out" | awk -F': ' '/^Total transferred file size/ {gsub(/[^0-9]/, "", $2); print $2; exit}')"
  echo "${n:-0}"
}

# Writes files.list (sha256sum -c format) and files.inodes for <new_tree>.
# Hashes are reused from the previous snapshot by "inode:size:mtime" key, but
# only when that snapshot's verify_status is ok: a suspect snapshot's hash cache
# is never trusted. Reused hashes are not re-read, so bit rot in a hard-linked
# file is only caught by `verify.sh --deep` (run it monthly; the guide says so).
files_hash_list() {
  local tree="$1" prev="${2:-}" out_list="$3" out_inodes="$4"
  local -A known=()
  if [[ -n "$prev" && -f "${prev}/files.inodes" \
    && "$(read_manifest_field "${prev}/manifest.json" '.verify_status')" == "ok" ]]; then
    local k s
    while IFS=' ' read -r k s; do
      [[ -n "$k" && -n "$s" ]] && known["$k"]="$s"
    done <"${prev}/files.inodes"
  fi

  local work
  work="$(mktemp -d)"
  local key rel sha
  exec 3>"${work}/list.reused" 4>"${work}/inodes.reused" 5>"${work}/todo.rels" 6>"${work}/todo.keys"
  while IFS= read -r -d '' key && IFS= read -r -d '' rel; do
    sha="${known[$key]:-}"
    # Names with a backslash or newline are always hashed: sha256sum escapes them
    # itself, and its escaping is what `sha256sum -c` expects back.
    if [[ -n "$sha" && "$rel" != *\\* && "$rel" != *$'\n'* ]]; then
      printf '%s  %s\n' "$sha" "$rel" >&3
      printf '%s %s\n' "$key" "$sha" >&4
    else
      printf '%s\0' "$rel" >&5
      printf '%s\n' "$key" >&6
    fi
  done < <(find "$tree" -type f -printf '%i:%s:%T@\0%P\0')
  exec 3>&- 4>&- 5>&- 6>&-

  : >"${work}/list.new"
  : >"${work}/inodes.new"
  if [[ -s "${work}/todo.rels" ]]; then
    if ! (cd "$tree" && xargs -0 sha256sum -- <"${work}/todo.rels") >"${work}/list.new"; then
      log_error "sha256sum failed while hashing the file tree"
      rm -rf "$work"
      return 1
    fi
    awk '{s = $1; sub(/^\\/, "", s); print s}' "${work}/list.new" >"${work}/shas.new"
    if [[ "$(wc -l <"${work}/shas.new")" != "$(wc -l <"${work}/todo.keys")" ]]; then
      log_error "hash list out of step with the file list"
      rm -rf "$work"
      return 1
    fi
    paste -d' ' "${work}/todo.keys" "${work}/shas.new" >"${work}/inodes.new"
  fi

  cat "${work}/list.reused" "${work}/list.new" | LC_ALL=C sort -k2 >"$out_list"
  cat "${work}/inodes.reused" "${work}/inodes.new" | LC_ALL=C sort >"$out_inodes"
  rm -rf "$work"
  return 0
}

# Prints one JSON object for the manifest's stores.files.
# Return codes: 0 files copied (status ok); 3 the copy failed after the
# fallback (status failed; the caller keeps the database snapshot); 1 a
# configuration error (rsync missing, workspace not mounted).
files_snapshot() {
  local tmp_dir="$1" root
  root="$(files_workspace_root)"
  if [[ ! -d "$root" ]]; then
    log_error "workspace not mounted at ${root} — update docker-compose.yml (LUM-892)"
    return 1
  fi
  if ! command -v rsync >/dev/null 2>&1; then
    log_error "rsync is not installed in the backup image — run: docker compose build backup (LUM-892)"
    return 1
  fi

  local prev prev_id=""
  prev="$(files_previous_snapshot)"
  [[ -n "$prev" ]] && prev_id="$(basename "$prev")"

  local -a owner=() excl=() link=()
  mapfile -t owner < <(files_owner_args snapshot)
  mapfile -t excl < <(files_exclude_args --warn)
  [[ -n "$prev" ]] && link=("--link-dest=${prev}/files/workspace")

  local dest="${tmp_dir}/files/workspace"
  local ownership="unchanged"
  [[ "$(id -u)" == "0" ]] && ownership="root_readonly"

  mkdir -p "$dest"
  local rc=0
  rsync -a --numeric-ids --hard-links "${owner[@]}" ${link[@]+"${link[@]}"} "${excl[@]}" \
    "${root}/" "${dest}/" >&2 || rc=$?
  if (( rc == 24 )); then
    log_warn "files vanished during copy (rsync exit 24); continuing"
    rc=0
  fi
  if (( rc != 0 )); then
    log_warn "file copy failed (rsync exit ${rc}); retrying without owner and permission changes"
    rm -rf "${tmp_dir}/files"
    mkdir -p "$dest"
    local -a fb=()
    mapfile -t fb < <(files_owner_args fallback)
    rc=0
    rsync -a --numeric-ids --hard-links "${fb[@]}" ${link[@]+"${link[@]}"} "${excl[@]}" \
      "${root}/" "${dest}/" >&2 || rc=$?
    if (( rc == 24 )); then
      log_warn "files vanished during copy (rsync exit 24); continuing"
      rc=0
    fi
    if (( rc != 0 )); then
      log_error "file copy failed (rsync exit ${rc}); this snapshot holds databases only"
      rm -rf "${tmp_dir}/files" "${tmp_dir}/files.list" "${tmp_dir}/files.inodes"
      jq -cn --argjson n "$rc" '{status: "failed", rsync_exit: $n}'
      return 3
    fi
    ownership="not_preserved"
  fi

  if ! files_hash_list "$dest" "$prev" "${tmp_dir}/files.list" "${tmp_dir}/files.inodes"; then
    rm -rf "${tmp_dir}/files" "${tmp_dir}/files.list" "${tmp_dir}/files.inodes"
    return 1
  fi

  local file_count bytes new_bytes hard_links="true"
  # One dot per file, so a name with a newline still counts once.
  file_count="$(find "$dest" -type f -printf '.' | wc -c | tr -d ' ')"
  bytes="$(find "$dest" -type f -printf '%s\n' | awk '{s += $1} END {printf "%.0f", s}')"
  new_bytes="$(find "$dest" -type f -links 1 -printf '%s\n' | awk '{s += $1} END {printf "%.0f", s}')"
  local prev_count=0
  [[ -n "$prev" ]] && prev_count="$(read_manifest_field "${prev}/manifest.json" '.stores.files.file_count')"
  if [[ -n "$prev" ]] && (( file_count > 0 && ${prev_count:-0} > 0 )) \
    && [[ -z "$(find "$dest" -type f -links +1 -print -quit)" ]]; then
    hard_links="false"
    log_warn "hard links unavailable at $(backup_root_dir): this snapshot is a full copy"
  fi

  local excludes_json
  excludes_json="$(printf '%s\n' "${excl[@]}" | sed 's|^--exclude=/||' | jq -R . | jq -sc .)"
  jq -cn \
    --argjson file_count "${file_count:-0}" \
    --argjson bytes "${bytes:-0}" \
    --argjson new_bytes "${new_bytes:-0}" \
    --arg linked "$prev_id" \
    --argjson hard_links "$hard_links" \
    --argjson excludes "$excludes_json" \
    --arg ownership "$ownership" \
    '{dir: "files/workspace", list: "files.list", inodes: "files.inodes",
      file_count: $file_count, bytes: $bytes, new_bytes: $new_bytes,
      linked_from: ($linked | if . == "" then null else . end),
      hard_links: $hard_links, excludes: $excludes,
      status: "ok", ownership: $ownership}'
  return 0
}

# Prints integrity errors for the snapshot's file tree, one per line; prints
# nothing when it is intact. Always returns 0 (the caller reads the output).
# `--deep` re-reads every file against files.list.
files_verify() {
  local snap deep=false
  # Absolute: the deep check cd's into the tree and must still find files.list.
  snap="$(cd "${1%/}" 2>/dev/null && pwd)" || { echo "snapshot directory not found: ${1}"; return 0; }
  [[ "${2:-}" == "--deep" ]] && deep=true
  local manifest="${snap}/manifest.json" rel_dir
  rel_dir="$(read_manifest_field "$manifest" '.stores.files.dir')"
  [[ -n "$rel_dir" ]] || return 0
  local tree="${snap}/${rel_dir}"
  if [[ ! -d "$tree" ]]; then
    echo "file tree missing: ${rel_dir}"
    return 0
  fi

  local want_count want_bytes list_rel list
  want_count="$(read_manifest_field "$manifest" '.stores.files.file_count')"
  want_bytes="$(read_manifest_field "$manifest" '.stores.files.bytes')"
  list_rel="$(read_manifest_field "$manifest" '.stores.files.list')"
  list="${snap}/${list_rel:-files.list}"

  local have_count have_bytes
  have_count="$(find "$tree" -type f -printf '.' | wc -c | tr -d ' ')"
  have_bytes="$(find "$tree" -type f -printf '%s\n' | awk '{s += $1} END {printf "%.0f", s}')"
  if [[ "$have_count" != "$want_count" ]]; then
    echo "file count mismatch: manifest ${want_count}, snapshot ${have_count}"
  fi
  if [[ "$have_bytes" != "$want_bytes" ]]; then
    echo "file bytes mismatch: manifest ${want_bytes}, snapshot ${have_bytes}"
  fi
  if [[ ! -f "$list" ]]; then
    echo "files.list missing"
    return 0
  fi
  local list_count
  list_count="$(wc -l <"$list" | tr -d ' ')"
  if [[ "$list_count" != "$want_count" ]]; then
    echo "files.list has ${list_count} lines, manifest says ${want_count}"
  fi

  if [[ "$deep" == "true" ]] && (( list_count > 0 )); then
    local out rc=0 line failed=0
    out="$(cd "$tree" && sha256sum -c --quiet "$list" 2>&1)" || rc=$?
    while IFS= read -r line; do
      if [[ "$line" == *': FAILED'* ]]; then
        echo "checksum mismatch: ${line}"
        failed=1
      fi
    done <<<"$out"
    # A non-zero exit with no FAILED line (unreadable list, unreadable file) is an
    # error too: the deep check must not pass because it could not run.
    if (( rc != 0 && failed == 0 )); then
      echo "deep check could not run: ${out:-sha256sum exit ${rc}}"
    fi
  fi
  return 0
}

# Copies the snapshot's file tree into the workspace. Never deletes, never
# hard-links: a changed file is moved to restore-displaced/<snap_id>/ first.
files_restore() {
  local snap="${1%/}" snap_id="$2" root manifest rel_dir
  root="$(files_workspace_root)"
  manifest="${snap}/manifest.json"
  rel_dir="$(read_manifest_field "$manifest" '.stores.files.dir')"
  local tree="${snap}/${rel_dir}"
  if [[ -z "$rel_dir" || ! -d "$tree" ]]; then
    log_error "snapshot has no file tree to restore"
    return 1
  fi
  if [[ ! -d "$root" ]]; then
    log_error "workspace not mounted at ${root} — update docker-compose.yml (LUM-892)"
    return 1
  fi

  # What the backup actually left out, plus what the current config would.
  local -a excl=('--exclude=/restore-displaced/')
  local e
  while IFS= read -r e; do
    [[ -n "$e" ]] && excl+=("--exclude=/${e}")
  done < <(jq -r '.stores.files.excludes[]? // empty' "$manifest")
  while IFS= read -r e; do
    [[ -n "$e" ]] && excl+=("$e")
  done < <(files_exclude_args)

  # Owner: the runtime user (.env reaches this container), else the owner of the
  # workspace. A restore on a new machine must not leave the old machine's uid.
  local uid gid
  if [[ -n "${LUMOGIS_RUN_UID:-}" ]]; then
    uid="$LUMOGIS_RUN_UID"
    gid="${LUMOGIS_RUN_GID:-$uid}"
  else
    read -r uid gid < <(stat -c '%u %g' "$root")
  fi
  local -a owner=()
  if [[ "$(id -u)" == "0" ]]; then
    owner=("--chown=${uid}:${gid}")
    log_info "restoring files as uid:gid ${uid}:${gid}"
    if [[ "$uid" == "0" ]]; then
      log_info "files are root-owned: the orchestrator hands them to its runtime user on its next boot"
    fi
  else
    owner=(--no-owner --no-group)
  fi

  local extras extras_list
  extras_list="$(rsync -an --delete --itemize-changes "${excl[@]}" "${tree}/" "${root}/" 2>/dev/null \
    | grep '^\*deleting' || true)"
  extras=0
  [[ -n "$extras_list" ]] && extras="$(printf '%s\n' "$extras_list" | wc -l | tr -d ' ')"

  local displaced_dir="${root}/restore-displaced/${snap_id}"
  # rsync would follow a symlink here as root. The orchestrator uid owns the
  # workspace and could have planted one; refuse rather than write through it.
  if [[ -L "${root}/restore-displaced" || -L "$displaced_dir" ]]; then
    log_error "${root}/restore-displaced is a symlink; remove it and re-run restore"
    return 1
  fi
  local out rc=0
  out="$(rsync -a --numeric-ids --itemize-changes "${owner[@]}" --chmod=Du+w,Fu+w \
    --backup "--backup-dir=${displaced_dir}" "${excl[@]}" "${tree}/" "${root}/")" || rc=$?
  if (( rc != 0 && rc != 24 )); then
    log_error "file restore failed (rsync exit ${rc})"
    return 1
  fi

  local restored displaced=0
  restored="$(printf '%s\n' "$out" | grep -c '^>f' || true)"
  if [[ -d "$displaced_dir" ]]; then
    displaced="$(find "$displaced_dir" -type f | wc -l | tr -d ' ')"
  fi
  log_info "files restored: ${restored}; displaced (changed since the snapshot): ${displaced}; extra files kept: ${extras}"
  if (( displaced > 0 )); then
    log_info "displaced versions are under ${root}/restore-displaced/${snap_id}/"
  fi
  if (( extras > 0 )); then
    log_info "files in the workspace that are not in the snapshot (kept, first 20):"
    printf '%s\n' "$extras_list" | head -20 | sed 's/^\*deleting *//' | while IFS= read -r e; do
      log_info "  ${e}"
    done
  fi
  return 0
}
