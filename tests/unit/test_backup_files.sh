#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# LUM-892 — files store of the DR backup (scripts/backup/lib/files.sh).
# Needs rsync and sha256sum on PATH; no Docker. The root-only block runs when
# `id -u` is 0 (the round trip runs this file inside the backup image).
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

for tool in rsync sha256sum cmp jq; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "test_backup_files: ${tool} not on PATH — install it (apt install rsync coreutils diffutils jq)" >&2
    exit 2
  fi
done

# shellcheck source=scripts/backup/lib/common.sh
source "${REPO_ROOT}/scripts/backup/lib/common.sh"
# shellcheck source=scripts/backup/lib/files.sh
source "${REPO_ROOT}/scripts/backup/lib/files.sh"
set +e # assertions count failures; they do not abort

REAL_SHA256SUM="$(command -v sha256sum)"
REAL_RSYNC="$(command -v rsync)"
ORIG_PATH="$PATH"
failures=0
passes=0
TMPS=()

cleanup() {
  local t
  for t in "${TMPS[@]}"; do
    chmod -R u+w "$t" 2>/dev/null || true
    rm -rf "$t"
  done
}
trap cleanup EXIT

assert_eq() {
  local label="$1" expected="$2" actual="$3"
  if [[ "$expected" != "$actual" ]]; then
    echo "FAIL: ${label} (expected '${expected}', got '${actual}')" >&2
    failures=$((failures + 1))
  else
    passes=$((passes + 1))
  fi
}

assert_contains() {
  local label="$1" needle="$2" haystack="$3"
  if [[ "$haystack" != *"$needle"* ]]; then
    echo "FAIL: ${label} (missing '${needle}' in: ${haystack})" >&2
    failures=$((failures + 1))
  else
    passes=$((passes + 1))
  fi
}

assert_not_contains() {
  local label="$1" needle="$2" haystack="$3"
  if [[ "$haystack" == *"$needle"* ]]; then
    echo "FAIL: ${label} (unexpected '${needle}' in: ${haystack})" >&2
    failures=$((failures + 1))
  else
    passes=$((passes + 1))
  fi
}

assert_exists() {
  local label="$1" path="$2"
  if [[ ! -e "$path" ]]; then
    echo "FAIL: ${label} (missing ${path})" >&2
    failures=$((failures + 1))
  else
    passes=$((passes + 1))
  fi
}

assert_absent() {
  local label="$1" path="$2"
  if [[ -e "$path" ]]; then
    echo "FAIL: ${label} (unexpected ${path})" >&2
    failures=$((failures + 1))
  else
    passes=$((passes + 1))
  fi
}

# Fresh workspace + a backup root OUTSIDE it. Sets T, WS, BK.
new_env() {
  T="$(mktemp -d)"
  TMPS+=("$T")
  chmod 755 "$T"
  WS="${T}/ws"
  BK="${T}/bk"
  mkdir -p "$WS" "$BK/snapshots"
  export WORKSPACE_PATH="$WS" BACKUP_DIR="$BK"
  unset LUMOGIS_RUN_UID LUMOGIS_RUN_GID
  PATH="$ORIG_PATH"
  SHA_LOG="${T}/sha.log"
  : >"$SHA_LOG"
}

put() { # put <relpath> <content>
  mkdir -p "$(dirname "${WS}/$1")"
  printf '%s' "$2" >"${WS}/$1"
}

# Takes a snapshot and finalises it under $BACKUP_DIR/snapshots/<id>.
# Sets SNAP_RC, SNAP_JSON, SNAP_ERR.
take_snapshot() { # take_snapshot <id> [verify_status]
  local id="$1" st="${2:-ok}"
  local tmp
  tmp="$(snapshots_dir)/.tmp/${id}"
  mkdir -p "$tmp"
  SNAP_RC=0
  SNAP_JSON="$(files_snapshot "$tmp" 2>"${T}/snap.err")" || SNAP_RC=$?
  SNAP_ERR="$(cat "${T}/snap.err")"
  jq -n --arg st "$st" --argjson files "${SNAP_JSON:-null}" \
    '{schema_version: 2, verify_status: $st, stores: {files: $files}}' >"${tmp}/manifest.json"
  mv "$tmp" "$(snapshots_dir)/${id}"
}

snap_dir() { echo "$(snapshots_dir)/$1"; }

set_verify_status() { # set_verify_status <id> <status>
  local m
  m="$(snap_dir "$1")/manifest.json"
  jq --arg st "$2" '.verify_status = $st' "$m" >"${m}.new" && mv "${m}.new" "$m"
}

# PATH shim: a fake `name` that logs and optionally changes behaviour.
shim() { # shim <name> <body>  (body sees $REAL)
  mkdir -p "${T}/shims"
  printf '#!/usr/bin/env bash\n%s\n' "$2" >"${T}/shims/$1"
  chmod +x "${T}/shims/$1"
  PATH="${T}/shims:${ORIG_PATH}"
}

count_lines() { if [[ -s "$1" ]]; then wc -l <"$1" | tr -d ' '; else echo 0; fi; }

# ---------------------------------------------------------------------------

test_snapshot_copies_tree_minus_excludes() {
  new_env
  put "uploads/a.pdf" "pdf"
  put "uploads/Ünïcode name.pdf" "unicode"
  put "inbox/b.txt" "inbox"
  put "captures/u/c/a/blob.jpg" "jpg"
  put "backups/x" "backup"
  put "mcp/t.env" "token"
  put "deep/backups/keep.txt" "keep"
  take_snapshot 20260101-000000
  local tree
  tree="$(snap_dir 20260101-000000)/files/workspace"
  assert_eq "snapshot rc" 0 "$SNAP_RC"
  assert_exists "uploads kept" "${tree}/uploads/a.pdf"
  assert_exists "unicode+space name kept" "${tree}/uploads/Ünïcode name.pdf"
  assert_exists "inbox kept" "${tree}/inbox/b.txt"
  assert_exists "captures kept" "${tree}/captures/u/c/a/blob.jpg"
  assert_exists "nested backups dir kept" "${tree}/deep/backups/keep.txt"
  assert_absent "backups excluded" "${tree}/backups/x"
  assert_absent "mcp excluded" "${tree}/mcp/t.env"
  assert_eq "status ok" ok "$(jq -r .status <<<"$SNAP_JSON")"
  assert_eq "file_count" 5 "$(jq -r .file_count <<<"$SNAP_JSON")"
  assert_eq "excludes recorded" '["backups/","mcp/"]' "$(jq -c .excludes <<<"$SNAP_JSON")"
}

test_backup_root_inside_workspace_under_other_name_is_excluded() {
  new_env
  export BACKUP_DIR="${WS}/dr"
  mkdir -p "${WS}/dr/snapshots"
  put "uploads/a.pdf" "pdf"
  put "dr/marker" "should not be copied"
  take_snapshot 20260101-000000
  local tree
  tree="$(snap_dir 20260101-000000)/files/workspace"
  assert_exists "uploads kept" "${tree}/uploads/a.pdf"
  assert_absent "backup root excluded" "${tree}/dr"
  assert_contains "excludes list names dr/" '"dr/"' "$(jq -c .excludes <<<"$SNAP_JSON")"
  # backup_root_inside_workspace_warns — once
  local n
  n="$(grep -c 'BACKUP_HOST_DIR is inside the workspace at dr/' <<<"$SNAP_ERR" || true)"
  assert_eq "inside-workspace WARN logged once" 1 "$n"
}

test_second_snapshot_hard_links_unchanged_files() {
  new_env
  put "uploads/same.bin" "unchanged-content"
  put "uploads/changed.bin" "first"
  take_snapshot 20260101-000000
  put "uploads/changed.bin" "second-longer"
  take_snapshot 20260102-000000
  local t2
  t2="$(snap_dir 20260102-000000)/files/workspace"
  assert_eq "unchanged file is hard linked" 2 "$(stat -c %h "${t2}/uploads/same.bin")"
  assert_eq "changed file is a new inode" 1 "$(stat -c %h "${t2}/uploads/changed.bin")"
  assert_eq "new_bytes is the changed file only" 13 "$(jq -r .new_bytes <<<"$SNAP_JSON")"
  assert_eq "linked_from" 20260101-000000 "$(jq -r .linked_from <<<"$SNAP_JSON")"
  assert_eq "hard_links true" true "$(jq -r .hard_links <<<"$SNAP_JSON")"
}

test_hash_list_reuses_by_inode_and_matches_sha256sum() {
  new_env
  put "uploads/same.bin" "unchanged-content"
  put "uploads/changed.bin" "first"
  take_snapshot 20260101-000000
  put "uploads/changed.bin" "second-longer"
  shim sha256sum "echo \"\$@\" >>'${SHA_LOG}'; exec '${REAL_SHA256SUM}' \"\$@\""
  take_snapshot 20260102-000000
  PATH="$ORIG_PATH"
  local snap
  snap="$(snap_dir 20260102-000000)"
  assert_eq "sha256sum -c passes" 0 "$(cd "${snap}/files/workspace" && "$REAL_SHA256SUM" -c --quiet "${snap}/files.list" >/dev/null 2>&1; echo $?)"
  assert_contains "changed file hashed" "uploads/changed.bin" "$(cat "$SHA_LOG")"
  assert_not_contains "unchanged file reused, not re-read" "uploads/same.bin" "$(cat "$SHA_LOG")"
}

test_hash_list_handles_newline_and_backslash_names() {
  new_env
  put $'odd/a\nb' "newline"
  put 'odd/c\d' "backslash"
  put "odd/plain" "plain"
  take_snapshot 20260101-000000
  take_snapshot 20260102-000000 # second run re-hashes the odd names even with a cache
  local snap
  snap="$(snap_dir 20260102-000000)"
  assert_eq "sha256sum -c passes with odd names" 0 \
    "$(cd "${snap}/files/workspace" && "$REAL_SHA256SUM" -c --quiet "${snap}/files.list" >/dev/null 2>&1; echo $?)"
  assert_eq "list has one line per file" 3 "$(count_lines "${snap}/files.list")"
}

test_hash_list_does_not_reuse_on_size_or_mtime_change() {
  new_env
  put "uploads/f.bin" "first-content"
  take_snapshot 20260101-000000
  # Poison the cache: same file, but the stored key claims a different size.
  local inodes
  inodes="$(snap_dir 20260101-000000)/files.inodes"
  awk '{ split($1, p, ":"); print p[1] ":" (p[2] + 1) ":" p[3] " deadbeef" }' "$inodes" >"${inodes}.new" && mv "${inodes}.new" "$inodes"
  shim sha256sum "echo \"\$@\" >>'${SHA_LOG}'; exec '${REAL_SHA256SUM}' \"\$@\""
  take_snapshot 20260102-000000
  PATH="$ORIG_PATH"
  local snap
  snap="$(snap_dir 20260102-000000)"
  assert_contains "mismatched key forces a re-hash" "uploads/f.bin" "$(cat "$SHA_LOG")"
  assert_eq "sha256sum -c passes (poisoned hash not reused)" 0 \
    "$(cd "${snap}/files/workspace" && "$REAL_SHA256SUM" -c --quiet "${snap}/files.list" >/dev/null 2>&1; echo $?)"
}

test_estimate_counts_only_delta() {
  new_env
  put "uploads/old.bin" "$(head -c 500 /dev/zero | tr '\0' a)"
  take_snapshot 20260101-000000
  local prev
  prev="$(files_previous_snapshot)"
  put "uploads/new.bin" "$(head -c 1000 /dev/zero | tr '\0' b)"
  assert_eq "delta only" 1000 "$(files_estimate_bytes "$prev")"
  assert_eq "no previous snapshot: whole tree" 1500 "$(files_estimate_bytes "")"
  assert_eq "no workspace: 0" 0 "$(WORKSPACE_PATH="${T}/nope" files_estimate_bytes "")"
}

test_verify_detects_count_mismatch() {
  new_env
  put "uploads/a" "aaa"
  put "uploads/b" "bbb"
  take_snapshot 20260101-000000
  local snap
  snap="$(snap_dir 20260101-000000)"
  assert_eq "intact snapshot verifies clean" "" "$(files_verify "$snap")"
  rm -f "${snap}/files/workspace/uploads/b"
  assert_contains "missing file reported" "file count mismatch" "$(files_verify "$snap")"
}

test_verify_deep_detects_content_change() {
  new_env
  put "uploads/a" "aaa"
  put "uploads/b" "bbb"
  take_snapshot 20260101-000000
  local snap
  snap="$(snap_dir 20260101-000000)"
  local f="${snap}/files/workspace/uploads/a"
  cp "$f" "${f}.new" && chmod u+w "${f}.new" && printf 'X' | dd of="${f}.new" bs=1 count=1 conv=notrunc 2>/dev/null && mv -f "${f}.new" "$f"
  assert_eq "shallow verify misses a same-size change" "" "$(files_verify "$snap")"
  assert_contains "deep verify catches it" "uploads/a" "$(files_verify "$snap" --deep)"
}

test_restore_never_deletes_and_displaces_changed() {
  new_env
  put "uploads/a.pdf" "snapshot-bytes"
  take_snapshot 20260101-000000
  put "uploads/a.pdf" "newer-bytes-changed-since"
  put "extra.txt" "only in workspace"
  local log
  log="$(files_restore "$(snap_dir 20260101-000000)" 20260101-000000 2>&1)"
  assert_exists "extra file kept" "${WS}/extra.txt"
  assert_eq "snapshot bytes back" "snapshot-bytes" "$(cat "${WS}/uploads/a.pdf")"
  assert_eq "old bytes displaced" "newer-bytes-changed-since" \
    "$(cat "${WS}/restore-displaced/20260101-000000/uploads/a.pdf")"
  assert_contains "extra file reported" "extra.txt" "$log"
}

# LUM-922: the admin's stop-entities list lives at <workspace>/config. A restore
# onto a fresh install must put the curated list back over the shipped one the
# orchestrator copied there on its first boot.
test_stop_entities_list_round_trips_over_a_reseeded_default() {
  new_env
  put "config/stop_entities.txt" "curated: project phoenix"
  take_snapshot 20260101-000000
  assert_exists "list in snapshot" "$(snap_dir 20260101-000000)/files/workspace/config/stop_entities.txt"
  rm -rf "${WS:?}/config"
  put "config/stop_entities.txt" "shipped: the meeting"
  files_restore "$(snap_dir 20260101-000000)" 20260101-000000 >/dev/null 2>&1
  assert_eq "curated list back" "curated: project phoenix" "$(cat "${WS}/config/stop_entities.txt")"
  assert_eq "shipped list displaced, not deleted" "shipped: the meeting" \
    "$(cat "${WS}/restore-displaced/20260101-000000/config/stop_entities.txt")"
}

test_restore_copies_not_links() {
  new_env
  put "uploads/a.pdf" "snapshot-bytes"
  take_snapshot 20260101-000000
  rm -f "${WS}/uploads/a.pdf"
  files_restore "$(snap_dir 20260101-000000)" 20260101-000000 >/dev/null 2>&1
  assert_eq "restored file is not hard linked" 1 "$(stat -c %h "${WS}/uploads/a.pdf")"
  printf 'more' >>"${WS}/uploads/a.pdf"
  assert_eq "snapshot file unchanged by a workspace edit" "snapshot-bytes" \
    "$(cat "$(snap_dir 20260101-000000)/files/workspace/uploads/a.pdf")"
}

test_restore_uses_manifest_excludes() {
  new_env
  export BACKUP_DIR="${WS}/dr"
  mkdir -p "${WS}/dr/snapshots"
  put "uploads/a.pdf" "x"
  take_snapshot 20260101-000000
  # The backup root moves outside the workspace; the old location must stay protected.
  export BACKUP_DIR="${BK}"
  mkdir -p "${BK}/snapshots"
  mv "${WS}/dr/snapshots/20260101-000000" "${BK}/snapshots/"
  put "dr/x" "leftover of the old backup root"
  local log
  log="$(files_restore "${BK}/snapshots/20260101-000000" 20260101-000000 2>&1)"
  assert_not_contains "old backup root not reported as extra" "dr/x" "$log"
  assert_absent "old backup root not displaced" "${WS}/restore-displaced/20260101-000000/dr"
  assert_exists "old backup root untouched" "${WS}/dr/x"
}

test_manifest_schema_supported_accepts_1_and_2_only() {
  manifest_schema_supported 1 && manifest_schema_supported 2
  assert_eq "1 and 2 ok" 0 $?
  manifest_schema_supported 3
  assert_eq "3 rejected" 1 $?
  manifest_schema_supported ""
  assert_eq "empty rejected" 1 $?
}

test_previous_snapshot_uses_newest_ok_snapshot_with_files() {
  new_env
  put "uploads/a" "aaa"
  take_snapshot 20260102-000000 ok
  take_snapshot 20260103-000000 failed
  # A v1 snapshot (no files) and a v2 whose file copy failed (no dir), both newer or older.
  mkdir -p "$(snap_dir 20260101-000000)"
  echo '{"schema_version":1,"verify_status":"ok","stores":{}}' >"$(snap_dir 20260101-000000)/manifest.json"
  mkdir -p "$(snap_dir 20260104-000000)"
  echo '{"schema_version":2,"verify_status":"ok","stores":{"files":{"status":"failed","rsync_exit":12}}}' \
    >"$(snap_dir 20260104-000000)/manifest.json"
  assert_eq "newest ok snapshot with a tree; a verify-failed one is never linked against" \
    "$(snap_dir 20260102-000000)" "$(files_previous_snapshot)"
}

test_corruption_in_a_failed_snapshot_is_not_carried_forward() {
  new_env
  put "uploads/f.pdf" "good-content"
  take_snapshot 20260101-000000
  # Bit rot in S1: same size and mtime, different bytes (quick check cannot see it).
  local f
  f="$(snap_dir 20260101-000000)/files/workspace/uploads/f.pdf"
  local mt
  mt="$(stat -c %Y "$f")"
  cp "$f" "${f}.new" && chmod u+w "${f}.new" && printf 'BAD!' | dd of="${f}.new" bs=1 count=4 conv=notrunc 2>/dev/null \
    && mv -f "${f}.new" "$f" && touch -d "@${mt}" "$f"
  assert_contains "deep verify flags S1" "uploads/f.pdf" "$(files_verify "$(snap_dir 20260101-000000)" --deep)"
  set_verify_status 20260101-000000 failed
  take_snapshot 20260102-000000
  assert_eq "S2 is a full copy, not linked from the failed S1" null "$(jq -r '.linked_from // "null"' <<<"$SNAP_JSON")"
  assert_eq "S2 holds the good bytes" "good-content" \
    "$(cat "$(snap_dir 20260102-000000)/files/workspace/uploads/f.pdf")"
  assert_eq "S2 deep-verifies clean" "" "$(files_verify "$(snap_dir 20260102-000000)" --deep)"
}

test_newline_in_a_file_name_counts_once() {
  new_env
  put $'odd/a\nb' "newline"
  put "odd/plain" "plain"
  take_snapshot 20260101-000000
  assert_eq "file_count counts the newline name once" 2 "$(jq -r .file_count <<<"$SNAP_JSON")"
  assert_eq "files_verify is clean (deep too)" "" "$(files_verify "$(snap_dir 20260101-000000)" --deep)"
}

test_verify_deep_works_with_a_relative_snapshot_path() {
  new_env
  put "uploads/a" "aaa"
  take_snapshot 20260101-000000
  local f
  f="$(snap_dir 20260101-000000)/files/workspace/uploads/a"
  cp "$f" "${f}.new" && chmod u+w "${f}.new" && printf 'X' | dd of="${f}.new" bs=1 count=1 conv=notrunc 2>/dev/null && mv -f "${f}.new" "$f"
  local rel
  rel="$(cd "$BK" && echo "snapshots/20260101-000000")"
  assert_contains "relative path still catches the damage" "uploads/a" "$(cd "$BK" && files_verify "$rel" --deep)"
}

test_verify_fails_closed_when_the_list_is_unreadable() {
  new_env
  put "uploads/a" "aaa"
  take_snapshot 20260101-000000
  chmod 000 "$(snap_dir 20260101-000000)/files.list"
  if [[ "$(id -u)" != "0" ]]; then
    local out
    out="$(files_verify "$(snap_dir 20260101-000000)" --deep 2>/dev/null)"
    assert_contains "unreadable list is reported, not passed" "files.list" "$out"
  fi
  chmod 644 "$(snap_dir 20260101-000000)/files.list"
}

test_restore_refuses_a_symlinked_restore_displaced() {
  new_env
  put "uploads/a.pdf" "snapshot-bytes"
  take_snapshot 20260101-000000
  put "uploads/a.pdf" "changed-since-the-snapshot"
  mkdir -p "${T}/elsewhere" "${WS}/restore-displaced"
  ln -s "${T}/elsewhere" "${WS}/restore-displaced/20260101-000000"
  local rc=0
  files_restore "$(snap_dir 20260101-000000)" 20260101-000000 >/dev/null 2>&1 || rc=$?
  assert_eq "restore refuses" 1 "$rc"
  assert_eq "nothing written through the symlink" "" "$(find "${T}/elsewhere" -type f)"
  assert_eq "workspace file untouched" "changed-since-the-snapshot" "$(cat "${WS}/uploads/a.pdf")"
}

test_snapshot_strips_group_other_write_on_dirs_and_setuid() {
  new_env
  put "uploads/a" "aaa"
  chmod 4755 "${WS}/uploads/a"
  chmod 777 "${WS}/uploads"
  take_snapshot 20260101-000000
  local tree
  tree="$(snap_dir 20260101-000000)/files/workspace"
  assert_eq "directory is not group/other writable" "" "$(find "$tree" -type d -perm /022 ! -path "$tree")"
  assert_eq "no setuid/setgid files" "" "$(find "$tree" -type f -perm /6000)"
}

test_snapshot_files_are_read_only() {
  new_env
  put "uploads/a" "aaa"
  take_snapshot 20260101-000000
  take_snapshot 20260102-000000
  local t2
  t2="$(snap_dir 20260102-000000)/files/workspace"
  assert_eq "no write bit on any file" "" "$(find "$t2" -type f -perm /222)"
  assert_eq "second snapshot still hard-links" 2 "$(stat -c %h "${t2}/uploads/a")"
  if [[ "$(id -u)" != "0" ]]; then
    assert_eq "ownership unchanged for non-root" unchanged "$(jq -r .ownership <<<"$SNAP_JSON")"
  fi
}

shim_rsync_fail_first() { # exits $1 on the first call, then runs the real rsync
  shim rsync "n=\$(cat '${T}/rsync.n' 2>/dev/null || echo 0); echo \$((n+1)) >'${T}/rsync.n'
if [ \"\$n\" = 0 ]; then exit $1; fi
exec '${REAL_RSYNC}' \"\$@\""
}

test_snapshot_rsync_failure_falls_back() {
  new_env
  put "uploads/a" "aaa"
  shim_rsync_fail_first 23
  take_snapshot 20260101-000000
  PATH="$ORIG_PATH"
  assert_eq "rc 0 after the fallback" 0 "$SNAP_RC"
  assert_eq "ownership recorded" not_preserved "$(jq -r .ownership <<<"$SNAP_JSON")"
  assert_exists "tree complete" "$(snap_dir 20260101-000000)/files/workspace/uploads/a"
  assert_eq "WARN logged once" 1 "$(grep -c 'retrying without owner and permission changes' <<<"$SNAP_ERR")"
}

test_snapshot_rsync_failure_twice_records_failed_store() {
  new_env
  put "uploads/a" "aaa"
  shim rsync 'exit 12'
  local tmp="${T}/tmp-snap"
  mkdir -p "$tmp"
  local rc=0 out
  out="$(files_snapshot "$tmp" 2>/dev/null)" || rc=$?
  PATH="$ORIG_PATH"
  assert_eq "rc 3" 3 "$rc"
  assert_eq "failed store json" '{"status":"failed","rsync_exit":12}' "$out"
  assert_absent "no files/ left" "${tmp}/files"
  assert_absent "no files.list left" "${tmp}/files.list"
  assert_absent "no files.inodes left" "${tmp}/files.inodes"
  # verify skips a failed store
  mkdir -p "${T}/snapx"
  jq -n --argjson f "$out" '{schema_version: 2, stores: {files: $f}}' >"${T}/snapx/manifest.json"
  assert_eq "files_verify prints nothing for a failed store" "" "$(files_verify "${T}/snapx")"
}

test_rsync_exit_24_is_warning() {
  new_env
  put "uploads/a" "aaa"
  shim rsync "'${REAL_RSYNC}' \"\$@\"; exit 24"
  take_snapshot 20260101-000000
  PATH="$ORIG_PATH"
  assert_eq "rc 0" 0 "$SNAP_RC"
  assert_eq "status ok" ok "$(jq -r .status <<<"$SNAP_JSON")"
  assert_contains "WARN logged" "vanished" "$SNAP_ERR"
}

test_snapshot_leaves_workspace_unchanged() {
  new_env
  put "uploads/a" "aaa"
  put "captures/u/c/a/blob.jpg" "jpg"
  local before after
  before="$(find "$WS" -printf '%p %s %T@ %m\n' | sort)"
  take_snapshot 20260101-000000
  take_snapshot 20260102-000000
  after="$(find "$WS" -printf '%p %s %T@ %m\n' | sort)"
  assert_eq "workspace listing identical" "$before" "$after"
}

test_snapshot_fails_loudly_without_workspace() {
  new_env
  export WORKSPACE_PATH="${T}/not-mounted"
  local tmp="${T}/tmp-snap" rc=0 err
  mkdir -p "$tmp"
  err="$(files_snapshot "$tmp" 2>&1 >/dev/null)" || rc=$?
  assert_eq "rc 1" 1 "$rc"
  assert_contains "message names the mount" "workspace not mounted at ${T}/not-mounted" "$err"
}

test_hard_links_unavailable_is_recorded() {
  new_env
  put "uploads/a" "aaa"
  take_snapshot 20260101-000000
  shim rsync "args=(); for a in \"\$@\"; do case \"\$a\" in --link-dest=*) ;; *) args+=(\"\$a\") ;; esac; done
exec '${REAL_RSYNC}' \"\${args[@]}\""
  take_snapshot 20260102-000000
  PATH="$ORIG_PATH"
  assert_eq "hard_links false" false "$(jq -r .hard_links <<<"$SNAP_JSON")"
  assert_contains "WARN" "hard links unavailable" "$SNAP_ERR"
}

# ---------------------------------------------------------------------------
# Root-only block: ownership and read-only snapshots (round trip step 4c runs
# this file as root inside the backup image).
# ---------------------------------------------------------------------------

root_new_env() {
  new_env
  chown 4242:4242 "$WS"
}

test_root_snapshot_is_root_owned_readonly() {
  root_new_env
  put "uploads/u1/a.pdf" "pdf"
  chown -R 4343:4343 "${WS}/uploads"
  take_snapshot 20260101-000000
  local tree
  tree="$(snap_dir 20260101-000000)/files/workspace"
  assert_eq "everything is uid 0" "" "$(find "$tree" ! -user 0)"
  assert_eq "no write bit on files" "" "$(find "$tree" -type f -perm /222)"
  assert_eq "ownership recorded" root_readonly "$(jq -r .ownership <<<"$SNAP_JSON")"
}

test_root_second_snapshot_hard_links_with_chown() {
  root_new_env
  put "uploads/u1/a.pdf" "pdf"
  chown -R 4343:4343 "${WS}/uploads"
  take_snapshot 20260101-000000
  take_snapshot 20260102-000000
  assert_eq "unchanged file still hard-linked under --chown/--chmod" 2 \
    "$(stat -c %h "$(snap_dir 20260102-000000)/files/workspace/uploads/u1/a.pdf")"
}

test_root_restore_sets_workspace_owner() {
  root_new_env
  put "uploads/u1/a.pdf" "pdf"
  chown -R 4343:4343 "${WS}/uploads"
  take_snapshot 20260101-000000
  rm -rf "${WS}/uploads"
  files_restore "$(snap_dir 20260101-000000)" 20260101-000000 >/dev/null 2>&1
  assert_eq "file owned by the workspace owner" 4242 "$(stat -c %u "${WS}/uploads/u1/a.pdf")"
  assert_eq "new dir owned by the workspace owner" 4242 "$(stat -c %u "${WS}/uploads/u1")"
  assert_eq "owner can write" "" "$(find "${WS}/uploads" -type f ! -perm -u+w)"
  setpriv --reuid 4242 --regid 4242 --clear-groups sh -c "touch '${WS}/uploads/u1/new' && rm '${WS}/uploads/u1/new'"
  assert_eq "runtime uid can create and unlink in the restored dir" 0 $?
}

test_root_restore_honours_lumogis_run_uid() {
  root_new_env
  put "uploads/u1/a.pdf" "pdf"
  take_snapshot 20260101-000000
  rm -rf "${WS}/uploads"
  LUMOGIS_RUN_UID=4545 files_restore "$(snap_dir 20260101-000000)" 20260101-000000 >/dev/null 2>&1
  assert_eq "uid:gid from LUMOGIS_RUN_UID" 4545:4545 "$(stat -c %u:%g "${WS}/uploads/u1/a.pdf")"
}

test_root_restore_root_owned_workspace() {
  new_env # workspace stays root-owned
  put "uploads/u1/a.pdf" "pdf"
  take_snapshot 20260101-000000
  rm -rf "${WS}/uploads"
  files_restore "$(snap_dir 20260101-000000)" 20260101-000000 >/dev/null 2>&1
  assert_eq "root-owned workspace gives root-owned files" 0:0 "$(stat -c %u:%g "${WS}/uploads/u1/a.pdf")"
}

# ---------------------------------------------------------------------------

for t in $(declare -F | awk '{print $3}' | grep '^test_' | grep -v '^test_root_'); do
  "$t"
done

if [[ "$(id -u)" == "0" ]]; then
  for t in $(declare -F | awk '{print $3}' | grep '^test_root_'); do
    "$t"
  done
else
  echo "SKIP (root only): test_root_* — run in the backup image (round trip step 4c)"
fi

if ((failures > 0)); then
  echo "test_backup_files: ${failures} failed, ${passes} passed" >&2
  exit 1
fi
echo "test_backup_files: all ${passes} assertions passed"
