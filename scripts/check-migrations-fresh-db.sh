#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
#
# Apply postgres/migrations/*.sql to a disposable Postgres volume via db_migrations.py,
# then assert a known migration-era table exists. Does not touch lumogis-test / prod.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="${INTEGRATION_ENV_FILE:-config/test.env.example}"

PROJECT="lumogis-migrate-gate-$$"

cleanup() {
  COMPOSE_PROJECT_NAME="$PROJECT" docker compose -f "$ROOT/docker-compose.yml" --env-file "$ROOT/$ENV_FILE" down -v 2>/dev/null || true
}
trap cleanup EXIT

export COMPOSE_PROJECT_NAME="$PROJECT"

docker compose -f "$ROOT/docker-compose.yml" --env-file "$ROOT/$ENV_FILE" up -d postgres --wait

# Must bypass orchestrator docker-entrypoint.sh (Ollama wait); otherwise the gate
# hangs or gets SIGKILL (137) under CI/memory limits.
docker compose -f "$ROOT/docker-compose.yml" --env-file "$ROOT/$ENV_FILE" run --rm --no-deps \
  --entrypoint /usr/local/bin/python3 \
  -v "$ROOT:/project" \
  -e POSTGRES_HOST=postgres \
  orchestrator \
  /project/orchestrator/db_migrations.py

docker compose -f "$ROOT/docker-compose.yml" --env-file "$ROOT/$ENV_FILE" exec -T postgres \
  psql -U lumogis -d lumogis -v ON_ERROR_STOP=1 \
  -c "SELECT 1 FROM household_connector_credentials LIMIT 1;" >/dev/null

echo "check-migrations-fresh-db: OK"
