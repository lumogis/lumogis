#!/usr/bin/env bash
# smoke_test_m2.sh — Manual self-healing smoke test for the M2 graph backfill.
#
# Scenario tested:
#   1. Stop FalkorDB.
#   2. POST /session/end with known entities — core pipeline returns 200.
#   3. Confirm sessions row exists in Postgres with entity_ids populated
#      but graph_projected_at IS NULL (projection skipped while DB was down).
#   4. Restart FalkorDB and wait for it to be healthy.
#   5. POST /graph/backfill → 202.
#   6. Wait for reconciliation to complete.
#   7. Confirm sessions.graph_projected_at IS NOT NULL.
#   8. Confirm sessions.entity_ids is non-empty (UUID path was available).
#   9. Query FalkorDB directly to confirm DISCUSSED_IN edges exist and that
#      the entity node IDs on those edges match the UUIDs from Postgres —
#      proving the UUID path was used, not the name-string fallback.
#
# Usage:
#   bash scripts/smoke_test_m2.sh
#
# Optional env vars:
#   LUMOGIS_BASE_URL      — default: http://localhost:8000
#   GRAPH_ADMIN_TOKEN     — set if GRAPH_ADMIN_TOKEN is configured on the server
#   GRAPH_NAME            — FalkorDB graph name; default: lumogis
#   BACKFILL_WAIT_SECONDS — how long to sleep after triggering backfill; default: 20
#   SESSION_WAIT_SECONDS  — how long to sleep for background session processing; default: 20
#
# Requirements:
#   - docker compose stack running (FalkorDB will be stopped/started by this script).
#   - GRAPH_BACKEND=falkordb in the running orchestrator environment.
#   - psql available, or docker compose exec postgres used as fallback.
#   - redis-cli available, or docker compose exec falkordb used as fallback.
#
# This script is for manual operational verification, not CI.

set -euo pipefail

LUMOGIS_BASE_URL="${LUMOGIS_BASE_URL:-http://localhost:8000}"
COMPOSE_FILE_ARGS="-f docker-compose.yml -f docker-compose.falkordb.yml"
GRAPH_NAME="${GRAPH_NAME:-lumogis}"
BACKFILL_WAIT_SECONDS="${BACKFILL_WAIT_SECONDS:-20}"
SESSION_WAIT_SECONDS="${SESSION_WAIT_SECONDS:-20}"
GRAPH_ADMIN_TOKEN="${GRAPH_ADMIN_TOKEN:-}"

# Fixed test session so we can look it up precisely.
TEST_SESSION_ID="sm2-test-$(date +%s)"

_header() {
    echo ""
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
    echo "  $*"
    echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
}

_ok()   { echo "  ✓ $*"; }
_fail() { echo "  ✗ $*" >&2; exit 1; }
_info() { echo "  → $*"; }

_psql() {
    local query="$1"
    if command -v psql &>/dev/null; then
        PGPASSWORD="${POSTGRES_PASSWORD:-lumogis}" \
            psql -h "${POSTGRES_HOST:-localhost}" \
                 -p "${POSTGRES_PORT:-5432}" \
                 -U "${POSTGRES_USER:-lumogis}" \
                 -d "${POSTGRES_DB:-lumogis}" \
                 -tAX -c "$query"
    else
        docker compose $COMPOSE_FILE_ARGS exec -T postgres \
            psql -U "${POSTGRES_USER:-lumogis}" -d "${POSTGRES_DB:-lumogis}" -tAX -c "$query"
    fi
}

# Run a FalkorDB Cypher query via redis-cli GRAPH.QUERY.
# Output: raw redis-cli response (arrays of strings).
_falkor_query() {
    local cypher="$1"
    if command -v redis-cli &>/dev/null; then
        redis-cli -h "${FALKORDB_HOST:-localhost}" \
                  -p "${FALKORDB_PORT:-6379}" \
                  GRAPH.QUERY "$GRAPH_NAME" "$cypher" --no-auth-warning 2>/dev/null || true
    else
        docker compose $COMPOSE_FILE_ARGS exec -T falkordb \
            redis-cli GRAPH.QUERY "$GRAPH_NAME" "$cypher" 2>/dev/null || true
    fi
}

_backfill_curl_args() {
    if [[ -n "$GRAPH_ADMIN_TOKEN" ]]; then
        echo -H "X-Graph-Admin-Token: ${GRAPH_ADMIN_TOKEN}"
    fi
}

# ─── Step 1: Stop FalkorDB ────────────────────────────────────────────────────

_header "Step 1: Stop FalkorDB"
docker compose $COMPOSE_FILE_ARGS stop falkordb
_ok "FalkorDB stopped"

# ─── Step 2: POST /session/end with known entities ───────────────────────────

_header "Step 2: POST /session/end (FalkorDB still down)"

# Two messages that will produce entity mentions.
# "Ada Lovelace" and "Alan Turing" are common enough that most NER models
# extract them. If your model extracts different names, that's fine — the
# test checks entity_ids count > 0, not specific names.
SESSION_PAYLOAD=$(cat <<JSON
{
  "session_id": "${TEST_SESSION_ID}",
  "messages": [
    {"role": "user",      "content": "Tell me about Ada Lovelace and her work with Charles Babbage."},
    {"role": "assistant", "content": "Ada Lovelace is often regarded as the first computer programmer. She worked closely with Charles Babbage on his Analytical Engine. Alan Turing later built on her ideas."}
  ]
}
JSON
)

_info "session_id = ${TEST_SESSION_ID}"
HTTP_STATUS=$(curl -s -o /tmp/sm2_session_resp.json -w "%{http_code}" \
    -X POST "${LUMOGIS_BASE_URL}/session/end" \
    -H "Content-Type: application/json" \
    -d "$SESSION_PAYLOAD")

echo "  Response: $(cat /tmp/sm2_session_resp.json)"

if [[ "$HTTP_STATUS" == "200" ]]; then
    _ok "session/end returned 200 — core pipeline unaffected by FalkorDB outage"
else
    _fail "session/end returned HTTP $HTTP_STATUS (expected 200)"
fi

# ─── Step 3: Wait for background processing, then check Postgres ─────────────

_header "Step 3: Wait ${SESSION_WAIT_SECONDS}s for background session processing"
_info "summarize_session → store_session → store_entities all run in the background"
sleep "$SESSION_WAIT_SECONDS"

# Confirm sessions row was written with entity_ids.
SESSION_EXISTS=$(_psql "SELECT COUNT(*) FROM sessions WHERE session_id = '${TEST_SESSION_ID}';")
if [[ "$SESSION_EXISTS" -eq 0 ]]; then
    _fail "sessions row not found for session_id=${TEST_SESSION_ID}. Background processing may still be running — try increasing SESSION_WAIT_SECONDS."
fi
_ok "sessions row exists in Postgres"

ENTITY_IDS_COUNT=$(_psql "SELECT array_length(entity_ids, 1) FROM sessions WHERE session_id = '${TEST_SESSION_ID}';")
GRAPH_PROJ_AT=$(_psql "SELECT graph_projected_at FROM sessions WHERE session_id = '${TEST_SESSION_ID}';")

_info "sessions.entity_ids array length: ${ENTITY_IDS_COUNT:-0}"
_info "sessions.graph_projected_at:      ${GRAPH_PROJ_AT:-(null)}"

if [[ -z "$ENTITY_IDS_COUNT" || "$ENTITY_IDS_COUNT" == "0" ]]; then
    _fail "sessions.entity_ids is empty — entity extraction either produced no entities or entity_ids were not persisted. Check orchestrator logs."
fi
_ok "entity_ids persisted: ${ENTITY_IDS_COUNT} UUID(s)"

if [[ -n "$GRAPH_PROJ_AT" ]]; then
    _fail "sessions.graph_projected_at is already set (${GRAPH_PROJ_AT}) — projection should have failed while FalkorDB was down. Was GRAPH_BACKEND=falkordb set?"
fi
_ok "graph_projected_at IS NULL — projection correctly skipped while FalkorDB was down"

# Save entity_ids for later comparison with FalkorDB edge data.
ENTITY_IDS_CSV=$(_psql "SELECT array_to_string(entity_ids, ',') FROM sessions WHERE session_id = '${TEST_SESSION_ID}';")
_info "entity_ids to verify in FalkorDB after backfill: ${ENTITY_IDS_CSV}"

# ─── Step 4: Restart FalkorDB and wait for healthy ───────────────────────────

_header "Step 4: Restart FalkorDB"
docker compose $COMPOSE_FILE_ARGS start falkordb

_info "Waiting for FalkorDB healthcheck..."
MAX_WAIT=45
ELAPSED=0
while [[ "$ELAPSED" -lt "$MAX_WAIT" ]]; do
    HEALTH=$(docker compose $COMPOSE_FILE_ARGS ps falkordb --format json 2>/dev/null \
        | python3 -c "import sys,json; rows=json.load(sys.stdin); print(rows[0].get('Health','') if rows else '')" \
        2>/dev/null || echo "")
    if [[ "$HEALTH" == "healthy" ]]; then
        _ok "FalkorDB is healthy"
        break
    fi
    sleep 3
    ELAPSED=$((ELAPSED + 3))
done
if [[ "$ELAPSED" -ge "$MAX_WAIT" ]]; then
    _info "FalkorDB healthcheck did not report healthy within ${MAX_WAIT}s — proceeding anyway"
fi

# ─── Step 5: Trigger backfill ────────────────────────────────────────────────

_header "Step 5: POST /graph/backfill"

BACKFILL_EXTRA_ARGS=()
if [[ -n "$GRAPH_ADMIN_TOKEN" ]]; then
    BACKFILL_EXTRA_ARGS+=(-H "X-Graph-Admin-Token: ${GRAPH_ADMIN_TOKEN}")
fi

HTTP_STATUS=$(curl -s -o /tmp/sm2_backfill_resp.json -w "%{http_code}" \
    -X POST "${LUMOGIS_BASE_URL}/graph/backfill" \
    "${BACKFILL_EXTRA_ARGS[@]+"${BACKFILL_EXTRA_ARGS[@]}"}")

echo "  Response: $(cat /tmp/sm2_backfill_resp.json)"

if   [[ "$HTTP_STATUS" == "202" ]]; then _ok "Backfill accepted (202)"
elif [[ "$HTTP_STATUS" == "503" ]]; then _fail "503 — check GRAPH_BACKEND=falkordb in orchestrator env"
elif [[ "$HTTP_STATUS" == "403" ]]; then _fail "403 — set GRAPH_ADMIN_TOKEN env var before running"
elif [[ "$HTTP_STATUS" == "401" ]]; then _fail "401 — set auth credentials (AUTH_ENABLED=true)"
else _fail "Unexpected HTTP $HTTP_STATUS"
fi

# ─── Step 6: Wait for reconciliation ─────────────────────────────────────────

_header "Step 6: Wait ${BACKFILL_WAIT_SECONDS}s for background reconciliation"
sleep "$BACKFILL_WAIT_SECONDS"
_ok "Done waiting"

# ─── Step 7: Confirm sessions.graph_projected_at is now set ──────────────────

_header "Step 7: Confirm sessions.graph_projected_at is stamped"
GRAPH_PROJ_AT=$(_psql "SELECT graph_projected_at FROM sessions WHERE session_id = '${TEST_SESSION_ID}';")

if [[ -n "$GRAPH_PROJ_AT" ]]; then
    _ok "sessions.graph_projected_at = ${GRAPH_PROJ_AT}"
else
    _fail "graph_projected_at is still NULL after backfill. Check: docker compose logs orchestrator | grep -E 'backfill|reconcil|session'"
fi

# ─── Step 8: Confirm entity_ids still non-empty (sanity) ─────────────────────

_header "Step 8: Confirm sessions.entity_ids was not cleared by reconciliation"
ENTITY_IDS_AFTER=$(_psql "SELECT array_length(entity_ids, 1) FROM sessions WHERE session_id = '${TEST_SESSION_ID}';")
if [[ -n "$ENTITY_IDS_AFTER" && "$ENTITY_IDS_AFTER" -gt 0 ]]; then
    _ok "sessions.entity_ids still holds ${ENTITY_IDS_AFTER} UUID(s) — column intact after reconciliation"
else
    _fail "entity_ids is empty or NULL after reconciliation — unexpected column mutation"
fi

# ─── Step 9: Verify DISCUSSED_IN edges in FalkorDB use entity UUIDs ──────────

_header "Step 9: Verify DISCUSSED_IN edges in FalkorDB"

# Query FalkorDB for all entity nodes that have a DISCUSSED_IN edge pointing
# at the Session node for our session_id. The Session node's lumogis_id is the
# session_id. Entity nodes carry lumogis_id = entity UUID.
CYPHER="MATCH (e)-[:DISCUSSED_IN]->(s:Session {lumogis_id: '${TEST_SESSION_ID}'}) RETURN e.lumogis_id"
_info "Cypher: ${CYPHER}"

FALKOR_OUTPUT=$(_falkor_query "$CYPHER")
_info "FalkorDB raw output:"
echo "$FALKOR_OUTPUT" | sed 's/^/    /'

# Extract entity UUIDs from the FalkorDB response.
# redis-cli outputs each field on its own line; lumogis_id values are UUID strings.
EDGE_ENTITY_IDS=$(echo "$FALKOR_OUTPUT" \
    | grep -E '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$' \
    || true)

if [[ -z "$EDGE_ENTITY_IDS" ]]; then
    _fail "No DISCUSSED_IN edges found in FalkorDB for session ${TEST_SESSION_ID}. Reconciliation may not have projected the session node. Check logs."
fi

EDGE_COUNT=$(echo "$EDGE_ENTITY_IDS" | wc -l | tr -d ' ')
_ok "Found ${EDGE_COUNT} DISCUSSED_IN edge(s)"

# Cross-check: every entity UUID on the graph edge must appear in
# sessions.entity_ids. If any edge carries an ID not in that column,
# it was created via name lookup (old fallback), not UUID resolution.
UNMATCHED=0
while IFS= read -r edge_eid; do
    if echo "$ENTITY_IDS_CSV" | grep -qF "$edge_eid"; then
        _ok "Entity ${edge_eid} → matches sessions.entity_ids (UUID path confirmed)"
    else
        echo "  ✗ Entity ${edge_eid} NOT found in sessions.entity_ids" >&2
        UNMATCHED=$((UNMATCHED + 1))
    fi
done <<< "$EDGE_ENTITY_IDS"

if [[ "$UNMATCHED" -gt 0 ]]; then
    _fail "${UNMATCHED} DISCUSSED_IN edge(s) reference entity IDs not in sessions.entity_ids — name-string fallback may have been used unexpectedly"
fi

_ok "All DISCUSSED_IN edges reference UUIDs from sessions.entity_ids — name-string fallback was NOT used"

# ─── Summary ─────────────────────────────────────────────────────────────────

_header "Smoke test PASSED"
echo "  Core session/end was unaffected by FalkorDB outage."
echo "  entity_ids were persisted on the sessions row (UUID path)."
echo "  graph_projected_at remained NULL until backfill ran."
echo "  After backfill: graph_projected_at stamped, DISCUSSED_IN edges"
echo "  created in FalkorDB with entity UUIDs from sessions.entity_ids."
echo "  Name-string fallback was NOT used. ✓"
echo ""
