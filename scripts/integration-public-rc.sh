#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Lumogis
#
# Deterministic integration subset (`integration and public_rc`) against the RC compose stack
# (docker-compose.yml + docker-compose.test.yml + docker-compose.public-rc-stack.yml).
# Used by Makefile targets `test-integration` and `verify-public-rc`.

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export COMPOSE_PROFILES=
export COMPOSE_FILE=docker-compose.yml:docker-compose.test.yml:docker-compose.public-rc-stack.yml
ENV_FILE="${INTEGRATION_ENV_FILE:-config/test.env.example}"

compose() {
  (cd "$ROOT" && docker compose --env-file "$ENV_FILE" "$@")
}

cmd_up() {
  (cd "$ROOT" && test -f .env || cp config/test.env.example .env)
  compose up -d --wait
  if [[ "${COMPOSE_PROJECT_NAME:-}" == "lumogis-test" ]]; then
    bash "$ROOT/scripts/seed-public-rc-approvals-fixture.sh"
  fi
}

cmd_pytest() {
  if [[ ! -d "$ROOT/.venv" ]]; then
    python3 -m venv "$ROOT/.venv"
  fi
  # shellcheck source=/dev/null
  source "$ROOT/.venv/bin/activate"
  pip install -q -r "$ROOT/orchestrator/requirements-dev.txt"
  eval "$(python3 "$ROOT/scripts/rc_test_env_defaults.py" "$ROOT/$ENV_FILE")"
  (
    cd "$ROOT/orchestrator"
    export LUMOGIS_WEB_BASE_URL=http://127.0.0.1
    export LUMOGIS_API_URL=http://127.0.0.1:8000
    pytest -c ../tests/integration/pytest.ini \
      ../tests/integration -v --tb=short -p no:cacheprovider \
      -m 'integration and public_rc'
  )
}

cmd_down() {
  compose down --remove-orphans
}

usage() {
  echo "usage: $0 up | pytest | down | full-cycle | gate-start | gate-end" >&2
  exit 2
}

case "${1:-}" in
  up)
    cmd_up
    ;;
  pytest)
    cmd_pytest
    ;;
  down)
    cmd_down
    ;;
  full-cycle)
    cmd_up
    set +e
    cmd_pytest
    ec=$?
    set -e
    cmd_down || true
    exit "$ec"
    ;;
  gate-start)
    cmd_up
    ;;
  gate-pytest)
    cmd_pytest
    ;;
  gate-end)
    cmd_down
    ;;
  *)
    usage
    ;;
esac
