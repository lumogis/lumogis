#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
#
# Seed a known document (file_index + Qdrant chunks) for the smoke user so
# document-scoped chat can be verified end to end (LUM-503). Drives the real
# ingestion path inside the orchestrator container.
# Safety: requires COMPOSE_PROJECT_NAME=lumogis-test.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

ENV_FILE="${INTEGRATION_ENV_FILE:-config/test.env.example}"

eval "$(python3 "$ROOT/scripts/rc_test_env_defaults.py" "$ROOT/$ENV_FILE")"

if [[ "${COMPOSE_PROJECT_NAME:-}" != "lumogis-test" ]]; then
  echo "seed-document-chat-fixture: refusing (COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT_NAME:-<unset>}, expected lumogis-test)" >&2
  exit 2
fi

export COMPOSE_PROFILES=
export COMPOSE_FILE=docker-compose.yml:docker-compose.test.yml:docker-compose.public-rc-stack.yml

docker compose --env-file "$ROOT/$ENV_FILE" exec -T --workdir /project/orchestrator orchestrator \
  python -m scripts.seed_document_chat_fixture
