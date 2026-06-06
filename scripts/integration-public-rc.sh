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
# Absolute host path to the repo. Carried into stack-control (see docker-compose.public-rc-stack.yml)
# so its in-container `docker compose --force-recreate` resolves relative bind-mount sources to real
# host paths instead of non-existent /project/* dirs.
export HOST_PROJECT_DIR="$ROOT"
ENV_FILE="${INTEGRATION_ENV_FILE:-config/test.env.example}"

_host_port_in_use() {
  local port=$1
  if command -v ss >/dev/null 2>&1; then
    ss -tln 2>/dev/null | awk '{print $4}' | grep -qE "[.:]${port}$"
    return $?
  fi
  if command -v lsof >/dev/null 2>&1; then
    lsof -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1
    return $?
  fi
  return 1
}

_containers_on_host_port() {
  local want=$1
  local name hp
  while IFS= read -r name; do
    [ -n "$name" ] || continue
    hp=$(docker inspect --format '{{range $p, $conf := .NetworkSettings.Ports}}{{range $conf}}{{.HostPort}} {{end}}{{end}}' "$name" 2>/dev/null || true)
    case " $hp " in
      *" $want "*) printf '%s\n' "$name" ;;
    esac
  done < <(docker ps --format '{{.Names}}')
}

_rc_compose_project() {
  eval "$(python3 "$ROOT/scripts/rc_test_env_defaults.py" "$ROOT/$ENV_FILE")"
  echo "${COMPOSE_PROJECT_NAME:-lumogis-test}"
}

_free_host_port_for_rc() {
  local port=$1
  local c seen="" rc_project
  rc_project="$(_rc_compose_project)"
  while IFS= read -r c; do
    [ -n "$c" ] || continue
    case "$c" in
      "${rc_project}"-*)
        # Port belongs to our RC stack (e.g. orchestrator recreate); do not stop it.
        continue
        ;;
    esac
    case " $seen " in *" $c "*) continue ;; esac
    seen="$seen $c"
    echo "[integration-public-rc] INFO: stopping $c to free port $port for RC stack" >&2
    docker stop "$c" >/dev/null 2>&1 || true
  done < <(_containers_on_host_port "$port")
}

_wait_port_free() {
  local port=$1
  local i=0
  while [ "$i" -lt 20 ]; do
    if ! _host_port_in_use "$port"; then
      return 0
    fi
    sleep 0.5
    i=$((i + 1))
  done
  return 1
}

_rc_qdrant_host_port() {
  eval "$(python3 "$ROOT/scripts/rc_test_env_defaults.py" "$ROOT/$ENV_FILE")"
  echo "${QDRANT_HOST_PORT:-6335}"
}

_rc_preflight_host_ports() {
  local p qdrant_port
  qdrant_port="$(_rc_qdrant_host_port)"
  for p in 6333 6334 8000 "$qdrant_port"; do
    if ! _host_port_in_use "$p"; then
      continue
    fi
    _free_host_port_for_rc "$p"
    if ! _wait_port_free "$p"; then
      echo "[integration-public-rc] ERROR: port $p still occupied after stop attempt. Free it manually and retry." >&2
      exit 1
    fi
  done
  for p in 6333 6334 8000 "$qdrant_port"; do
    if _host_port_in_use "$p"; then
      echo "[integration-public-rc] ERROR: port $p still occupied after stop attempt. Free it manually and retry." >&2
      exit 1
    fi
  done
}

compose() {
  (cd "$ROOT" && docker compose --env-file "$ENV_FILE" "$@")
}

_recreate_orchestrator_rc() {
  compose up -d --no-deps --force-recreate orchestrator
  compose up -d --wait --no-deps orchestrator
}

# Pre-pull the embedding model into the (persistent) ollama_data volume. OLLAMA_SKIP_WAIT
# makes each orchestrator --force-recreate skip the on-boot model pull for a fast, deterministic
# restart; but the restart_e2e ingest proof needs embeddings to succeed. Pulling once here means
# every subsequent recreate finds the model already present. Best-effort: a failure only degrades
# the ingest assertions (which then fail loudly), it does not abort the gate.
_ensure_embedding_model_rc() {
  local model="${EMBEDDING_MODEL:-nomic-embed-text}"
  echo "[integration-public-rc] INFO: ensuring Ollama embedding model '${model}' is present (restart_e2e ingest needs embeddings)"
  compose exec -T ollama ollama pull "${model}" \
    || echo "[integration-public-rc] WARNING: could not pull '${model}'; restart_e2e ingest assertions may fail" >&2
}

_wait_web_smoke_ready() {
  local origin="${LUMOGIS_WEB_BASE_URL:-http://127.0.0.1}"
  local email password i
  eval "$(python3 "$ROOT/scripts/rc_test_env_defaults.py" "$ROOT/$ENV_FILE")"
  email="${LUMOGIS_WEB_SMOKE_EMAIL:-${LUMOGIS_BOOTSTRAP_ADMIN_EMAIL:-}}"
  password="${LUMOGIS_WEB_SMOKE_PASSWORD:-${LUMOGIS_BOOTSTRAP_ADMIN_PASSWORD:-}}"
  echo "[integration-public-rc] INFO: waiting for Caddy + auth at ${origin}" >&2
  for i in $(seq 1 60); do
    if curl -sf "${origin}/" 2>/dev/null | grep -q '<div id="root">'; then
      if [ -n "$email" ] && [ -n "$password" ]; then
        if E="$email" P="$password" curl -sf -o /dev/null -X POST "${origin}/api/v1/auth/login" \
          -H 'Content-Type: application/json' \
          -d "$(E="$email" P="$password" python3 -c 'import json,os; print(json.dumps({"email":os.environ["E"],"password":os.environ["P"]}))')"; then
          return 0
        fi
      elif curl -sf -o /dev/null "${origin}/health"; then
        return 0
      fi
    fi
    sleep 2
  done
  echo "[integration-public-rc] ERROR: stack not ready for Playwright at ${origin} after 120s" >&2
  return 1
}

cmd_up() {
  (cd "$ROOT" && test -f .env || cp config/test.env.example .env)
  # Create the default filesystem root on the host as the invoking user *before* compose up.
  # Otherwise the Docker daemon auto-creates the ./lumogis-data bind-mount target as root, and
  # the restart_e2e fallback test (which writes a probe file there from the host) hits EACCES.
  mkdir -p "$ROOT/lumogis-data"
  eval "$(python3 "$ROOT/scripts/rc_test_env_defaults.py" "$ROOT/$ENV_FILE")"
  _rc_preflight_host_ports
  compose up -d --wait
  if [[ "${COMPOSE_PROJECT_NAME:-}" == "lumogis-test" ]]; then
    bash "$ROOT/scripts/seed-public-rc-approvals-fixture.sh"
    bash "$ROOT/scripts/seed-public-rc-ingest-owner.sh"
    _ensure_embedding_model_rc
    _recreate_orchestrator_rc
    _wait_web_smoke_ready
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

cmd_restart_e2e_pytest() {
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
      -m 'integration and restart_e2e'
  )
}

cmd_down() {
  compose down --remove-orphans
}

cmd_print_qdrant_host_port() {
  _rc_qdrant_host_port
}

usage() {
  echo "usage: $0 up | pytest | restart-e2e-pytest | down | full-cycle | gate-start | gate-end | print-qdrant-host-port" >&2
  exit 2
}

case "${1:-}" in
  print-qdrant-host-port)
    cmd_print_qdrant_host_port
    ;;
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
  restart-e2e-pytest)
    cmd_restart_e2e_pytest
    ;;
  *)
    usage
    ;;
esac
