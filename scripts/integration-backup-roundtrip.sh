#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
#
# LUM-484 — disposable-compose DR round-trip: seed → backup → wipe data volumes →
# restore → assert Postgres + Qdrant (+ FalkorDB when overlay present) fixtures survived.
#
# Safety: forces COMPOSE_PROJECT_NAME=lumogis-test-backup (never touches dev stack).

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

cleanup() {
  log "tearing down ${COMPOSE_PROJECT_NAME}"
  compose down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

if [[ "${COMPOSE_PROJECT_NAME}" != "lumogis-test-backup" ]]; then
  log "refusing: COMPOSE_PROJECT_NAME must be lumogis-test-backup"
  exit 2
fi

# docker-compose.yml services declare env_file: .env (same as compose-test-doctor).
cp -f "$ROOT/$ENV_FILE" "$ROOT/.env"

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

if [[ "$INCLUDE_FALKORDB" == "true" ]]; then
  falkor_skipped="$(compose run --rm --no-deps backup \
    jq -r '.stores.falkordb.skipped // false' "/backups/snapshots/${latest}/manifest.json")"
  if [[ "$falkor_skipped" == "true" ]]; then
    log "backup manifest skipped falkordb — cannot run graph round-trip"
    exit 1
  fi
fi

log "stopping stack and wiping data volumes"
compose stop >/dev/null 2>&1 || true
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
