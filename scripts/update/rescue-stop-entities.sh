#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
#
# LUM-922 — copy the admin's stop-entities list out of the existing orchestrator
# container BEFORE it is recreated.
#
# Before LUM-922 the list lived at /app/config/stop_entities.txt, inside the
# image: an admin's edits stayed in the container's writable layer and every
# image update deleted them. The new location is <workspace>/config. This script
# moves an edited list the old container still holds into that folder on the host.
# The new orchestrator copies the shipped list there on boot only when no file
# is there yet, so a rescued list is what it uses.
#
# Never overwrites: if <workspace>/config/stop_entities.txt already exists and
# differs, it is kept and the container's copy is saved beside the update state.
#
# Runs on the HOST (Linux or macOS): plain POSIX tools only, no GNU-only flags
# (guarded by test_rescue_script_is_host_portable).
#
# Exit 0 = rescued, or nothing to rescue.
# Exit 1 = rescue incomplete; the message says why and where the copy is.
#
# Usage: bash scripts/update/rescue-stop-entities.sh   (also: make rescue-stop-entities)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

UPDATE_SCRIPT_NAME=rescue-stop-entities
# shellcheck source=scripts/update/common.sh
source "$ROOT/scripts/update/common.sh"

command -v docker >/dev/null 2>&1 || die "docker not found on PATH"

# 1. The orchestrator container, if there is one.
cid="$(docker compose ps -a -q orchestrator 2>/dev/null | head -1 || true)"
if [[ -z "$cid" ]]; then
  log "no orchestrator container; nothing to rescue"
  exit 0
fi

# 2. Where does the old container keep the list? Read ONLY this key: the
#    container env holds secrets and must not be printed.
explicit="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$cid" \
  | grep -E '^STOP_ENTITIES_PATH=' | tail -1 || true)"
explicit="${explicit#STOP_ENTITIES_PATH=}"
# Blank counts as unset. Trim the ends only: a path may contain a space.
explicit="${explicit#"${explicit%%[![:space:]]*}"}"
explicit="${explicit%"${explicit##*[![:space:]]}"}"
if [[ -n "$explicit" ]]; then
  src="$explicit"
else
  src="/app/config/stop_entities.txt"
fi

# 3. Is that path persisted? A path on a mount survives a recreate (this covers
#    a list already in the workspace and the dev overlay's ./orchestrator:/app).
mount_dests="$(docker inspect --format '{{range .Mounts}}{{println .Destination}}{{end}}' "$cid")"
while IFS= read -r d; do
  [[ -n "$d" ]] || continue
  case "$src" in
    "${d%/}" | "${d%/}"/*) # a file bind mount, or a folder above it
      log "the stop-entities list is on a mounted path ($src); nothing to rescue"
      exit 0
      ;;
  esac
done <<<"$mount_dests"

# 4. Was it edited in this container? `docker diff` lists the writable layer, so
#    an unedited list (the image's own) is left for the new image to ship.
#    If the diff cannot be read, copy anyway: the merge below never overwrites.
edited=unknown
if diff_out="$(docker diff "$cid" 2>/dev/null)"; then
  edited=false
  while IFS= read -r line; do
    case "$line" in
      "A $src" | "C $src")
        edited=true
        break
        ;;
    esac
  done <<<"$diff_out"
fi
if [[ "$edited" == "false" ]]; then
  log "the stop-entities list was not edited in this container; nothing to rescue"
  exit 0
fi

# 5. The host workspace folder: env, else .env, else ./ai-workspace.
ws_setting="${WORKSPACE_HOST_DIR:-}"
if [[ -z "$ws_setting" && -f "$ROOT/.env" ]]; then
  ws_setting="$(grep -E '^WORKSPACE_HOST_DIR=' "$ROOT/.env" | tail -1 | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//' || true)"
fi
[[ -n "$ws_setting" ]] || ws_setting="./ai-workspace"
case "$ws_setting" in
  /*) ws_abs_input="$ws_setting" ;;
  *) ws_abs_input="$ROOT/$ws_setting" ;;
esac
mkdir -p "$ws_abs_input"
ws="$(cd "$ws_abs_input" && pwd)"
dest="$ws/config/stop_entities.txt"

state_dir="$ROOT/.lumogis-state"
mkdir -p "$state_dir"

# 6. Copy once. A running container is fine: the list is one small file.
staging="$(mktemp -d "$state_dir/stop-entities-rescue.XXXXXX")"
cp_err="$state_dir/stop-entities-rescue.err"
if ! docker cp "$cid:$src" "$staging/stop_entities.txt" 2>"$cp_err"; then
  if grep -q 'Could not find the file' "$cp_err"; then
    log "nothing to rescue ($src does not exist in the container)"
    rm -rf "$staging" "$cp_err"
    exit 0
  fi
  err="$(cat "$cp_err")"
  rm -f "$cp_err"
  die "could not copy $src from container $cid: $err (staged copy, if any: $staging)"
fi
rm -f "$cp_err"
staged="$staging/stop_entities.txt"
if [[ ! -f "$staged" || -L "$staged" ]]; then
  rm -rf "$staging"
  die "$src in container $cid is not a regular file; copy the list by hand to $dest"
fi

# 6b. `docker diff` also says C when only the owner changed, and the LUM-887
#     entrypoint re-owns /app/config at every boot. Compare with an image's own
#     copy (a created, never-started container): the same bytes as a shipped
#     list mean there is no edit to rescue. Without this, every update after
#     LUM-887 warned an admin who had edited <workspace>/config to overwrite it
#     with the shipped default (LUM-900). Try the container's own image first.
#     During `make update` it still resolves: the update tagged it
#     lumogis-rollback/… before building (LUM-984). After a bare `make build`
#     or `make dev`, on the containerd image store that ID no longer resolves
#     once a new image took the tag, so then try the image the tag names now.
#     If neither copy can be read, carry on: step 7 never overwrites.
if [[ -z "$explicit" ]]; then
  same=false
  for ref in \
    "$(docker inspect --format '{{.Image}}' "$cid" 2>/dev/null || true)" \
    "$(docker inspect --format '{{.Config.Image}}' "$cid" 2>/dev/null || true)"; do
    [[ -n "$ref" ]] || continue
    pristine="$(docker create "$ref" 2>/dev/null || true)"
    [[ -n "$pristine" ]] || continue
    if docker cp "$pristine:$src" "$staging/image_copy.txt" 2>/dev/null \
      && cmp -s "$staging/stop_entities.txt" "$staging/image_copy.txt"; then
      same=true
    fi
    docker rm -v "$pristine" >/dev/null 2>&1 || true
    rm -f "$staging/image_copy.txt"
    break # the first image that can be read decides
  done
  if [[ "$same" == "true" ]]; then
    rm -rf "$staging"
    log "the stop-entities list in the container matches its image (only its owner changed); nothing to rescue"
    exit 0
  fi
fi

# 7. Merge without overwriting. No `cp -n`: its exit status differs between GNU
#    coreutils releases and BSD.
if [[ -e "$dest" || -L "$dest" ]]; then
  if cmp -s "$staged" "$dest"; then
    log "the stop-entities list is already at $dest"
    rm -rf "$staging"
  else
    kept="$state_dir/stop_entities.from-container-${cid:0:12}.txt"
    mv "$staged" "$kept"
    rm -rf "$staging"
    warn "$dest already exists and differs from the old container's list; it was kept. The old container's list is saved at $kept: copy it over $dest if that is the one you want."
  fi
else
  mkdir -p "$(dirname "$dest")"
  cp -p "$staged" "$dest" \
    || die "could not write $dest; the old container's list is staged at $staging"
  rm -rf "$staging"
  log "rescued the stop-entities list into $dest"
fi

# 8. An explicit path that is not mounted: the new container would read the same
#    unmounted path again, so stop here with an instruction.
if [[ -n "$explicit" ]]; then
  die "STOP_ENTITIES_PATH in .env points at an unmounted path, which every recreate empties. The list is now in $dest: remove STOP_ENTITIES_PATH from .env and run the update again"
fi

# 9. Ownership hint. A root-owned file needs nothing: the orchestrator's
#    shared-tree repair re-owns it at the next boot.
# shellcheck disable=SC2012  # `ls -ldn` is the portable owner lookup (no GNU stat -c on macOS)
owner="$(ls -ldn "$ROOT" | awk '{print $3}')"
me="$(id -u)"
if [[ "$me" != "0" && "$me" != "$owner" ]]; then
  warn "the rescued list is owned by uid $me, not the checkout owner; run 'sudo chown $owner: $dest' if editing the list fails"
fi
exit 0
