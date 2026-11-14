#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Seed minimal Postgres + Qdrant (+ optional FalkorDB) fixtures for LUM-484 DR round-trip.
set -euo pipefail

ENTITY_ID="${BACKUP_ROUNDTRIP_ENTITY_ID:-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee}"
ENTITY_NAME="${BACKUP_ROUNDTRIP_ENTITY_NAME:-DR Roundtrip Seed Entity}"
POINT_ID="${BACKUP_ROUNDTRIP_POINT_ID:-11111111-2222-3333-4444-555555555555}"
COLLECTION="${BACKUP_ROUNDTRIP_COLLECTION:-dr_roundtrip_docs}"

POSTGRES_HOST="${POSTGRES_HOST:-postgres}"
POSTGRES_USER="${POSTGRES_USER:-lumogis}"
POSTGRES_DB="${POSTGRES_DB:-lumogis}"
QDRANT_URL="${QDRANT_URL:-http://qdrant:6333}"
FALKOR_HOST="${FALKORDB_HOST:-falkordb}"
FALKOR_PORT="${FALKORDB_PORT:-6379}"
GRAPH_NAME="${FALKORDB_GRAPH_NAME:-lumogis}"

export PGPASSWORD="${POSTGRES_PASSWORD:?POSTGRES_PASSWORD required}"
psql -h "$POSTGRES_HOST" -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1 <<SQL
INSERT INTO entities (entity_id, name, entity_type, user_id)
VALUES ('${ENTITY_ID}'::uuid, '${ENTITY_NAME}', 'CONCEPT', 'default')
ON CONFLICT (entity_id) DO NOTHING;
SQL
unset PGPASSWORD

curl -sf -X DELETE "${QDRANT_URL}/collections/${COLLECTION}" >/dev/null 2>&1 || true
curl -sf -X PUT "${QDRANT_URL}/collections/${COLLECTION}" \
  -H 'Content-Type: application/json' \
  -d '{"vectors":{"size":384,"distance":"Cosine"}}'

vector_json="$(jq -n '[range(0;384)|0]')"
payload="$(jq -n \
  --arg id "$POINT_ID" \
  --argjson vec "$vector_json" \
  '{points:[{id:$id, vector:$vec, payload:{seed:"lumogis-dr-roundtrip"}}]}')"
curl -sf -X PUT "${QDRANT_URL}/collections/${COLLECTION}/points" \
  -H 'Content-Type: application/json' \
  -d "$payload" >/dev/null

if [[ "${SEED_FALKORDB:-}" == "1" ]]; then
  redis-cli -h "$FALKOR_HOST" -p "$FALKOR_PORT" GRAPH.QUERY "$GRAPH_NAME" \
    "MERGE (n:Entity {lumogis_id: '${ENTITY_ID}', user_id: 'default'}) SET n.name = '${ENTITY_NAME}' RETURN id(n)" \
    >/dev/null
  echo "[seed_backup_roundtrip_data] falkordb graph=${GRAPH_NAME} entity=${ENTITY_ID}"
fi

echo "[seed_backup_roundtrip_data] entity=${ENTITY_ID} collection=${COLLECTION} point=${POINT_ID}"
