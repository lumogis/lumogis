#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
#
# LUM-484 — disposable-compose DR round-trip: seed → backup → wipe data volumes →
# restore → assert Postgres + Qdrant (+ FalkorDB when overlay present) fixtures survived.
#
# LUM-892 — also the files: an uploaded document and a capture photo are seeded in a
# disposable workspace (WORKSPACE_HOST_DIR=./ai-workspace/workspace-test), the
# workspace is wiped with the volumes, and after restore the document must be
# openable and the capture must download byte-identical. A second wipe proves the
# check is not vacuous. The same run covers the root-only shell tests, a snapshot
# whose files carry a foreign uid, and the stopped-container capture rescue.
#
# LUM-922 — the admin's stop-entities list rides along: it is written through the
# admin route into <workspace>/config, must be in force after restore and gone
# after the second wipe, and the stopped-container rescue copies an edited list.
#
# Safety: forces COMPOSE_PROJECT_NAME=lumogis-test-backup (never touches dev stack).

# The single-quoted snippets passed to backup_sh are run by sh INSIDE the container,
# so their $1 must not expand here.
# shellcheck disable=SC2016
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${INTEGRATION_ENV_FILE:-config/test.env.example}"
export COMPOSE_PROFILES=
export COMPOSE_PROJECT_NAME=lumogis-test-backup
export QDRANT_HOST_PORT="${QDRANT_HOST_PORT:-6336}"
export FALKORDB_HOST_PORT="${FALKORDB_HOST_PORT:-6381}"

FALKORDB_OVERLAY="${ROOT}/docker-compose.falkordb.yml"
INCLUDE_FALKORDB=false
if [[ -f "$FALKORDB_OVERLAY" ]]; then
  INCLUDE_FALKORDB=true
  export COMPOSE_FILE=docker-compose.yml:docker-compose.falkordb.yml
else
  export COMPOSE_FILE=docker-compose.yml
fi

BACKUP_INCLUDE_FALKORDB="${BACKUP_INCLUDE_FALKORDB:-$INCLUDE_FALKORDB}"
if [[ "$INCLUDE_FALKORDB" != "true" ]]; then
  BACKUP_INCLUDE_FALKORDB=false
fi
export BACKUP_INCLUDE_FALKORDB

ENTITY_ID="${BACKUP_ROUNDTRIP_ENTITY_ID:-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee}"
ENTITY_NAME="${BACKUP_ROUNDTRIP_ENTITY_NAME:-DR Roundtrip Seed Entity}"
POINT_ID="${BACKUP_ROUNDTRIP_POINT_ID:-11111111-2222-3333-4444-555555555555}"
COLLECTION="${BACKUP_ROUNDTRIP_COLLECTION:-dr_roundtrip_docs}"
GRAPH_NAME="${FALKORDB_GRAPH_NAME:-lumogis}"
FALKOR_VOLUME="${COMPOSE_PROJECT_NAME}_falkordb_data"

compose() {
  docker compose --env-file "$ROOT/$ENV_FILE" "$@"
}

log() {
  echo "[integration-backup-roundtrip] $*" >&2
}

qdrant_point_count() {
  local collection="$1"
  compose run --rm --no-deps backup \
    curl -sf -X POST "http://qdrant:6333/collections/${collection}/points/count" \
    -H 'Content-Type: application/json' \
    -d '{}' | jq -r '.result.count'
}

falkor_graph_read_name() {
  compose exec -T falkordb redis-cli GRAPH.QUERY "$GRAPH_NAME" \
    "MATCH (n:Entity {lumogis_id: '${ENTITY_ID}'}) RETURN n.name LIMIT 1" 2>/dev/null || true
}

# Runs a shell snippet as root in the backup container (the workspace and the
# snapshots hold root-owned files the runner cannot touch). $1 is the script,
# further args become $1.. inside it.
backup_sh() {
  local snippet="$1"
  shift
  compose run --rm --no-deps --entrypoint sh backup -c "$snippet" _ "$@"
}

clear_test_workspace() {
  backup_sh 'find /workspace -mindepth 1 -delete' >/dev/null 2>&1 || true
}

# The orchestrator image, as the runtime (non-root) uid, to reach the service code.
orchestrator_py() {
  compose run --rm --no-deps --user "$(id -u):$(id -g)" \
    -e PYTHONPATH=/app -e POSTGRES_HOST=postgres -e POSTGRES_PORT=5432 \
    -v "$ROOT/tests:/integration-tests:ro" \
    ${SEED_MOUNT[@]+"${SEED_MOUNT[@]}"} \
    --entrypoint python orchestrator /integration-tests/integration/backup_roundtrip_files.py "$@"
}
SEED_MOUNT=()

cleanup() {
  log "tearing down ${COMPOSE_PROJECT_NAME}"
  clear_test_workspace
  compose down -v --remove-orphans >/dev/null 2>&1 || true
  [[ -n "${SEED_TMP:-}" ]] && rm -rf "$SEED_TMP"
}
trap cleanup EXIT

if [[ "${COMPOSE_PROJECT_NAME}" != "lumogis-test-backup" ]]; then
  log "refusing: COMPOSE_PROJECT_NAME must be lumogis-test-backup"
  exit 2
fi

# docker-compose.yml services declare env_file: .env (same as compose-test-doctor).
cp -f "$ROOT/$ENV_FILE" "$ROOT/.env"

# LUM-892: a disposable workspace. Refuse anything but the test folder: this run
# wipes it, and the default ./ai-workspace holds the developer's real documents.
export WORKSPACE_HOST_DIR="${WORKSPACE_HOST_DIR:-./ai-workspace/workspace-test}"
mkdir -p "$ROOT/$WORKSPACE_HOST_DIR"
ws_resolved="$(cd "$ROOT/$WORKSPACE_HOST_DIR" && pwd)"
if [[ "$ws_resolved" != "$ROOT/ai-workspace/workspace-test" ]]; then
  log "refusing: WORKSPACE_HOST_DIR must resolve to $ROOT/ai-workspace/workspace-test (got $ws_resolved)"
  exit 2
fi
WORKSPACE_DIR="$ws_resolved"

log "building the backup and orchestrator images"
compose build backup orchestrator

log "clearing the test workspace"
clear_test_workspace

# Rescue, stopped-container path: a created (never started) old-style orchestrator
# container holds three captures in its writable layer; the rescue must copy them
# into the workspace, and a second run must find them already there.
log "rescue-captures: stopped-container path"
compose up --no-start --no-deps --pull never orchestrator >/dev/null
rescue_cid="$(compose ps -a -q orchestrator | head -1)"
if [[ -z "$rescue_cid" ]]; then
  log "could not create the orchestrator container for the rescue test"
  exit 1
fi
rescue_src="$(mktemp -d)"
mkdir -p "$rescue_src/captures/u/c/a"
for n in 1 2 3; do printf 'rescue-%s\n' "$n" >"$rescue_src/captures/u/c/a/$n.bin"; done
docker cp "$rescue_src/captures" "$rescue_cid:/opt/lumogis/data/"
rescue_out="$(bash "$ROOT/scripts/update/rescue-captures.sh" 2>&1)" || {
  log "rescue-captures failed: $rescue_out"
  exit 1
}
grep -q 'rescued 3 capture files' <<<"$rescue_out" || { log "rescue did not report 3 files: $rescue_out"; exit 1; }
for n in 1 2 3; do
  cmp -s "$rescue_src/captures/u/c/a/$n.bin" "$WORKSPACE_DIR/captures/u/c/a/$n.bin" \
    || { log "rescued file $n differs or is missing"; exit 1; }
done
rescue_out="$(bash "$ROOT/scripts/update/rescue-captures.sh" 2>&1)" || {
  log "second rescue-captures run failed: $rescue_out"
  exit 1
}
grep -q 'rescued 0 capture files.*(3 already there)' <<<"$rescue_out" \
  || { log "second rescue run did not find the 3 files already there: $rescue_out"; exit 1; }

# LUM-922 — the same container and the admin's stop-entities list: the image's own
# list is left alone, an edited one (docker cp puts it in the writable layer) is
# copied into <workspace>/config, and a different list already there is never
# overwritten.
log "rescue-stop-entities: stopped-container path"
rescue_out="$(bash "$ROOT/scripts/update/rescue-stop-entities.sh" 2>&1)" || {
  log "rescue-stop-entities failed on an unedited list: $rescue_out"
  exit 1
}
grep -q 'not edited in this container' <<<"$rescue_out" \
  || { log "rescue-stop-entities did not leave the image's list alone: $rescue_out"; exit 1; }
printf 'dr roundtrip rescued phrase\n' >"$rescue_src/stop_entities.txt"
docker cp "$rescue_src/stop_entities.txt" "$rescue_cid:/app/config/stop_entities.txt"
rescue_out="$(bash "$ROOT/scripts/update/rescue-stop-entities.sh" 2>&1)" || {
  log "rescue-stop-entities failed: $rescue_out"
  exit 1
}
grep -q 'rescued the stop-entities list' <<<"$rescue_out" \
  || { log "rescue-stop-entities did not report the copy: $rescue_out"; exit 1; }
cmp -s "$rescue_src/stop_entities.txt" "$WORKSPACE_DIR/config/stop_entities.txt" \
  || { log "the rescued stop-entities list differs or is missing"; exit 1; }
printf 'a later edit in the old container\n' >"$rescue_src/stop_entities.txt"
docker cp "$rescue_src/stop_entities.txt" "$rescue_cid:/app/config/stop_entities.txt"
rescue_out="$(bash "$ROOT/scripts/update/rescue-stop-entities.sh" 2>&1)" || {
  log "rescue-stop-entities failed on a conflict: $rescue_out"
  exit 1
}
grep -q 'it was kept' <<<"$rescue_out" \
  || { log "rescue-stop-entities did not report the kept list: $rescue_out"; exit 1; }
grep -qx 'dr roundtrip rescued phrase' "$WORKSPACE_DIR/config/stop_entities.txt" \
  || { log "rescue-stop-entities overwrote the list already in the workspace"; exit 1; }
rm -f "$ROOT"/.lumogis-state/stop_entities.from-container-*.txt
rm -rf "$rescue_src"
compose rm -f -s orchestrator >/dev/null 2>&1 || true
clear_test_workspace

# The shell unit tests as root, inside the backup image: the root-only block
# (--chown, read-only snapshots, restore owner) cannot run on a CI host as non-root.
log "tests/unit/test_backup_files.sh as root in the backup image"
compose run --rm --no-deps -v "$ROOT:/src:ro" --entrypoint bash backup /src/tests/unit/test_backup_files.sh

if [[ "$INCLUDE_FALKORDB" == "true" ]]; then
  log "starting postgres + qdrant + falkordb"
  compose up -d --wait postgres qdrant falkordb
else
  log "docker-compose.falkordb.yml absent — postgres + qdrant DR round-trip only"
  compose up -d --wait postgres qdrant
fi

log "seeding fixtures"
seed_falkor_env=()
if [[ "$INCLUDE_FALKORDB" == "true" ]]; then
  seed_falkor_env=(-e SEED_FALKORDB=1)
fi
compose run --rm --no-deps \
  -e BACKUP_ROUNDTRIP_ENTITY_ID="$ENTITY_ID" \
  -e BACKUP_ROUNDTRIP_ENTITY_NAME="$ENTITY_NAME" \
  -e BACKUP_ROUNDTRIP_POINT_ID="$POINT_ID" \
  -e BACKUP_ROUNDTRIP_COLLECTION="$COLLECTION" \
  "${seed_falkor_env[@]}" \
  -e "BACKUP_INCLUDE_FALKORDB=${BACKUP_INCLUDE_FALKORDB}" \
  -v "$ROOT/tests:/integration-tests:ro" \
  backup \
  bash /integration-tests/integration/seed_backup_roundtrip_data.sh

entity_count_before="$(compose exec -T postgres \
  psql -U lumogis -d lumogis -tA -c "SELECT count(*) FROM entities WHERE entity_id = '${ENTITY_ID}'::uuid")"
qdrant_count_before="$(qdrant_point_count "$COLLECTION")"
graph_before=""
if [[ "$INCLUDE_FALKORDB" == "true" ]]; then
  graph_before="$(falkor_graph_read_name)"
fi

if [[ "$entity_count_before" != "1" || "$qdrant_count_before" != "1" ]]; then
  log "seed failed (entities=${entity_count_before}, qdrant=${qdrant_count_before})"
  exit 1
fi

# LUM-892 — files: migrations first (captures tables), then the upload + capture seed.
log "migrating and seeding the uploaded document and the capture photo"
# (config/test.env.example points POSTGRES_HOST at the host's published port; inside
# the compose network the database is postgres:5432.)
compose run --rm --no-deps --user "$(id -u):$(id -g)" \
  -e POSTGRES_HOST=postgres -e POSTGRES_PORT=5432 \
  --entrypoint python orchestrator db_migrations.py >/dev/null
orchestrator_py seed
SEED_TMP="$(mktemp -d)"
cp "$WORKSPACE_DIR/dr-roundtrip-seed.json" "$SEED_TMP/seed.json"
chmod 755 "$SEED_TMP"
chmod 644 "$SEED_TMP/seed.json"

# A foreign uid on the seeded files simulates a snapshot taken on another machine.
backup_sh 'chown -R 4242:4242 /workspace/uploads/dr-roundtrip /workspace/captures'
log "foreign-uid seed files in place (uid 4242)"
if [[ "$INCLUDE_FALKORDB" == "true" ]] && ! grep -Fq "$ENTITY_NAME" <<<"$graph_before"; then
  log "falkordb seed failed — graph read did not return entity name"
  log "graph response: ${graph_before}"
  exit 1
fi

log "running backup"
compose run --rm -e "BACKUP_INCLUDE_FALKORDB=${BACKUP_INCLUDE_FALKORDB}" backup /scripts/backup/backup.sh run

latest="$(ls -1t "$ROOT/ai-workspace/backups-test/snapshots" 2>/dev/null | grep -v '^\.tmp$' | head -1 || true)"
if [[ -z "$latest" ]]; then
  log "no snapshot directory under ai-workspace/backups-test/snapshots"
  exit 1
fi
log "snapshot=${latest}"

# LUM-892 — the snapshot's files: counted, root-owned, read-only, and not writable
# by the runtime uid even though the orchestrator mounts the backup dir read-write.
file_count="$(backup_sh 'jq -r ".stores.files.file_count // 0" "/backups/snapshots/$1/manifest.json"' "$latest")"
files_status="$(backup_sh 'jq -r ".stores.files.status // empty" "/backups/snapshots/$1/manifest.json"' "$latest")"
files_ownership="$(backup_sh 'jq -r ".stores.files.ownership // empty" "/backups/snapshots/$1/manifest.json"' "$latest")"
if (( file_count < 2 )) || [[ "$files_status" != "ok" || "$files_ownership" != "root_readonly" ]]; then
  log "snapshot files store wrong (file_count=${file_count} status=${files_status} ownership=${files_ownership})"
  exit 1
fi
not_root_ro="$(backup_sh 'find "/backups/snapshots/$1/files/workspace" -type f \( ! -user 0 -o -perm /222 \) | wc -l' "$latest" | tr -d '[:space:]')"
if [[ "$not_root_ro" != "0" ]]; then
  log "${not_root_ro} snapshot files are not root-owned and read-only"
  exit 1
fi
probe="$(backup_sh 'find "/backups/snapshots/$1/files/workspace" -type f | head -1' "$latest" | tr -d '\r')"
if ! compose run --rm --no-deps --user "$(id -u):$(id -g)" --entrypoint sh backup -c 'test -r "$1"' _ "$probe"; then
  log "runtime uid cannot read a snapshot file; the append probe below would prove nothing"
  exit 1
fi
if compose run --rm --no-deps --user "$(id -u):$(id -g)" --entrypoint sh backup -c ': >> "$1"' _ "$probe" 2>/dev/null; then
  log "the runtime uid could append to a snapshot file ($probe)"
  exit 1
fi
# The backup status card reads manifest.json as the non-root orchestrator user.
if ! compose run --rm --no-deps --user "$(id -u):$(id -g)" --entrypoint sh backup \
  -c 'test -r "/backups/snapshots/$1/manifest.json"' _ "$latest"; then
  log "manifest.json is not readable by the runtime uid: the backup status card cannot read it"
  exit 1
fi
log "snapshot files: ${file_count} files, root-owned, read-only"

if [[ "$INCLUDE_FALKORDB" == "true" ]]; then
  falkor_skipped="$(compose run --rm --no-deps backup \
    jq -r '.stores.falkordb.skipped // false' "/backups/snapshots/${latest}/manifest.json")"
  if [[ "$falkor_skipped" == "true" ]]; then
    log "backup manifest skipped falkordb — cannot run graph round-trip"
    exit 1
  fi
fi

log "stopping stack and wiping data volumes and the test workspace"
compose stop >/dev/null 2>&1 || true
clear_test_workspace
volumes=(
  "${COMPOSE_PROJECT_NAME}_postgres_data"
  "${COMPOSE_PROJECT_NAME}_qdrant_data"
)
if [[ "$INCLUDE_FALKORDB" == "true" ]]; then
  volumes+=("$FALKOR_VOLUME")
fi
for vol in "${volumes[@]}"; do
  docker volume rm "$vol" >/dev/null 2>&1 || true
done

if [[ "$INCLUDE_FALKORDB" == "true" ]]; then
  log "starting fresh postgres + qdrant + falkordb"
  compose up -d --wait postgres qdrant falkordb
else
  log "starting fresh postgres + qdrant"
  compose up -d --wait postgres qdrant
fi

if [[ "$INCLUDE_FALKORDB" == "true" ]]; then
  log "restoring snapshot (falkordb stopped for dump load)"
  compose stop falkordb >/dev/null 2>&1 || true
else
  log "restoring snapshot"
fi
compose run --rm --no-deps \
  -e RESTORE_SKIP_QUIESCE=1 \
  -e RESTORE_CONFIRM=1 \
  -e RESTORE_SNAPSHOT="$latest" \
  -e "BACKUP_INCLUDE_FALKORDB=${BACKUP_INCLUDE_FALKORDB}" \
  backup \
  /scripts/backup/restore.sh "${latest}" --yes

if [[ "$INCLUDE_FALKORDB" == "true" ]]; then
  log "restarting falkordb to load restored dump.rdb"
  compose up -d --wait falkordb
fi

entity_count_after="$(compose exec -T postgres \
  psql -U lumogis -d lumogis -tA -c "SELECT count(*) FROM entities WHERE entity_id = '${ENTITY_ID}'::uuid")"
qdrant_count_after="$(qdrant_point_count "$COLLECTION")"
graph_after=""
if [[ "$INCLUDE_FALKORDB" == "true" ]]; then
  graph_after="$(falkor_graph_read_name)"
fi

if [[ "$entity_count_after" != "1" || "$qdrant_count_after" != "1" ]]; then
  log "assertion failed after restore (entities=${entity_count_after}, qdrant=${qdrant_count_after})"
  exit 1
fi
if [[ "$INCLUDE_FALKORDB" == "true" ]] && ! grep -Fq "$ENTITY_NAME" <<<"$graph_after"; then
  log "falkordb assertion failed after restore — graph read did not return entity name"
  log "graph response: ${graph_after}"
  exit 1
fi

# LUM-892 — files after restore: owned by the workspace owner (not the foreign uid),
# then proven through the service code as the non-root runtime uid.
want_uid="$(id -u)"
upload_file=/workspace/uploads/dr-roundtrip/1_roundtrip.txt
got_uid="$(backup_sh 'stat -c %u "$1"' "$upload_file" | tr -d '[:space:]')"
if [[ "$got_uid" != "$want_uid" ]]; then
  log "restored $upload_file is owned by uid ${got_uid}, expected ${want_uid} (the workspace owner)"
  exit 1
fi
cap_blob="$(backup_sh 'find /workspace/captures -type f -name "blob.*" | head -1' | tr -d '\r')"
cap_uid="$(backup_sh 'stat -c %u "$1"' "$cap_blob" | tr -d '[:space:]')"
if [[ -z "$cap_blob" || "$cap_uid" != "$want_uid" ]]; then
  log "restored capture blob '${cap_blob}' is owned by uid '${cap_uid}', expected ${want_uid}"
  exit 1
fi
log "restored files carry the workspace owner's uid"
orchestrator_py assert-restored

# Negative control: wipe the workspace again; the same rows must now be reported missing.
log "negative control: wiping the workspace again"
clear_test_workspace
SEED_MOUNT=(-v "$SEED_TMP:/seed:ro")
orchestrator_py assert-missing --seed-json /seed/seed.json
SEED_MOUNT=()

log "running in-container smoke (backup + verify)"
compose run --rm \
  -e "BACKUP_INCLUDE_FALKORDB=${BACKUP_INCLUDE_FALKORDB}" \
  -v "$ROOT/tests:/integration-tests:ro" \
  backup \
  bash /integration-tests/integration/test_backup_restore_roundtrip.sh

if [[ "$INCLUDE_FALKORDB" == "true" ]]; then
  log "ok — full DR volume-wipe round-trip passed (postgres + qdrant + falkordb)"
else
  log "ok — DR volume-wipe round-trip passed (postgres + qdrant; falkordb overlay absent)"
fi
