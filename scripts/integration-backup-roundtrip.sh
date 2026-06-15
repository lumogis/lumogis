#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
#
# LUM-484 — disposable-compose DR round-trip: seed → backup → wipe data volumes →
# restore → assert Postgres + Qdrant + FalkorDB fixtures survived.
#
# Safety: forces COMPOSE_PROJECT_NAME=lumogis-test-backup (never touches dev stack).

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${INTEGRATION_ENV_FILE:-config/test.env.example}"
export COMPOSE_PROFILES=
export COMPOSE_FILE=docker-compose.yml:docker-compose.falkordb.yml
export COMPOSE_PROJECT_NAME=lumogis-test-backup
export QDRANT_HOST_PORT="${QDRANT_HOST_PORT:-6336}"
export FALKORDB_HOST_PORT="${FALKORDB_HOST_PORT:-6381}"

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

log "starting postgres + qdrant + falkordb"
compose up -d --wait postgres qdrant falkordb

log "seeding fixtures"
compose run --rm --no-deps \
  -e BACKUP_ROUNDTRIP_ENTITY_ID="$ENTITY_ID" \
  -e BACKUP_ROUNDTRIP_ENTITY_NAME="$ENTITY_NAME" \
  -e BACKUP_ROUNDTRIP_POINT_ID="$POINT_ID" \
  -e BACKUP_ROUNDTRIP_COLLECTION="$COLLECTION" \
  -e SEED_FALKORDB=1 \
  -e BACKUP_INCLUDE_FALKORDB=true \
  -v "$ROOT/tests:/integration-tests:ro" \
  backup \
  bash /integration-tests/integration/seed_backup_roundtrip_data.sh

entity_count_before="$(compose exec -T postgres \
  psql -U lumogis -d lumogis -tA -c "SELECT count(*) FROM entities WHERE entity_id = '${ENTITY_ID}'::uuid")"
qdrant_count_before="$(qdrant_point_count "$COLLECTION")"
graph_before="$(falkor_graph_read_name)"

if [[ "$entity_count_before" != "1" || "$qdrant_count_before" != "1" ]]; then
  log "seed failed (entities=${entity_count_before}, qdrant=${qdrant_count_before})"
  exit 1
fi
if ! grep -Fq "$ENTITY_NAME" <<<"$graph_before"; then
  log "falkordb seed failed — graph read did not return entity name"
  log "graph response: ${graph_before}"
  exit 1
fi

log "running backup"
compose run --rm -e BACKUP_INCLUDE_FALKORDB=true backup /scripts/backup/backup.sh run

latest="$(ls -1t "$ROOT/ai-workspace/backups-test/snapshots" 2>/dev/null | grep -v '^\.tmp$' | head -1 || true)"
if [[ -z "$latest" ]]; then
  log "no snapshot directory under ai-workspace/backups-test/snapshots"
  exit 1
fi
log "snapshot=${latest}"

falkor_skipped="$(compose run --rm --no-deps backup \
  jq -r '.stores.falkordb.skipped // false' "/backups/snapshots/${latest}/manifest.json")"
if [[ "$falkor_skipped" == "true" ]]; then
  log "backup manifest skipped falkordb — cannot run graph round-trip"
  exit 1
fi

log "stopping stack and wiping data volumes"
compose stop >/dev/null 2>&1 || true
for vol in "${COMPOSE_PROJECT_NAME}_postgres_data" "${COMPOSE_PROJECT_NAME}_qdrant_data" "$FALKOR_VOLUME"; do
  docker volume rm "$vol" >/dev/null 2>&1 || true
done

log "starting fresh postgres + qdrant + falkordb"
compose up -d --wait postgres qdrant falkordb

log "restoring snapshot (falkordb stopped for dump load)"
compose stop falkordb >/dev/null 2>&1 || true
compose run --rm --no-deps \
  -e RESTORE_SKIP_QUIESCE=1 \
  -e RESTORE_CONFIRM=1 \
  -e RESTORE_SNAPSHOT="$latest" \
  -e BACKUP_INCLUDE_FALKORDB=true \
  backup \
  /scripts/backup/restore.sh "${latest}" --yes

log "restarting falkordb to load restored dump.rdb"
compose up -d --wait falkordb

entity_count_after="$(compose exec -T postgres \
  psql -U lumogis -d lumogis -tA -c "SELECT count(*) FROM entities WHERE entity_id = '${ENTITY_ID}'::uuid")"
qdrant_count_after="$(qdrant_point_count "$COLLECTION")"
graph_after="$(falkor_graph_read_name)"

if [[ "$entity_count_after" != "1" || "$qdrant_count_after" != "1" ]]; then
  log "assertion failed after restore (entities=${entity_count_after}, qdrant=${qdrant_count_after})"
  exit 1
fi
if ! grep -Fq "$ENTITY_NAME" <<<"$graph_after"; then
  log "falkordb assertion failed after restore — graph read did not return entity name"
  log "graph response: ${graph_after}"
  exit 1
fi

log "running in-container smoke (backup + verify)"
compose run --rm \
  -e BACKUP_INCLUDE_FALKORDB=true \
  -v "$ROOT/tests:/integration-tests:ro" \
  backup \
  bash /integration-tests/integration/test_backup_restore_roundtrip.sh

log "ok — full DR volume-wipe round-trip passed (postgres + qdrant + falkordb)"
