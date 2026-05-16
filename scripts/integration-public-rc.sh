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

_free_host_port_for_rc() {
  local port=$1
  local c seen=""
  while IFS= read -r c; do
    [ -n "$c" ] || continue
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

# RC Qdrant host publish: avoid clashing with dev/test defaults (6334/6335).
_pick_free_qdrant_host_port() {
  local p
  for p in $(seq 6400 6500); do
    if ! _host_port_in_use "$p"; then
      printf '%s\n' "$p"
      return 0
    fi
  done
  return 1
}

_ensure_qdrant_host_port() {
  if [ -n "${QDRANT_HOST_PORT:-}" ]; then
    export QDRANT_HOST_PORT
    return 0
  fi
  local picked
  if ! picked="$(_pick_free_qdrant_host_port)"; then
    echo "[verify-public-rc] ERROR: no free host port in range 6400-6500 for Qdrant" >&2
    exit 1
  fi
  export QDRANT_HOST_PORT="$picked"
  echo "[verify-public-rc] Using QDRANT_HOST_PORT=${QDRANT_HOST_PORT}" >&2
}

_rc_preflight_host_ports() {
  local p
  for p in 6333 6334 8000; do
    if ! _host_port_in_use "$p"; then
      continue
    fi
    _free_host_port_for_rc "$p"
    if ! _wait_port_free "$p"; then
      echo "[integration-public-rc] ERROR: port $p still occupied after stop attempt. Free it manually and retry." >&2
      exit 1
    fi
  done
  for p in 6333 6334 8000; do
    if _host_port_in_use "$p"; then
      echo "[integration-public-rc] ERROR: port $p still occupied after stop attempt. Free it manually and retry." >&2
      exit 1
    fi
  done
}

# LUM-249: workaround for an observed race where the Qdrant container reports
# healthy on its loopback /readyz check but is not attached to the project's
# default bridge network. `depends_on: qdrant.service_healthy` is satisfied,
# the orchestrator starts, then crashes with "QdrantStore unreachable" because
# `qdrant` does not resolve via Docker DNS. Bring qdrant up first, verify the
# network attachment, and reattach if needed before launching the rest.
_ensure_qdrant_network_attached() {
  local project="${COMPOSE_PROJECT_NAME:-lumogis-test}"
  local network="${project}_default"
  local container="${project}-qdrant-1"
  if ! docker inspect "$container" >/dev/null 2>&1; then
    return 0
  fi
  local attached
  attached=$(docker inspect "$container" \
    --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' 2>/dev/null || true)
  case " $attached " in
    *" $network "*) return 0 ;;
  esac
  echo "[integration-public-rc] WARN: $container not attached to $network — reconnecting (LUM-249)" >&2
  if ! docker network connect --alias qdrant "$network" "$container" >/dev/null 2>&1; then
    echo "[integration-public-rc] ERROR: failed to attach $container to $network" >&2
    return 1
  fi
  attached=$(docker inspect "$container" \
    --format '{{range $k,$v := .NetworkSettings.Networks}}{{$k}} {{end}}' 2>/dev/null || true)
  case " $attached " in
    *" $network "*) return 0 ;;
  esac
  echo "[integration-public-rc] ERROR: $container still not attached to $network after reconnect" >&2
  return 1
}

compose() {
  if [[ "${1:-}" == "up" ]]; then
    _rc_preflight_host_ports
  fi
  (cd "$ROOT" && docker compose --env-file "$ENV_FILE" "$@")
}

cmd_up() {
  _ensure_qdrant_host_port
  (cd "$ROOT" && test -f .env || cp config/test.env.example .env)
  eval "$(python3 "$ROOT/scripts/rc_test_env_defaults.py" "$ROOT/$ENV_FILE")"
  # LUM-249: bring qdrant up first and guard its network attachment before
  # launching the rest. See _ensure_qdrant_network_attached for context.
  compose up -d qdrant
  _ensure_qdrant_network_attached
  compose up -d --wait
  _ensure_qdrant_network_attached
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
  echo "usage: $0 up | pytest | down | full-cycle | gate-start | gate-end | print-qdrant-host-port" >&2
  exit 2
}

case "${1:-}" in
  print-qdrant-host-port)
    if [ -n "${QDRANT_HOST_PORT:-}" ]; then
      printf '%s\n' "$QDRANT_HOST_PORT"
      exit 0
    fi
    _pick_free_qdrant_host_port || exit 1
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
  *)
    usage
    ;;
esac
