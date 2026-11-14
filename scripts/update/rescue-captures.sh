#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
#
# LUM-892 — copy capture photos and voice notes out of the existing orchestrator
# container BEFORE it is recreated.
#
# Before LUM-892 the capture media root (/opt/lumogis/data/captures) was not a
# mounted path, so the files lived in the container's writable layer and every
# image update deleted them. The new default is <workspace>/captures. This script
# moves what the old container still holds into that folder on the host.
#
# Runs on the HOST (Linux or macOS): plain POSIX tools only, no GNU-only flags
# (guarded by test_rescue_script_is_host_portable).
#
# Exit 0 = rescued, or nothing to rescue.
# Exit 1 = rescue incomplete; the message says why and where the copy is.
#
# Usage: bash scripts/update/rescue-captures.sh      (also: make rescue-captures)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

UPDATE_SCRIPT_NAME=rescue-captures
# shellcheck source=scripts/update/common.sh
source "$ROOT/scripts/update/common.sh"

command -v docker >/dev/null 2>&1 || die "docker not found on PATH"

# 1. The orchestrator container, if there is one.
cid="$(docker compose ps -a -q orchestrator 2>/dev/null | head -1 || true)"
if [[ -z "$cid" ]]; then
  log "no orchestrator container; nothing to rescue"
  exit 0
fi

# 2. Where does the old container keep captures? Read ONLY the two keys: the
#    container env holds secrets and must not be printed.
env_lines="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$cid" \
  | grep -E '^(CAPTURE_MEDIA_ROOT|LUMOGIS_DATA_DIR)=' || true)"
capture_env=""
data_env=""
while IFS= read -r line; do
  case "$line" in
    CAPTURE_MEDIA_ROOT=*) capture_env="${line#CAPTURE_MEDIA_ROOT=}" ;;
    LUMOGIS_DATA_DIR=*) data_env="${line#LUMOGIS_DATA_DIR=}" ;;
  esac
done <<<"$env_lines"
# Blank values count as unset. Trim the ends only: a path may contain a space.
trim() {
  local s="$1"
  s="${s#"${s%%[![:space:]]*}"}"
  printf '%s' "${s%"${s##*[![:space:]]}"}"
}
capture_env="$(trim "$capture_env")"
data_env="$(trim "$data_env")"

explicit=false
if [[ -n "$capture_env" ]]; then
  root_in_container="$capture_env"
  explicit=true
elif [[ -n "$data_env" ]]; then
  root_in_container="${data_env%/}/captures"
  explicit=true
else
  root_in_container="/opt/lumogis/data/captures"
fi

# 3. Is that path persisted? A path on a mount survives a recreate.
mount_dests="$(docker inspect --format '{{range .Mounts}}{{println .Destination}}{{end}}' "$cid")"
while IFS= read -r d; do
  [[ -n "$d" ]] || continue
  case "$root_in_container/" in
    "${d%/}"/*)
      log "captures are on a mounted path ($root_in_container); nothing to rescue"
      if [[ "${d%/}" == "/opt/lumogis/data" && "$explicit" == "false" ]]; then
        warn "the capture default is now the workspace (<workspace>/captures). Set LUMOGIS_DATA_DIR=/opt/lumogis/data in .env to keep using your /opt/lumogis/data mount."
      fi
      exit 0
      ;;
  esac
done <<<"$mount_dests"

# 4. The host workspace folder: env, else .env, else ./ai-workspace.
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
dest="$ws/captures"

state_dir="$ROOT/.lumogis-state"
mkdir -p "$state_dir"

# 5. Detect while the container runs, then freeze it. A container that holds no
#    captures keeps running: no downtime for an install that is already fixed.
running="$(docker inspect -f '{{.State.Running}}' "$cid" 2>/dev/null || echo false)"
if [[ "$running" == "true" ]]; then
  found="$(docker exec "$cid" sh -c 'find "$1" -type f 2>/dev/null | head -1' _ "$root_in_container" 2>/dev/null || echo "?")"
  if [[ -z "$found" ]]; then
    log "nothing to rescue ($root_in_container is empty in the running container)"
    exit 0
  fi
  # Space for the staged copy plus the destination.
  need_kb="$(docker exec "$cid" du -sk "$root_in_container" 2>/dev/null | cut -f1 || true)"
  free_kb="$(df -Pk "$state_dir" | awk 'NR==2 {print $4}')"
  if [[ -n "$need_kb" && -n "$free_kb" ]] && (( free_kb < 2 * need_kb )); then
    die "rescue needs ~$(( 2 * need_kb )) KB free under $state_dir (have $free_kb KB). Free some space and run it again; the orchestrator was not touched."
  fi
  log "stopping the orchestrator so no capture is written after the copy; the next 'docker compose up -d' starts it"
  docker stop "$cid" >/dev/null 2>&1 \
    || die "could not stop the orchestrator ($cid) to take a consistent copy"
fi

# 6. Copy once, from the stopped container.
staging="$(mktemp -d "$state_dir/captures-rescue.XXXXXX")"
cp_err="$(mktemp "$state_dir/captures-rescue-err.XXXXXX")"
if ! docker cp "$cid:$root_in_container/." "$staging/" 2>"$cp_err"; then
  if grep -q 'Could not find the file' "$cp_err"; then
    log "nothing to rescue ($root_in_container does not exist in the container)"
    rm -rf "$staging" "$cp_err"
    exit 0
  fi
  err="$(cat "$cp_err")"
  rm -rf "$cp_err"
  die "could not copy $root_in_container from container $cid: $err (staged copy, if any: $staging)"
fi
rm -f "$cp_err"
if [[ -z "$(find "$staging" -type f | head -1)" ]]; then
  log "nothing to rescue ($root_in_container holds no files)"
  rm -rf "$staging"
  exit 0
fi

# 7. Merge without overwriting. No `cp -n`: its exit status differs between GNU
#    coreutils releases and BSD.
copied=0
same=0
conflict=0
failed=0
while IFS= read -r -d '' f; do
  rel="${f#"$staging"/}"
  target="$dest/$rel"
  if [[ ! -e "$target" ]]; then
    if mkdir -p "$(dirname "$target")" && cp -p "$f" "$target"; then
      copied=$((copied + 1))
    else
      failed=$((failed + 1))
    fi
  elif cmp -s "$f" "$target"; then
    same=$((same + 1))
  else
    conflict=$((conflict + 1))
  fi
done < <(find "$staging" -type f -print0)

# 8. Anything that did not reach the destination keeps the staged copy.
if (( failed + conflict > 0 )); then
  die "$(( failed + conflict )) captures did not reach $dest ($conflict differ from files already there, $failed failed to copy); staged copy kept at $staging. 'docker compose start orchestrator' brings the old container back unchanged."
fi

# 9. An explicit root that is not mounted: the new container would read the same
#    unmounted path again, so stop here with an instruction.
if [[ "$explicit" == "true" ]]; then
  rm -rf "$staging" # the files are at $dest; the staged copy is no longer needed
  die "captures were copied to $dest; remove CAPTURE_MEDIA_ROOT/LUMOGIS_DATA_DIR from .env (it points at an unmounted path) and run the update again"
fi

# 10. Ownership hint. Root-owned files need nothing: the orchestrator's
#     shared-tree repair re-owns them at the next boot.
# shellcheck disable=SC2012  # `ls -ldn` is the portable owner lookup (no GNU stat -c on macOS)
owner="$(ls -ldn "$ROOT" | awk '{print $3}')"
me="$(id -u)"
if [[ "$me" != "0" && "$me" != "$owner" ]]; then
  warn "rescued files are owned by uid $me, not the checkout owner; run 'sudo chown -R $owner: $dest' if deleting a capture fails"
fi

# 11. Done.
log "rescued $copied capture files into $dest ($same already there)"
rm -rf "$staging"
exit 0
